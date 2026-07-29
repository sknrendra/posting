from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.invoice_settings import InvoiceSettings

LOGO_UPLOAD_DIR = Path("app/static/uploads")

# account_id field name -> account types considered sane for that mapping.
_MAPPING_EXPECTED_TYPES = {
    "ar_account_id": ("asset",),
    "service_revenue_account_id": ("revenue",),
    "tax_payable_account_id": ("liability",),
    "unearned_revenue_account_id": ("liability",),
    "sales_discounts_account_id": ("revenue", "expense"),
}

_MAPPING_LABELS = {
    "ar_account_id": "Accounts Receivable",
    "service_revenue_account_id": "Service Revenue",
    "tax_payable_account_id": "Tax Payable",
    "unearned_revenue_account_id": "Unearned Revenue",
    "sales_discounts_account_id": "Sales Discounts",
}


class InvalidAccountMappingError(Exception):
    pass


def get_settings(db: DbSession) -> InvoiceSettings:
    """Singleton row (id=1) — created with defaults on first access."""
    settings = db.get(InvoiceSettings, 1)
    if settings is None:
        settings = InvoiceSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _validate_mapping(db: DbSession, field: str, account_id: int | None) -> None:
    if account_id is None:
        return
    account = db.get(Account, account_id)
    if account is None:
        raise InvalidAccountMappingError(f"{_MAPPING_LABELS[field]}: account not found")
    expected = _MAPPING_EXPECTED_TYPES[field]
    if account.account_type not in expected:
        raise InvalidAccountMappingError(
            f"{_MAPPING_LABELS[field]} must be mapped to a "
            f"{'/'.join(expected)} account (got {account.account_type}: {account.name})"
        )


def update_settings(
    db: DbSession,
    settings: InvoiceSettings,
    *,
    company_name: str | None,
    company_address: str | None,
    company_tax_reg_number: str | None,
    bank_name: str | None,
    bank_account_name: str | None,
    bank_account_number: str | None,
    accepted_payment_methods: str | None,
    payment_reference_instruction: str | None,
    invoice_prefix: str,
    default_payment_terms_days: int,
    default_tax_rate: Decimal,
    default_notes: str | None,
    default_terms: str | None,
    ar_account_id: int | None,
    service_revenue_account_id: int | None,
    tax_payable_account_id: int | None,
    unearned_revenue_account_id: int | None,
    sales_discounts_account_id: int | None,
) -> InvoiceSettings:
    mapping_fields = {
        "ar_account_id": ar_account_id,
        "service_revenue_account_id": service_revenue_account_id,
        "tax_payable_account_id": tax_payable_account_id,
        "unearned_revenue_account_id": unearned_revenue_account_id,
        "sales_discounts_account_id": sales_discounts_account_id,
    }
    for field, account_id in mapping_fields.items():
        _validate_mapping(db, field, account_id)

    settings.company_name = company_name
    settings.company_address = company_address
    settings.company_tax_reg_number = company_tax_reg_number
    settings.bank_name = bank_name
    settings.bank_account_name = bank_account_name
    settings.bank_account_number = bank_account_number
    settings.accepted_payment_methods = accepted_payment_methods
    settings.payment_reference_instruction = payment_reference_instruction
    settings.invoice_prefix = invoice_prefix
    settings.default_payment_terms_days = default_payment_terms_days
    settings.default_tax_rate = default_tax_rate
    settings.default_notes = default_notes
    settings.default_terms = default_terms
    for field, account_id in mapping_fields.items():
        setattr(settings, field, account_id)

    db.commit()
    db.refresh(settings)
    return settings


# field -> account code, used only to backfill a mapping that's still unset (never
# overwrites an explicit choice). Exists because migrations seed these accounts and
# the invoice_settings row in a fixed order, but entrypoint.sh runs
# `alembic upgrade head` before `python -m app.seed` — so on a brand new install,
# the pre-existing default chart of accounts (e.g. code 4000, Service Revenue)
# doesn't exist yet at migration time and can't be wired up until after seeding.
_DEFAULT_MAPPING_CODES = {
    "ar_account_id": "1100",
    "service_revenue_account_id": "4000",
    "tax_payable_account_id": "2020",
    "unearned_revenue_account_id": "2030",
}


def backfill_default_mappings(db: DbSession) -> None:
    settings = get_settings(db)
    accounts_by_code = {
        a.code: a.id for a in db.query(Account).filter(Account.code.in_(_DEFAULT_MAPPING_CODES.values()))
    }
    changed = False
    for field, code in _DEFAULT_MAPPING_CODES.items():
        if getattr(settings, field) is None and code in accounts_by_code:
            setattr(settings, field, accounts_by_code[code])
            changed = True
    if changed:
        db.commit()


def compose_payment_instructions(settings: InvoiceSettings) -> str | None:
    parts = []
    if settings.bank_name:
        parts.append(f"Bank: {settings.bank_name}")
    if settings.bank_account_name:
        parts.append(f"Account Name: {settings.bank_account_name}")
    if settings.bank_account_number:
        parts.append(f"Account Number: {settings.bank_account_number}")
    if settings.accepted_payment_methods:
        parts.append(f"Accepted Methods: {settings.accepted_payment_methods}")
    if settings.payment_reference_instruction:
        parts.append(settings.payment_reference_instruction)
    return "\n".join(parts) if parts else None


def save_logo(settings: InvoiceSettings, filename: str, content: bytes) -> str:
    """Saves an uploaded logo to disk and returns the web-servable path. Caller
    is responsible for calling db.commit() to persist settings.company_logo_path."""
    LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".png"
    disk_name = f"invoice-logo{suffix}"
    (LOGO_UPLOAD_DIR / disk_name).write_bytes(content)
    web_path = f"/static/uploads/{disk_name}"
    settings.company_logo_path = web_path
    return web_path
