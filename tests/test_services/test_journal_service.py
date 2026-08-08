import itertools
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import account_service, journal_service
from app.services.journal_service import (
    InvalidJournalEntryError,
    LineInput,
    UnbalancedEntryError,
)

ENTRY_DATE = date(2026, 7, 29)

# Accounts aren't cleaned between tests (see conftest._clean_slate), so every
# test needs its own unique codes rather than sharing fixed ones.
_seq = itertools.count()


def _accounts(db):
    n = next(_seq)
    cash = account_service.create_account(db, f"TJ-CASH-{n}", "Test Cash", "asset", is_cash_account=True)
    revenue = account_service.create_account(db, f"TJ-REV-{n}", "Test Revenue", "revenue")
    return cash, revenue


def _balanced_lines(cash_id, revenue_id, amount=D("1000")):
    return [
        LineInput(account_id=cash_id, debit_amount=amount, credit_amount=D(0)),
        LineInput(account_id=revenue_id, debit_amount=D(0), credit_amount=amount),
    ]


# --- post_entry: positive -------------------------------------------------


def test_post_entry_two_balanced_lines_creates_entry(db):
    cash, revenue = _accounts(db)
    entry, created = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Sale",
        lines=_balanced_lines(cash.id, revenue.id),
        source="manual",
    )
    assert created is True
    assert entry.id is not None
    assert {(l.account_id, l.debit_amount, l.credit_amount) for l in entry.lines} == {
        (cash.id, D("1000"), D(0)),
        (revenue.id, D(0), D("1000")),
    }


def test_post_entry_more_than_two_lines_balanced(db):
    cash, revenue = _accounts(db)
    fees = account_service.create_account(db, f"TJ-FEES-{next(_seq)}", "Test Fees", "expense")
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("900"), credit_amount=D(0)),
        LineInput(account_id=fees.id, debit_amount=D("100"), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("1000")),
    ]
    entry, created = journal_service.post_entry(
        db, entry_date=ENTRY_DATE, memo="Sale with fee", lines=lines, source="manual"
    )
    assert created is True
    assert len(entry.lines) == 3


def test_post_entry_commit_false_flushes_but_does_not_commit(db):
    cash, revenue = _accounts(db)
    entry, created = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Uncommitted",
        lines=_balanced_lines(cash.id, revenue.id),
        source="invoice",
        commit=False,
    )
    assert created is True
    assert entry.id is not None
    db.rollback()
    assert journal_service.get_entry(db, entry.id) is None


def test_post_entry_new_external_reference_creates(db):
    cash, revenue = _accounts(db)
    entry, created = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Webhook",
        lines=_balanced_lines(cash.id, revenue.id),
        source="webhook",
        external_reference="ref-1",
    )
    assert created is True
    assert entry.external_reference == "ref-1"


def test_post_entry_no_external_reference_never_dedupes(db):
    cash, revenue = _accounts(db)
    kwargs = dict(
        entry_date=ENTRY_DATE,
        memo="Manual",
        lines=_balanced_lines(cash.id, revenue.id),
        source="manual",
    )
    entry1, created1 = journal_service.post_entry(db, **kwargs)
    entry2, created2 = journal_service.post_entry(db, **kwargs)
    assert created1 is True
    assert created2 is True
    assert entry1.id != entry2.id


def test_post_entry_repeat_external_reference_returns_existing_unchanged(db):
    cash, revenue = _accounts(db)
    entry1, created1 = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="First",
        lines=_balanced_lines(cash.id, revenue.id),
        source="webhook",
        external_reference="dup-ref",
    )
    assert created1 is True

    # Garbage lines — the idempotency short-circuit must return before line validation.
    entry2, created2 = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Second attempt, ignored",
        lines=[],
        source="webhook",
        external_reference="dup-ref",
    )
    assert created2 is False
    assert entry2.id == entry1.id
    assert entry2.memo == "First"


