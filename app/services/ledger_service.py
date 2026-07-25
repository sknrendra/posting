from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.journal_entry import JournalEntry, JournalLine


def _lines_query(
    db: DbSession,
    account_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    query = (
        db.query(JournalLine)
        .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
        .filter(JournalLine.account_id == account_id)
    )
    if date_from is not None:
        query = query.filter(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        query = query.filter(JournalEntry.entry_date <= date_to)
    return query.order_by(JournalEntry.entry_date, JournalLine.id)


def get_lines_in_range(
    db: DbSession, account_id: int, date_from: date | None = None, date_to: date | None = None
) -> list[JournalLine]:
    return _lines_query(db, account_id, date_from=date_from, date_to=date_to).all()


def get_account_balance(db: DbSession, account: Account, as_of_date: date | None = None) -> Decimal:
    """Net balance through as_of_date (inclusive), sign-adjusted to the account's normal balance.

    Aggregated in Python (not SQL SUM) so money never round-trips through SQLite's
    numeric affinity / floating point handling."""
    lines = _lines_query(db, account.id, date_to=as_of_date).all()
    raw = sum((line.debit_amount - line.credit_amount for line in lines), Decimal("0"))
    return raw if account.normal_balance == "debit" else -raw


def get_balances_as_of(
    db: DbSession, accounts: list[Account], as_of_date: date | None = None
) -> dict[int, Decimal]:
    return {account.id: get_account_balance(db, account, as_of_date) for account in accounts}


def get_period_net_activity(
    db: DbSession, account: Account, date_from: date, date_to: date
) -> Decimal:
    """Net movement within [date_from, date_to] only (not cumulative), sign-adjusted
    to the account's normal balance. Used for period-restricted statements like P&L."""
    lines = _lines_query(db, account.id, date_from=date_from, date_to=date_to).all()
    raw = sum((line.debit_amount - line.credit_amount for line in lines), Decimal("0"))
    return raw if account.normal_balance == "debit" else -raw


def get_running_ledger(
    db: DbSession, account: Account, date_from: date | None = None, date_to: date | None = None
) -> dict:
    opening_balance = Decimal("0")
    if date_from is not None:
        opening_balance = get_account_balance(db, account, as_of_date=date_from - timedelta(days=1))

    sign = 1 if account.normal_balance == "debit" else -1
    running = opening_balance
    rows = []
    for line in _lines_query(db, account.id, date_from=date_from, date_to=date_to).all():
        running += sign * (line.debit_amount - line.credit_amount)
        rows.append({"line": line, "balance": running})

    return {"opening_balance": opening_balance, "rows": rows, "closing_balance": running}
