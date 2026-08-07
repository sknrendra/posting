from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.base import utcnow
from app.models.journal_entry import JournalEntry, JournalLine
from app.models.reconciliation import Reconciliation
from app.models.user import User
from app.services import journal_service, ledger_service
from app.services.journal_service import InvalidJournalEntryError, LineInput
from app.utils import periods as period_utils

MOVEMENTS = ("deposit", "withdrawal")


class ReconciliationError(Exception):
    pass


def list_reconcilable_accounts(db: DbSession) -> list[Account]:
    return (
        db.query(Account)
        .filter(Account.is_cash_account.is_(True), Account.is_active.is_(True))
        .order_by(Account.code)
        .all()
    )


def _signed_net(account: Account, lines: list[JournalLine]) -> Decimal:
    raw = sum((line.debit_amount - line.credit_amount for line in lines), Decimal("0"))
    return raw if account.normal_balance == "debit" else -raw


# --- Continuity -------------------------------------------------------------


def get_latest_completed(db: DbSession, account_id: int) -> Reconciliation | None:
    return (
        db.query(Reconciliation)
        .filter(Reconciliation.account_id == account_id, Reconciliation.status == "completed")
        .order_by(Reconciliation.statement_date.desc())
        .first()
    )


def get_expected_starting_balance(db: DbSession, account_id: int) -> Decimal:
    latest = get_latest_completed(db, account_id)
    return latest.statement_ending_balance if latest else Decimal("0")


def verify_reconciliation_integrity(db: DbSession, reconciliation: Reconciliation) -> Decimal:
    """Recomputes the reconciliation's ending balance purely from its own recorded
    starting balance plus the lines it locked, and returns the delta against its
    recorded statement_ending_balance (zero = self-consistent).

    This deliberately does NOT compare against ledger_service.get_account_balance —
    outstanding items routinely make the book balance differ from the statement
    balance, so that comparison would false-positive on completely normal
    reconciliations. Since journal entries are immutable in this app, a nonzero
    delta here can only mean the reconciliation's own locked-line linkage was
    altered after completion (e.g. an undo that didn't fully unlock, or direct DB
    tampering) — a genuine integrity signal.
    """
    locked_lines = (
        db.query(JournalLine).filter(JournalLine.reconciliation_id == reconciliation.id).all()
    )
    derived_ending = reconciliation.starting_balance + _signed_net(
        reconciliation.account, locked_lines
    )
    return derived_ending - reconciliation.statement_ending_balance


def get_continuity_warning(db: DbSession, account: Account) -> str | None:
    latest = get_latest_completed(db, account.id)
    if latest is None:
        return None
    delta = verify_reconciliation_integrity(db, latest)
    if delta == 0:
        return None
    return (
        f"Reconciliation #{latest.id} for {account.code} — {account.name} "
        f"(statement date {latest.statement_date}, ending balance "
        f"{latest.statement_ending_balance}) no longer balances against its locked "
        f"transactions by {delta}. A transaction in that completed period may have "
        f"been altered — review it before continuing."
    )


# --- Starting / working -------------------------------------------------------


def get_active_reconciliation(db: DbSession, account_id: int) -> Reconciliation | None:
    return (
        db.query(Reconciliation)
        .filter(Reconciliation.account_id == account_id, Reconciliation.status == "in_progress")
        .first()
    )


def get_reconciliation(db: DbSession, reconciliation_id: int) -> Reconciliation | None:
    return db.get(Reconciliation, reconciliation_id)


def start_reconciliation(
    db: DbSession,
    account_id: int,
    statement_date: date,
    statement_ending_balance: Decimal,
    user: User,
) -> tuple[Reconciliation, str | None]:
    account = db.get(Account, account_id)
    if not account or not account.is_active:
        raise ReconciliationError("Account not found or inactive")
    if get_active_reconciliation(db, account_id):
        raise ReconciliationError("An in-progress reconciliation already exists for this account")

    latest = get_latest_completed(db, account_id)
    if latest and statement_date <= latest.statement_date:
        raise ReconciliationError(
            f"Statement date must be after the last completed reconciliation "
            f"({latest.statement_date})"
        )
    duplicate = (
        db.query(Reconciliation)
        .filter(Reconciliation.account_id == account_id, Reconciliation.statement_date == statement_date)
        .first()
    )
    if duplicate:
        raise ReconciliationError("A reconciliation for this statement date already exists")

    warning = get_continuity_warning(db, account)
    reconciliation = Reconciliation(
        account_id=account_id,
        statement_date=statement_date,
        statement_ending_balance=statement_ending_balance,
        starting_balance=latest.statement_ending_balance if latest else Decimal("0"),
        status="in_progress",
        created_by_user_id=user.id,
    )
    db.add(reconciliation)
    db.commit()
    db.refresh(reconciliation)
    return reconciliation, warning


