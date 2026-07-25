from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.base import utcnow
from app.models.journal_entry import JournalEntry
from app.models.reconciliation import Reconciliation
from app.models.user import User
from app.services import ledger_service
from app.utils import periods as period_utils


class ReconciliationError(Exception):
    pass


def get_cash_accounts(db: DbSession) -> list[Account]:
    return (
        db.query(Account)
        .filter(Account.is_cash_account.is_(True), Account.is_active.is_(True))
        .order_by(Account.code)
        .all()
    )


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


def _ensure_period_closed(period_end: date) -> None:
    if period_end >= date.today():
        raise ReconciliationError("This period hasn't closed yet and cannot be reconciled")


def _find(db: DbSession, period_type: str, period_start: date, period_end: date, account_id: int):
    return (
        db.query(Reconciliation)
        .filter(
            Reconciliation.period_type == period_type,
            Reconciliation.period_start == period_start,
            Reconciliation.period_end == period_end,
            Reconciliation.account_id == account_id,
        )
        .first()
    )


def get_reconciliation_rows(
    db: DbSession, period_type: str, period_start: date, period_end: date
) -> list[dict]:
    accounts = get_cash_accounts(db)
    existing = {
        r.account_id: r
        for r in db.query(Reconciliation)
        .filter(
            Reconciliation.period_type == period_type,
            Reconciliation.period_start == period_start,
            Reconciliation.period_end == period_end,
        )
        .all()
    }
    rows = []
    for account in accounts:
        recon = existing.get(account.id)
        book_balance = (
            recon.book_balance
            if recon and recon.book_balance is not None
            else ledger_service.get_account_balance(db, account, as_of_date=period_end)
        )
        rows.append(
            {
                "account": account,
                "reconciliation": recon,
                "book_balance": book_balance,
                "status": recon.status if recon else "open",
            }
        )
    return rows


def submit_statement_balance(
    db: DbSession,
    period_type: str,
    period_start: date,
    period_end: date,
    account_id: int,
    statement_balance,
    user: User,
) -> Reconciliation:
    _ensure_period_closed(period_end)
    recon = _find(db, period_type, period_start, period_end, account_id)
    if recon and recon.status == "completed":
        raise ReconciliationError("This account is already reconciled for this period")
    if not recon:
        recon = Reconciliation(
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            account_id=account_id,
            statement_balance=statement_balance,
            status="open",
            submitted_by_user_id=user.id,
        )
        db.add(recon)
    else:
        recon.statement_balance = statement_balance
        recon.submitted_by_user_id = user.id
    db.commit()
    return recon


def confirm_reconciliation(
    db: DbSession, period_type: str, period_start: date, period_end: date, account_id: int, user: User
) -> Reconciliation:
    _ensure_period_closed(period_end)
    recon = _find(db, period_type, period_start, period_end, account_id)
    if not recon:
        raise ReconciliationError("Enter a statement balance before confirming")
    if recon.status == "completed":
        return recon
    account = db.get(Account, account_id)
    recon.book_balance = ledger_service.get_account_balance(db, account, as_of_date=period_end)
    recon.status = "completed"
    recon.reconciled_by_user_id = user.id
    recon.reconciled_at = utcnow()
    db.commit()
    return recon


def get_period_status(db: DbSession, period_type: str, period_start: date, period_end: date) -> dict:
    total = len(get_cash_accounts(db))
    completed = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.period_type == period_type,
            Reconciliation.period_start == period_start,
            Reconciliation.period_end == period_end,
            Reconciliation.status == "completed",
        )
        .count()
    )
    return {"completed": completed, "total": total, "is_complete": total > 0 and completed == total}
