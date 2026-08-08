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


# --- gaps -------------------------------------------------------------------


def test_update_draft_replaces_all_lines(db):
    header = _header(
        lines=[InvoiceLineInput(description="Old line", quantity=D(1), rate=D(100))]
    )
    invoice = invoice_service.create_draft(db, header, user_id=None)

    new_header = _header(
        lines=[
            InvoiceLineInput(description="New line 1", quantity=D(2), rate=D(50)),
            InvoiceLineInput(description="New line 2", quantity=D(1), rate=D(25)),
        ]
    )
    updated = invoice_service.update_draft(db, invoice, new_header)
    assert len(updated.lines) == 2
    assert {l.line_number for l in updated.lines} == {1, 2}
    descriptions = [l.description for l in sorted(updated.lines, key=lambda l: l.line_number)]
    assert descriptions == ["New line 1", "New line 2"]
    assert "Old line" not in descriptions


def test_deposit_exceeding_total_rejected_on_create(db):
    header = _header(
        deposit_applied=D(9999999),
        lines=[InvoiceLineInput(description="Small", quantity=D(1), rate=D(100))],
    )
    with pytest.raises(InvoiceValidationError, match="Deposit applied cannot exceed"):
        invoice_service.create_draft(db, header, user_id=None)


def test_deposit_exceeding_total_rejected_on_update(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    bad_header = _header(
        deposit_applied=D(9999999),
        lines=[InvoiceLineInput(description="Small", quantity=D(1), rate=D(100))],
    )
    with pytest.raises(InvoiceValidationError, match="Deposit applied cannot exceed"):
        invoice_service.update_draft(db, invoice, bad_header)
    # update_draft mutates the persistent `invoice` object in-place before
    # validating; the failed validation leaves it dirty (unflushed
    # deposit_applied + an orphan new line) — roll back so it doesn't trip up
    # a later flush (e.g. the shared teardown's cleanup).
    db.rollback()


def test_list_invoices_filters_by_status(db):
    draft = invoice_service.create_draft(db, _header(customer_name="Draft Co"), user_id=None)
    posted = invoice_service.create_draft(db, _header(customer_name="Posted Co"), user_id=None)
    post_invoice(db, posted, user_id=None)

    drafts = invoice_service.list_invoices(db, status="draft")
    assert {i.id for i in drafts} == {draft.id}
    posted_list = invoice_service.list_invoices(db, status="posted")
    assert {i.id for i in posted_list} == {posted.id}


def test_list_invoices_search_matches_invoice_number(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    results = invoice_service.list_invoices(db, search=posted.invoice_number[:6].lower())
    assert {i.id for i in results} == {posted.id}


def test_list_invoices_search_matches_customer_name_case_insensitive(db):
    invoice_service.create_draft(db, _header(customer_name="Very Unique Customer"), user_id=None)
    results = invoice_service.list_invoices(db, search="unique customer")
    assert len(results) == 1
    assert results[0].customer_name == "Very Unique Customer"


def test_list_invoices_search_no_match_empty(db):
    invoice_service.create_draft(db, _header(), user_id=None)
    assert invoice_service.list_invoices(db, search="totally-nonexistent-xyz") == []


def test_list_invoices_no_filters_ordered_desc(db):
    from datetime import date as date_cls

    early = invoice_service.create_draft(db, _header(customer_name="Early"), user_id=None)
    late_header = _header(customer_name="Late")
    late_header.invoice_date = date_cls(2026, 8, 1)
    late = invoice_service.create_draft(db, late_header, user_id=None)
    ids = [i.id for i in invoice_service.list_invoices(db)]
    assert ids.index(late.id) < ids.index(early.id)


def test_post_invoice_missing_tax_payable_mapping(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.tax_payable_account_id
    settings.tax_payable_account_id = None
    db.commit()
    try:
        header = _header(tax_rate=D(11))
        invoice = invoice_service.create_draft(db, header, user_id=None)
        with pytest.raises(MissingAccountMappingError, match="Tax Payable"):
            post_invoice(db, invoice, user_id=None)
    finally:
        settings.tax_payable_account_id = original
        db.commit()


def test_post_invoice_missing_unearned_revenue_mapping(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.unearned_revenue_account_id
    settings.unearned_revenue_account_id = None
    db.commit()
    try:
        header = _header(deposit_applied=D(100000))
        invoice = invoice_service.create_draft(db, header, user_id=None)
        with pytest.raises(MissingAccountMappingError, match="Unearned Revenue"):
            post_invoice(db, invoice, user_id=None)
    finally:
        settings.unearned_revenue_account_id = original
        db.commit()


def test_post_invoice_missing_service_revenue_mapping(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.service_revenue_account_id
    settings.service_revenue_account_id = None
    db.commit()
    try:
        invoice = invoice_service.create_draft(db, _header(), user_id=None)
        with pytest.raises(MissingAccountMappingError, match="Service Revenue"):
            post_invoice(db, invoice, user_id=None)
    finally:
        settings.service_revenue_account_id = original
        db.commit()


def test_post_invoice_net_method_when_discount_account_configured_but_no_discount(db):
    settings = invoice_settings_service.get_settings(db)
    discount_account = account_service.create_account(
        db, "4901", "Sales Discounts 2", "revenue", is_cash_account=False
    )
    settings.sales_discounts_account_id = discount_account.id
    db.commit()
    try:
        header = _header()  # no discount on the line
        invoice = invoice_service.create_draft(db, header, user_id=None)
        posted = post_invoice(db, invoice, user_id=None)
        entry = journal_service.get_entry(db, posted.journal_entry_id)
        assert not any(l.account_id == discount_account.id for l in entry.lines)
        revenue_lines = [l for l in entry.lines if l.account_id == settings.service_revenue_account_id]
        assert revenue_lines[0].credit_amount == posted.subtotal_net
    finally:
        settings.sales_discounts_account_id = None
        db.commit()


def test_post_invoice_zero_amount_lines_omitted_from_journal_entry(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    entry = journal_service.get_entry(db, posted.journal_entry_id)
    # No deposit, no tax, no discount -> exactly AR debit + revenue credit.
    assert len(entry.lines) == 2


def test_void_invoice_with_missing_original_entry_raises(db):
    from sqlalchemy import text

    from app.services.invoice_service import InvoiceError

    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    orphaned_entry_id = posted.journal_entry_id

    # Simulate a dangling reference: null the FK, delete the entry for real,
    # then restore the in-memory (uncommitted) pointer so void_invoice sees
    # a journal_entry_id that no longer resolves to a row.
    posted.journal_entry_id = None
    db.commit()
    db.execute(text("DELETE FROM journal_lines WHERE journal_entry_id = :id"), {"id": orphaned_entry_id})
    db.execute(text("DELETE FROM journal_entries WHERE id = :id"), {"id": orphaned_entry_id})
    db.commit()
    posted.journal_entry_id = orphaned_entry_id

    with pytest.raises(InvoiceError, match="could not be found"):
        void_invoice(db, posted, "reason", user_id=None)
    # Leaves `posted` dirty in-memory (journal_entry_id set to a value that no
    # longer exists in the DB) — roll back so the shared teardown's cleanup
    # flush doesn't trip over it.
    db.rollback()


def test_get_invoice_by_journal_entry_id_matches_void_entry(db):
    invoice = invoice_service.create_draft(db, _header(), user_id=None)
    posted = post_invoice(db, invoice, user_id=None)
    voided = void_invoice(db, posted, "reason", user_id=None)
    found = invoice_service.get_invoice_by_journal_entry_id(db, voided.void_journal_entry_id)
    assert found.id == voided.id


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