def get_clearable_lines(db: DbSession, reconciliation: Reconciliation) -> list[JournalLine]:
    lines = ledger_service.get_lines_in_range(
        db, reconciliation.account_id, date_to=reconciliation.statement_date
    )
    return [
        line for line in lines if line.reconciliation_id in (None, reconciliation.id)
    ]


def toggle_line_cleared(
    db: DbSession, reconciliation: Reconciliation, line_id: int, cleared: bool
) -> JournalLine:
    if reconciliation.status != "in_progress":
        raise ReconciliationError("Only an in-progress reconciliation can be edited")
    line = db.get(JournalLine, line_id)
    if not line or line.account_id != reconciliation.account_id:
        raise ReconciliationError("Transaction not found for this account")
    if line.journal_entry.entry_date > reconciliation.statement_date:
        raise ReconciliationError("Transaction is dated after the statement date")
    if line.reconciliation_id not in (None, reconciliation.id):
        raise ReconciliationError("Transaction is locked into a different reconciliation")
    line.cleared_status = "cleared" if cleared else "uncleared"
    db.commit()
    db.refresh(line)
    return line


def compute_difference(db: DbSession, reconciliation: Reconciliation) -> dict:
    lines = get_clearable_lines(db, reconciliation)
    cleared_lines = [line for line in lines if line.cleared_status == "cleared"]
    outstanding_lines = [line for line in lines if line.cleared_status == "uncleared"]
    cleared_total = _signed_net(reconciliation.account, cleared_lines)
    ending_balance_computed = reconciliation.starting_balance + cleared_total
    difference = ending_balance_computed - reconciliation.statement_ending_balance
    return {
        "starting_balance": reconciliation.starting_balance,
        "cleared_lines": cleared_lines,
        "outstanding_lines": outstanding_lines,
        "cleared_total": cleared_total,
        "ending_balance_computed": ending_balance_computed,
        "statement_ending_balance": reconciliation.statement_ending_balance,
        "difference": difference,
        "is_balanced": difference == 0,
    }


def add_missing_transaction(
    db: DbSession,
    reconciliation: Reconciliation,
    counter_account_id: int,
    amount: Decimal,
    movement: str,
    entry_date: date,
    memo: str,
    user: User,
):
    if reconciliation.status != "in_progress":
        raise ReconciliationError("Only an in-progress reconciliation can be edited")
    if entry_date > reconciliation.statement_date:
        raise ReconciliationError("Transaction is dated after the statement date")
    if movement not in MOVEMENTS:
        raise ReconciliationError(f"Movement must be one of {MOVEMENTS}")
    if amount <= 0:
        raise ReconciliationError("Amount must be greater than zero")
    if counter_account_id == reconciliation.account_id:
        raise ReconciliationError("Pick a different account for the other side of the entry")

    account = reconciliation.account
    account_is_debit_side = (movement == "deposit") == (account.normal_balance == "debit")
    if account_is_debit_side:
        account_debit, account_credit = amount, Decimal("0")
        counter_debit, counter_credit = Decimal("0"), amount
    else:
        account_debit, account_credit = Decimal("0"), amount
        counter_debit, counter_credit = amount, Decimal("0")

    lines = [
        LineInput(
            account_id=reconciliation.account_id,
            debit_amount=account_debit,
            credit_amount=account_credit,
            memo=memo,
        ),
        LineInput(
            account_id=counter_account_id,
            debit_amount=counter_debit,
            credit_amount=counter_credit,
            memo=memo,
        ),
    ]
    try:
        entry, _created = journal_service.post_entry(
            db,
            entry_date=entry_date,
            memo=memo,
            lines=lines,
            source="manual",
            created_by_user_id=user.id,
            commit=False,
        )
    except InvalidJournalEntryError as exc:
        db.rollback()
        raise ReconciliationError(str(exc)) from exc

    for line in entry.lines:
        if line.account_id == reconciliation.account_id:
            line.cleared_status = "cleared"
    db.commit()
    db.refresh(entry)
    return entry


