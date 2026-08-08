from datetime import date
from decimal import Decimal as D
from pathlib import Path

from app.services import invoice_pdf_service, invoice_service, invoice_settings_service
from app.services.invoice_service import InvoiceHeaderInput, InvoiceLineInput


def _header(**overrides):
    defaults = dict(
        invoice_date=date(2026, 7, 29),
        customer_name="Acme Corp",
        lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=D(1000000))],
    )
    defaults.update(overrides)
    return InvoiceHeaderInput(**defaults)


def test_render_draft_invoice_uses_live_payment_instructions(db, monkeypatch):
    settings = invoice_settings_service.get_settings(db)
    original = settings.bank_name
    settings.bank_name = "Live Bank"
    db.commit()
    try:
        invoice = invoice_service.create_draft(db, _header(), user_id=None)
        pdf_bytes = invoice_pdf_service.render_invoice_pdf(invoice, settings)
        assert pdf_bytes[:4] == b"%PDF"
    finally:
        settings.bank_name = original
        db.commit()


def test_render_posted_invoice_uses_frozen_snapshot_not_live_settings(db):
    settings = invoice_settings_service.get_settings(db)
    original_bank = settings.bank_name
    settings.bank_name = "Bank At Post Time"
    db.commit()
    try:
        invoice = invoice_service.create_draft(db, _header(), user_id=None)
        posted = invoice_service.post_invoice(db, invoice, user_id=None)
        assert posted.snapshot_payment_instructions is not None
        assert "Bank At Post Time" in posted.snapshot_payment_instructions

        # Settings change after posting; the PDF must not reflect this.
        settings.bank_name = "Bank Changed After Posting"
        db.commit()
        db.refresh(posted)
        assert "Bank Changed After Posting" not in (posted.snapshot_payment_instructions or "")
        pdf_bytes = invoice_pdf_service.render_invoice_pdf(posted, settings)
        assert pdf_bytes[:4] == b"%PDF"
    finally:
        settings.bank_name = original_bank
        db.commit()


def test_logo_file_uri_no_logo_configured_falls_back_to_default(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.company_logo_path
    settings.company_logo_path = None
    db.commit()
    try:
        uri, is_default = invoice_pdf_service._logo_file_uri(settings)
        assert is_default is True
        assert uri is not None
        assert uri.startswith("file://")
    finally:
        settings.company_logo_path = original
        db.commit()


def test_logo_file_uri_configured_and_present_on_disk(db, tmp_path, monkeypatch):
    settings = invoice_settings_service.get_settings(db)
    original = settings.company_logo_path

    # _logo_file_uri resolves the web path against Path("app") / <web path>,
    # relative to CWD — mirror that exact layout under tmp_path.
    logo_dir = tmp_path / "app" / "static" / "uploads"
    logo_dir.mkdir(parents=True)
    (logo_dir / "invoice-logo.png").write_bytes(b"fake-logo-bytes")
    monkeypatch.chdir(tmp_path)
    settings.company_logo_path = "/static/uploads/invoice-logo.png"
    db.commit()
    try:
        uri, is_default = invoice_pdf_service._logo_file_uri(settings)
        assert is_default is False
        assert uri.endswith("invoice-logo.png")
    finally:
        settings.company_logo_path = original
        db.commit()


def test_logo_file_uri_configured_but_missing_on_disk_falls_back(db):
    settings = invoice_settings_service.get_settings(db)
    original = settings.company_logo_path
    # No chdir here — stay at the repo root so the default icon still
    # resolves; only the *configured* logo path is bogus.
    settings.company_logo_path = "/static/uploads/does-not-exist.png"
    db.commit()
    try:
        uri, is_default = invoice_pdf_service._logo_file_uri(settings)
        assert is_default is True
        assert uri is not None
    finally:
        settings.company_logo_path = original
        db.commit()


def test_logo_file_uri_no_default_icon_returns_none(db, monkeypatch):
    settings = invoice_settings_service.get_settings(db)
    original = settings.company_logo_path
    settings.company_logo_path = None
    db.commit()
    monkeypatch.setattr(invoice_pdf_service, "_DEFAULT_LOGO_PATH", Path("/nonexistent/path/logo.png"))
    try:
        uri, is_default = invoice_pdf_service._logo_file_uri(settings)
        assert uri is None
        assert is_default is False
    finally:
        settings.company_logo_path = original
        db.commit()
