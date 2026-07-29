from datetime import date
from decimal import Decimal as D

import pytest

from app.services import account_service, invoice_service
from app.services.invoice_service import InvoiceHeaderInput, InvoiceLineInput
from app.services.journal_service import InvalidJournalEntryError


def _make_draft(db, invoice_date, customer_name="Acme Corp", rate=D(100000)):
    header = InvoiceHeaderInput(
        invoice_date=invoice_date,
        customer_name=customer_name,
        lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=rate)],
    )
    return invoice_service.create_draft(db, header, user_id=None)


def test_sequence_assigned_gapless_and_starts_at_one(db):
    d = date(2026, 7, 29)
    assert invoice_service._assign_next_sequence(db, d) == 1
    assert invoice_service._assign_next_sequence(db, d) == 2
    assert invoice_service._assign_next_sequence(db, d) == 3
    db.rollback()


def test_sequence_resets_per_day(db):
    d1, d2 = date(2026, 7, 29), date(2026, 7, 30)
    assert invoice_service._assign_next_sequence(db, d1) == 1
    assert invoice_service._assign_next_sequence(db, d1) == 2
    assert invoice_service._assign_next_sequence(db, d2) == 1
    db.rollback()


def test_invoice_number_format():
    assert invoice_service._format_invoice_number("INV", date(2026, 7, 29), 1) == "INV-20260729-0001"
    assert invoice_service._format_invoice_number("INV", date(2026, 7, 29), 10000) == "INV-20260729-10000"


def test_posting_assigns_number_and_journal_entry(db):
    invoice = _make_draft(db, date(2026, 7, 29))
    posted = invoice_service.post_invoice(db, invoice, user_id=None)
    assert posted.status == "posted"
    assert posted.invoice_number == "INV-20260729-0001"
    assert posted.journal_entry_id is not None


def test_abandoned_draft_consumes_zero_numbers(db):
    invoice = _make_draft(db, date(2026, 7, 29))
    invoice_service.delete_draft(db, invoice)
    # Posting a fresh invoice on the same date should still get sequence 1 —
    # the deleted draft never touched the counter.
    other = _make_draft(db, date(2026, 7, 29))
    posted = invoice_service.post_invoice(db, other, user_id=None)
    assert posted.invoice_number == "INV-20260729-0001"


def test_failed_post_rolls_back_consumed_number(db):
    # Deactivating the mapped AR account makes journal_service.post_entry fail
    # *after* the invoice number has already been assigned within the same
    # uncommitted transaction — this exercises the rollback path.
    from app.services import invoice_settings_service

    settings = invoice_settings_service.get_settings(db)
    ar_account = account_service.get_account(db, settings.ar_account_id)
    account_service.set_active(db, ar_account, False)
    try:
        invoice = _make_draft(db, date(2026, 7, 29))
        with pytest.raises(InvalidJournalEntryError):
            invoice_service.post_invoice(db, invoice, user_id=None)
        db.refresh(invoice)
        assert invoice.status == "draft"
        assert invoice.invoice_number is None
    finally:
        account_service.set_active(db, ar_account, True)

    # The failed attempt's consumed number must have rolled back — a
    # subsequent successful post on the same date still gets sequence 1.
    other = _make_draft(db, date(2026, 7, 29))
    posted = invoice_service.post_invoice(db, other, user_id=None)
    assert posted.invoice_number == "INV-20260729-0001"