# --- Completing / undoing -----------------------------------------------------


def complete_reconciliation(
    db: DbSession, reconciliation: Reconciliation, user: User
) -> Reconciliation:
    if reconciliation.status != "in_progress":
        raise ReconciliationError("Only an in-progress reconciliation can be completed")
    diff = compute_difference(db, reconciliation)
    if not diff["is_balanced"]:
        raise ReconciliationError(
            f"Reconciliation is out of balance by {diff['difference']}"
        )
    for line in diff["cleared_lines"]:
        line.reconciliation_id = reconciliation.id
    reconciliation.status = "completed"
    reconciliation.completed_at = utcnow()
    reconciliation.completed_by_user_id = user.id
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def undo_reconciliation(
    db: DbSession, reconciliation: Reconciliation, admin_user: User
) -> dict:
    if reconciliation.status != "completed":
        raise ReconciliationError("Only a completed reconciliation can be undone")
    later = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.account_id == reconciliation.account_id,
            Reconciliation.status == "completed",
            Reconciliation.statement_date > reconciliation.statement_date,
        )
        .order_by(Reconciliation.statement_date)
        .all()
    )
    locked_lines = (
        db.query(JournalLine).filter(JournalLine.reconciliation_id == reconciliation.id).all()
    )
    for line in locked_lines:
        line.reconciliation_id = None
    reconciliation.status = "undone"
    db.commit()
    db.refresh(reconciliation)
    return {"reconciliation": reconciliation, "affected_later_reconciliations": later}


def get_history(db: DbSession, account_id: int) -> list[Reconciliation]:
    return (
        db.query(Reconciliation)
        .filter(Reconciliation.account_id == account_id)
        .order_by(Reconciliation.statement_date.desc())
        .all()
    )


# --- Reports-controller compatibility ----------------------------------------
# reports_controller.reports_index gates report generation per calendar period on
# these two functions. Reconciliations are no longer calendar-anchored, so
# "reconciled for this period" is redefined below — kept as its own section since
# it exists purely to satisfy that call site's contract, not the reconciliation
# flow itself.


def get_activity_range(db: DbSession) -> tuple[date, date]:
    earliest = db.query(func.min(JournalEntry.entry_date)).scalar()
    latest = db.query(func.max(JournalEntry.entry_date)).scalar()
    today = date.today()
    earliest = earliest or today
    latest = max(latest or today, today)
    return earliest, latest


def list_periods(db: DbSession, period_type: str) -> list[tuple[date, date]]:
    """Only periods that have fully closed (period_end already in the past) are
    reconcilable — there's no statement balance to check against, and the book
    balance is still moving, for a period that hasn't ended yet."""
    earliest, latest = get_activity_range(db)
    periods = period_utils.list_periods(period_type, earliest, latest)
    today = date.today()
    return [(start, end) for start, end in periods if end < today]


def get_period_status(db: DbSession, period_type: str, period_start: date, period_end: date) -> dict:
    """period_type/period_start are accepted only to keep this call-site-compatible
    with reports_controller.py; only period_end is used.

    A calendar period counts as reconciled once every reconcilable account has a
    completed reconciliation dated on/after period_end — i.e. that account's
    activity through (at least) period_end is locked into the continuity chain.
    This is coarser than the old model: it doesn't guarantee every line strictly
    inside [period_start, period_end] was itself cleared, since outstanding items
    can legitimately roll forward into a later statement date. That's an accepted
    trade-off now that reconciliations are statement-date-anchored rather than
    calendar-period-anchored.
    """
    accounts = list_reconcilable_accounts(db)
    total = len(accounts)
    completed = 0
    for account in accounts:
        exists = (
            db.query(Reconciliation)
            .filter(
                Reconciliation.account_id == account.id,
                Reconciliation.status == "completed",
                Reconciliation.statement_date >= period_end,
            )
            .first()
        )
        if exists:
            completed += 1
    return {"completed": completed, "total": total, "is_complete": total > 0 and completed == total}
