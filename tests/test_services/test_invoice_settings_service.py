import itertools
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import account_service, invoice_settings_service
from app.services.invoice_settings_service import InvalidAccountMappingError

_seq = itertools.count()


def _account(db, account_type):
    return account_service.create_account(db, f"IS-{account_type[:3].upper()}-{next(_seq)}", "X", account_type)


def _base_update_kwargs(settings, **overrides):
    kwargs = dict(
        company_name=settings.company_name,
        company_address=settings.company_address,
        company_tax_reg_number=settings.company_tax_reg_number,
        bank_name=settings.bank_name,
        bank_account_name=settings.bank_account_name,
        bank_account_number=settings.bank_account_number,
        accepted_payment_methods=settings.accepted_payment_methods,
        payment_reference_instruction=settings.payment_reference_instruction,
        invoice_prefix=settings.invoice_prefix,
        default_payment_terms_days=settings.default_payment_terms_days,
        default_tax_rate=settings.default_tax_rate,
        default_notes=settings.default_notes,
        default_terms=settings.default_terms,
        ar_account_id=settings.ar_account_id,
        service_revenue_account_id=settings.service_revenue_account_id,
        tax_payable_account_id=settings.tax_payable_account_id,
        unearned_revenue_account_id=settings.unearned_revenue_account_id,
        sales_discounts_account_id=settings.sales_discounts_account_id,
    )
    kwargs.update(overrides)
    return kwargs


@pytest.fixture()
def snapshot_settings(db):
    """invoice_settings is a migration-seeded singleton not reset between
    tests; snapshot and restore whatever this test mutates."""
    settings = invoice_settings_service.get_settings(db)
    original = _base_update_kwargs(settings)
    yield settings
    invoice_settings_service.update_settings(db, settings, **original)


# --- get_settings ------------------------------------------------------------


def test_get_settings_returns_singleton(db):
    first = invoice_settings_service.get_settings(db)
    second = invoice_settings_service.get_settings(db)
    assert first.id == second.id == 1


# --- update_settings -----------------------------------------------------


def test_update_settings_all_mappings_none_succeeds(db, snapshot_settings):
    settings = snapshot_settings
    kwargs = _base_update_kwargs(
        settings,
        ar_account_id=None,
        service_revenue_account_id=None,
        tax_payable_account_id=None,
        unearned_revenue_account_id=None,
        sales_discounts_account_id=None,
    )
    updated = invoice_settings_service.update_settings(db, settings, **kwargs)
    assert updated.ar_account_id is None
    assert updated.sales_discounts_account_id is None


def test_update_settings_mapping_to_nonexistent_account_raises(db, snapshot_settings):
    settings = snapshot_settings
    kwargs = _base_update_kwargs(settings, ar_account_id=999999)
    with pytest.raises(InvalidAccountMappingError, match="account not found"):
        invoice_settings_service.update_settings(db, settings, **kwargs)


def test_update_settings_mapping_wrong_type_raises(db, snapshot_settings):
    settings = snapshot_settings
    wrong_type_account = _account(db, "expense")  # AR expects asset
    kwargs = _base_update_kwargs(settings, ar_account_id=wrong_type_account.id)
    with pytest.raises(InvalidAccountMappingError, match="Accounts Receivable"):
        invoice_settings_service.update_settings(db, settings, **kwargs)


@pytest.mark.parametrize("account_type", ["revenue", "expense"])
def test_update_settings_sales_discounts_accepts_revenue_or_expense(db, snapshot_settings, account_type):
    settings = snapshot_settings
    account = _account(db, account_type)
    kwargs = _base_update_kwargs(settings, sales_discounts_account_id=account.id)
    updated = invoice_settings_service.update_settings(db, settings, **kwargs)
    assert updated.sales_discounts_account_id == account.id


