from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.journal_entry import JournalEntry, JournalLine


class InvalidJournalEntryError(Exception):
    pass


class UnbalancedEntryError(InvalidJournalEntryError):
    pass


@dataclass
class LineInput:
    account_id: int
    debit_amount: Decimal
    credit_amount: Decimal
    memo: str | None = None


def post_entry(
    db: DbSession,
    entry_date: date,
    memo: str,
    lines: list[LineInput],
    source: str,
    created_by_user_id: int | None = None,
    created_by_api_key_id: int | None = None,
    external_reference: str | None = None,
) -> tuple[JournalEntry, bool]:
    """Returns (entry, created) — created is False when external_reference already
    existed and the prior entry was returned unchanged (webhook retry idempotency)."""
    if external_reference:
        existing = (
            db.query(JournalEntry)
            .filter(JournalEntry.external_reference == external_reference)
            .first()
        )
        if existing:
            return existing, False

    if len(lines) < 2:
        raise InvalidJournalEntryError("A journal entry needs at least 2 lines")

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    account_ids = {line.account_id for line in lines}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}

    for line in lines:
        if line.debit_amount and line.credit_amount:
            raise InvalidJournalEntryError("A line cannot have both a debit and a credit amount")
        if not line.debit_amount and not line.credit_amount:
            raise InvalidJournalEntryError("A line must have a nonzero debit or credit amount")
        account = accounts.get(line.account_id)
        if not account or not account.is_active:
            raise InvalidJournalEntryError(f"Account {line.account_id} is not active")
        total_debit += line.debit_amount
        total_credit += line.credit_amount

    if total_debit != total_credit:
        raise UnbalancedEntryError(
            f"Debits ({total_debit}) do not equal credits ({total_credit})"
        )

    entry = JournalEntry(
        entry_date=entry_date,
        memo=memo,
        source=source,
        created_by_user_id=created_by_user_id,
        created_by_api_key_id=created_by_api_key_id,
        external_reference=external_reference,
    )
    for line in lines:
        entry.lines.append(
            JournalLine(
                account_id=line.account_id,
                debit_amount=line.debit_amount,
                credit_amount=line.credit_amount,
                memo=line.memo,
            )
        )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry, True


def list_entries(db: DbSession, limit: int = 200) -> list[JournalEntry]:
    return (
        db.query(JournalEntry)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
        .limit(limit)
        .all()
    )


def get_entry(db: DbSession, entry_id: int) -> JournalEntry | None:
    return db.get(JournalEntry, entry_id)