@pytest.mark.parametrize("source", ["manual", "webhook", "invoice"])
def test_post_entry_accepts_all_valid_sources(db, source):
    cash, revenue = _accounts(db)
    entry, _created = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Source check",
        lines=_balanced_lines(cash.id, revenue.id),
        source=source,
    )
    assert entry.source == source


def test_post_entry_reverses_entry_id_persisted(db):
    cash, revenue = _accounts(db)
    original, _ = journal_service.post_entry(
        db, entry_date=ENTRY_DATE, memo="Original", lines=_balanced_lines(cash.id, revenue.id), source="manual"
    )
    reversal, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Reversal",
        lines=_balanced_lines(revenue.id, cash.id),
        source="manual",
        reverses_entry_id=original.id,
    )
    assert reversal.reverses_entry_id == original.id


def test_post_entry_invoice_id_persisted_and_lookupable(db):
    from datetime import date as date_cls

    from app.services import invoice_service
    from app.services.invoice_service import InvoiceHeaderInput, InvoiceLineInput

    cash, revenue = _accounts(db)
    entry_no_invoice, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Invoice-linked",
        lines=_balanced_lines(cash.id, revenue.id),
        source="invoice",
        invoice_id=None,
    )
    assert entry_no_invoice.invoice_id is None

    invoice = invoice_service.create_draft(
        db,
        InvoiceHeaderInput(
            invoice_date=date_cls(2026, 7, 29),
            customer_name="Acme",
            lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=D(1000))],
        ),
        user_id=None,
    )
    entry2, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Invoice-linked-2",
        lines=_balanced_lines(cash.id, revenue.id),
        source="invoice",
        invoice_id=invoice.id,
    )
    assert entry2.invoice_id == invoice.id
    # get_invoice_by_journal_entry_id resolves via Invoice.journal_entry_id/void_journal_entry_id
    # (the reverse FK), not via JournalEntry.invoice_id — that direction is exercised
    # end-to-end by invoice_service's own posting tests.


def test_post_entry_debit_only_line_accepted(db):
    cash, revenue = _accounts(db)
    entry, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Debit only line",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D("50"), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("50")),
        ],
        source="manual",
    )
    line = next(l for l in entry.lines if l.account_id == cash.id)
    assert line.debit_amount == D("50")
    assert line.credit_amount == D(0)


def test_post_entry_system_entry_with_no_creator(db):
    cash, revenue = _accounts(db)
    entry, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="System",
        lines=_balanced_lines(cash.id, revenue.id),
        source="manual",
        created_by_user_id=None,
        created_by_api_key_id=None,
    )
    assert entry.created_by_user_id is None
    assert entry.created_by_api_key_id is None


# --- post_entry: negative --------------------------------------------------


def test_post_entry_empty_lines_rejected(db):
    with pytest.raises(InvalidJournalEntryError, match="at least 2 lines"):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=[], source="manual")


def test_post_entry_single_line_rejected(db):
    cash, _ = _accounts(db)
    with pytest.raises(InvalidJournalEntryError, match="at least 2 lines"):
        journal_service.post_entry(
            db,
            entry_date=ENTRY_DATE,
            memo="x",
            lines=[LineInput(account_id=cash.id, debit_amount=D("10"), credit_amount=D(0))],
            source="manual",
        )


def test_post_entry_line_with_both_debit_and_credit_rejected(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("10"), credit_amount=D("10")),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("10")),
    ]
    with pytest.raises(InvalidJournalEntryError, match="cannot have both"):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_line_with_neither_debit_nor_credit_rejected(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D(0), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("10")),
    ]
    with pytest.raises(InvalidJournalEntryError, match="nonzero debit or credit"):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_nonexistent_account_rejected(db):
    cash, _ = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("10"), credit_amount=D(0)),
        LineInput(account_id=999999, debit_amount=D(0), credit_amount=D("10")),
    ]
    with pytest.raises(InvalidJournalEntryError, match="not active"):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_inactive_account_rejected(db):
    cash, revenue = _accounts(db)
    account_service.set_active(db, revenue, False)
    lines = _balanced_lines(cash.id, revenue.id)
    with pytest.raises(InvalidJournalEntryError, match="not active"):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_unbalanced_rejected_and_nothing_persisted(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("100"), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("90")),
    ]
    with pytest.raises(UnbalancedEntryError):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")
    assert journal_service.list_entries(db) == []


