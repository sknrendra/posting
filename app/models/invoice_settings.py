from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SQLiteDecimal, TimestampMixin


class InvoiceSettings(Base, TimestampMixin):
    """Singleton (id is always 1) — company/payment/defaults configuration for
    invoices, plus the chart-of-accounts mapping used when posting."""

    __tablename__ = "invoice_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_invoice_settings_singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    company_tax_reg_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    company_logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    accepted_payment_methods: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payment_reference_instruction: Mapped[str | None] = mapped_column(String(500), nullable=True)

    invoice_prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="INV")
    default_payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    default_tax_rate: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False, default=Decimal("0"))
    default_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    default_terms: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Required mappings — enforced (with a specific error per field) at posting time in
    # invoice_service.post_invoice, not via NOT NULL, so the settings screen can be saved
    # incrementally before every mapping is filled in.
    ar_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    service_revenue_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    tax_payable_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    unearned_revenue_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    # Optional — its presence (plus a nonzero discount) switches posting from the net
    # method to the gross method (see invoice_service.post_invoice).
    sales_discounts_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