def test_update_settings_validation_failure_leaves_other_fields_unchanged(db, snapshot_settings):
    settings = snapshot_settings
    original_name = settings.company_name
    kwargs = _base_update_kwargs(settings, company_name="Should Not Persist", ar_account_id=999999)
    with pytest.raises(InvalidAccountMappingError):
        invoice_settings_service.update_settings(db, settings, **kwargs)
    db.refresh(settings)
    assert settings.company_name == original_name


# --- backfill_default_mappings -------------------------------------------


def test_backfill_default_mappings_noop_when_all_set(db, snapshot_settings):
    settings = snapshot_settings
    before = (
        settings.ar_account_id,
        settings.service_revenue_account_id,
        settings.tax_payable_account_id,
        settings.unearned_revenue_account_id,
    )
    invoice_settings_service.backfill_default_mappings(db)
    after = (
        settings.ar_account_id,
        settings.service_revenue_account_id,
        settings.tax_payable_account_id,
        settings.unearned_revenue_account_id,
    )
    assert before == after


def test_backfill_default_mappings_fills_only_unset_fields(db, snapshot_settings):
    settings = snapshot_settings
    explicit_ar = _account(db, "asset")
    kwargs = _base_update_kwargs(
        settings,
        ar_account_id=explicit_ar.id,
        service_revenue_account_id=None,
    )
    invoice_settings_service.update_settings(db, settings, **kwargs)

    invoice_settings_service.backfill_default_mappings(db)

    assert settings.ar_account_id == explicit_ar.id  # explicit choice untouched
    assert settings.service_revenue_account_id is not None  # backfilled


# --- compose_payment_instructions -----------------------------------------


def test_compose_payment_instructions_all_fields():
    from app.models.invoice_settings import InvoiceSettings

    settings = InvoiceSettings(
        bank_name="Test Bank",
        bank_account_name="Acme Corp",
        bank_account_number="12345",
        accepted_payment_methods="Wire, Card",
        payment_reference_instruction="Use invoice number as reference",
    )
    result = invoice_settings_service.compose_payment_instructions(settings)
    assert result == (
        "Bank: Test Bank\n"
        "Account Name: Acme Corp\n"
        "Account Number: 12345\n"
        "Accepted Methods: Wire, Card\n"
        "Use invoice number as reference"
    )


def test_compose_payment_instructions_all_blank_returns_none():
    from app.models.invoice_settings import InvoiceSettings

    settings = InvoiceSettings()
    assert invoice_settings_service.compose_payment_instructions(settings) is None


def test_compose_payment_instructions_partial_fields_no_blank_lines():
    from app.models.invoice_settings import InvoiceSettings

    settings = InvoiceSettings(bank_name="Only Bank")
    result = invoice_settings_service.compose_payment_instructions(settings)
    assert result == "Bank: Only Bank"


# --- save_logo -------------------------------------------------------------


def test_save_logo_writes_file_and_sets_path(db, snapshot_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_settings_service, "LOGO_UPLOAD_DIR", tmp_path / "uploads")
    settings = snapshot_settings
    web_path = invoice_settings_service.save_logo(settings, "mylogo.png", b"fake-bytes")
    assert web_path == "/static/uploads/invoice-logo.png"
    assert (tmp_path / "uploads" / "invoice-logo.png").read_bytes() == b"fake-bytes"
    assert settings.company_logo_path == web_path


def test_save_logo_no_extension_falls_back_to_png(db, snapshot_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_settings_service, "LOGO_UPLOAD_DIR", tmp_path / "uploads")
    settings = snapshot_settings
    web_path = invoice_settings_service.save_logo(settings, "no-extension-file", b"x")
    assert web_path.endswith("invoice-logo.png")


def test_save_logo_second_upload_overwrites_disk_file(db, snapshot_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(invoice_settings_service, "LOGO_UPLOAD_DIR", tmp_path / "uploads")
    settings = snapshot_settings
    invoice_settings_service.save_logo(settings, "first.png", b"first-bytes")
    invoice_settings_service.save_logo(settings, "second.png", b"second-bytes")
    assert (tmp_path / "uploads" / "invoice-logo.png").read_bytes() == b"second-bytes"