def test_post_entry_unbalanced_rejected_is_subclass_of_invalid(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("100"), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("90")),
    ]
    with pytest.raises(InvalidJournalEntryError):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_one_cent_imbalance_rejected(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("100.00"), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("99.99")),
    ]
    with pytest.raises(UnbalancedEntryError):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")


def test_post_entry_negative_amount_rejected_by_db_constraint(db):
    cash, revenue = _accounts(db)
    lines = [
        LineInput(account_id=cash.id, debit_amount=D("-10"), credit_amount=D(0)),
        LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("-10")),
    ]
    with pytest.raises(IntegrityError):
        journal_service.post_entry(db, entry_date=ENTRY_DATE, memo="x", lines=lines, source="manual")
    db.rollback()


def test_post_entry_duplicate_external_reference_at_db_level_raises_integrity_error(db):
    """Documents that the app-level dedupe check (query-then-insert) is not
    atomic — the DB's UniqueConstraint is the real backstop for a race."""
    cash, revenue = _accounts(db)
    journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="First",
        lines=_balanced_lines(cash.id, revenue.id),
        source="webhook",
        external_reference="race-ref",
    )
    from app.models.journal_entry import JournalEntry

    dupe = JournalEntry(
        entry_date=ENTRY_DATE,
        memo="Racing insert",
        source="webhook",
        external_reference="race-ref",
    )
    db.add(dupe)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- list_entries -----------------------------------------------------------


def test_list_entries_ordered_desc_by_date_then_id(db):
    cash, revenue = _accounts(db)
    early, _ = journal_service.post_entry(
        db, entry_date=date(2026, 1, 1), memo="Early", lines=_balanced_lines(cash.id, revenue.id), source="manual"
    )
    late, _ = journal_service.post_entry(
        db, entry_date=date(2026, 6, 1), memo="Late", lines=_balanced_lines(cash.id, revenue.id), source="manual"
    )
    entries = journal_service.list_entries(db)
    ids = [e.id for e in entries]
    assert ids.index(late.id) < ids.index(early.id)


def test_list_entries_respects_limit(db):
    cash, revenue = _accounts(db)
    for _ in range(3):
        journal_service.post_entry(
            db, entry_date=ENTRY_DATE, memo="x", lines=_balanced_lines(cash.id, revenue.id), source="manual"
        )
    assert len(journal_service.list_entries(db, limit=2)) == 2


def test_list_entries_empty_returns_empty_list(db):
    assert journal_service.list_entries(db) == []


# --- get_entry ---------------------------------------------------------------


def test_get_entry_existing(db):
    cash, revenue = _accounts(db)
    entry, _ = journal_service.post_entry(
        db, entry_date=ENTRY_DATE, memo="x", lines=_balanced_lines(cash.id, revenue.id), source="manual"
    )
    fetched = journal_service.get_entry(db, entry.id)
    assert fetched is not None
    assert len(fetched.lines) == 2


def test_get_entry_nonexistent_returns_none(db):
    assert journal_service.get_entry(db, 999999) is None


# --- cross-cutting -------------------------------------------------------


def test_round_trip_preserves_line_order_and_decimal_precision(db):
    cash, revenue = _accounts(db)
    entry, _ = journal_service.post_entry(
        db,
        entry_date=ENTRY_DATE,
        memo="Precision check",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D("100.10"), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D("100.10")),
        ],
        source="manual",
    )
    fetched = journal_service.get_entry(db, entry.id)
    assert [l.id for l in fetched.lines] == sorted(l.id for l in fetched.lines)
    cash_line = next(l for l in fetched.lines if l.account_id == cash.id)
    assert cash_line.debit_amount == D("100.10")
    assert str(cash_line.debit_amount) == "100.10"
