from datetime import date
from decimal import Decimal as D

import pytest

from app.services import account_service, invoice_service, invoice_settings_service, journal_service
from app.services.invoice_service import (
    InvoiceHeaderInput,
    InvoiceLineInput,
    InvoiceNotDraftError,
    InvoiceNotPostedError,
    InvoiceValidationError,
    MissingAccountMappingError,
    post_invoice,
    void_invoice,
)


def _header(customer_name="Acme Corp", **overrides):
    defaults = dict(
        invoice_date=date(2026, 7, 29),
        customer_name=customer_name,
        lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=D(1000000))],
    )
    defaults.update(overrides)
    return InvoiceHeaderInput(**defaults)


def test_create_draft_never_assigns_number(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    assert invoice.status == "draft"
    assert invoice.invoice_number is None
    assert invoice.journal_entry_id is None


def test_draft_can_post_with_blank_customer_address_and_contact(db):
    header = _header(customer_name="Jane Doe", customer_address=None, customer_contact=None)
    invoice = invoice_service.create_draft(db, header, user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    assert posted.status == "posted"
    assert posted.customer_name == "Jane Doe"


def test_draft_allows_blank_customer_name(db):
    header = _header(customer_name=None)
    invoice = invoice_service.create_draft(db, header, user_id=None)
    assert invoice.status == "draft"


def test_posting_requires_customer_name(db):
    header = _header(customer_name=None)
    invoice = invoice_service.create_draft(db, header, user_id=None)
    with pytest.raises(InvoiceValidationError):
        post_invoice(db, invoice, user_id=None)


def test_editing_posted_invoice_fails(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    with pytest.raises(InvoiceNotDraftError):
        invoice_service.update_draft(db, posted, _header())


def test_deleting_posted_invoice_fails(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    with pytest.raises(InvoiceNotDraftError):
        invoice_service.delete_draft(db, posted)


def test_posting_already_posted_invoice_fails(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    with pytest.raises(InvoiceNotDraftError):
        post_invoice(db, posted, user_id=None)


def test_voiding_a_draft_fails(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    with pytest.raises(InvoiceNotPostedError):
        void_invoice(db, invoice, "changed my mind", user_id=None)


def test_void_requires_a_reason(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    with pytest.raises(InvoiceValidationError):
        void_invoice(db, posted, "   ", user_id=None)


def test_missing_account_mapping_blocks_posting(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.ar_account_id
    settings.ar_account_id = None
    db.commit()
    try:
        invoice = invoice_service.create_draft(db, _header(), user_id=None)
        with pytest.raises(MissingAccountMappingError):
            post_invoice(db, invoice, user_id=None)
    finally:
        settings.ar_account_id = original
        db.commit()


def test_net_method_journal_entry_is_balanced(db):
    header = _header(tax_rate=D(11), deposit_applied=D(0))
    invoice = invoice_service.create_draft(db, header, user_id=None)
    posted = post_invoice(db, invoice, user_id=None)

    entry = journal_service.get_entry(db, posted.journal_entry_id)
    total_debit = sum((l.debit_amount for l in entry.lines), D(0))
    total_credit = sum((l.credit_amount for l in entry.lines), D(0))
    assert total_debit == total_credit
    assert total_debit == posted.balance_due

    settings = invoice_settings_service.get_settings(db)
    revenue_lines = [l for l in entry.lines if l.account_id == settings.service_revenue_account_id]
    assert revenue_lines[0].credit_amount == posted.subtotal_net


def test_gross_method_used_when_sales_discount_account_configured_and_discount_present(db):
    settings = invoice_settings_service.get_settings(db)
    discount_account = account_service.create_account(
        db, "4900", "Sales Discounts", "revenue", is_cash_account=False
    )
    settings.sales_discounts_account_id = discount_account.id
    db.commit()
    try:
        header = _header(
            lines=[
                InvoiceLineInput(
                    description="Consulting",
                    quantity=D(1),
                    rate=D(1000000),
                    discount_mode="amount",
                    discount_amount=D(100000),
                )
            ]
        )
        invoice = invoice_service.create_draft(db, header, user_id=None)
        posted = post_invoice(db, invoice, user_id=None)
        entry = journal_service.get_entry(db, posted.journal_entry_id)

        discount_lines = [l for l in entry.lines if l.account_id == discount_account.id]
        assert discount_lines and discount_lines[0].debit_amount == D(100000)
        revenue_lines = [l for l in entry.lines if l.account_id == settings.service_revenue_account_id]
        assert revenue_lines[0].credit_amount == posted.subtotal_gross

        total_debit = sum((l.debit_amount for l in entry.lines), D(0))
        total_credit = sum((l.credit_amount for l in entry.lines), D(0))
        assert total_debit == total_credit
    finally:
        settings.sales_discounts_account_id = None
        db.commit()


def test_deposit_debits_unearned_revenue_not_revenue(db):
    header = _header(deposit_applied=D(200000))
    invoice = invoice_service.create_draft(db, header, user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    entry = journal_service.get_entry(db, posted.journal_entry_id)

    settings = invoice_settings_service.get_settings(db)
    unearned_lines = [l for l in entry.lines if l.account_id == settings.unearned_revenue_account_id]
    assert unearned_lines and unearned_lines[0].debit_amount == D(200000)


def test_void_creates_reversing_entry_and_preserves_original(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    original_entry_id = posted.journal_entry_id
    original_lines_before = {
        (l.account_id, l.debit_amount, l.credit_amount)
        for l in journal_service.get_entry(db, original_entry_id).lines
    }

    voided = void_invoice(db, posted, "customer requested cancellation", user_id=None)

    assert voided.status == "void"
    assert voided.invoice_number == posted.invoice_number  # number retained
    assert voided.void_journal_entry_id is not None
    assert voided.void_journal_entry_id != original_entry_id

    original_entry_after = journal_service.get_entry(db, original_entry_id)
    original_lines_after = {
        (l.account_id, l.debit_amount, l.credit_amount) for l in original_entry_after.lines
    }
    assert original_lines_after == original_lines_before  # untouched

    reversal_entry = journal_service.get_entry(db, voided.void_journal_entry_id)
    reversal_lines = {(l.account_id, l.debit_amount, l.credit_amount) for l in reversal_entry.lines}
    swapped_original = {(acc, credit, debit) for acc, debit, credit in original_lines_before}
    assert reversal_lines == swapped_original


def test_cannot_void_an_already_void_invoice(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    voided = void_invoice(db, posted, "reason", user_id=None)
    with pytest.raises(InvoiceNotPostedError):
        void_invoice(db, voided, "reason again", user_id=None)


def test_flat_fee_line_keeps_quantity_one_in_data(db):
    header = _header(
        lines=[
            InvoiceLineInput(
                description="Flat project fee", quantity=D(1), rate=D(5000000), hide_qty_rate=True
            )
        ]
    )
    invoice = invoice_service.create_draft(db, header, user_id=None)
    line = invoice.lines[0]
    assert line.hide_qty_rate is True
    assert line.quantity == D(1)
    assert line.line_amount == D(5000000)
