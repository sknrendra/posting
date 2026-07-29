from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SQLiteDecimal, TimestampMixin

STATUSES = ("draft", "posted", "void")
DISCOUNT_MODES = ("amount", "percent")


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(f"status IN {STATUSES}", name="ck_invoices_status"),
        CheckConstraint(
            "CAST(deposit_applied AS NUMERIC) <= CAST(total AS NUMERIC)",
            name="ck_invoices_deposit_not_over_total",
        ),
        UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # Assigned only at posting (see invoice_service.post_invoice) — never at draft
    # creation, so abandoned drafts never consume a number.
    invoice_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # No customer master exists. Kept as plain typed fields (nullable — required only
    # at posting, not at draft save) grouped together so a nullable customer_id FK can
    # be bolted on later without disturbing historically typed values.
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    customer_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tax_rate: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False, default=Decimal("0"))
    # Manually entered for now — there is no Payments module yet, so this debits Unearned
    # Revenue with nothing backing it on the cash side. Once Payments lands, this should
    # become a reference to an actual recorded deposit transaction instead of a free number.
    deposit_applied: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))

    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    terms: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Stored/recomputed on every draft save so list views never join+sum lines.
    subtotal_gross: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    total_discount: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    subtotal_net: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    balance_due: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))

    # Frozen at posting time so a posted invoice renders identically forever, even if
    # InvoiceSettings changes later. Customer fields need no snapshot — they already
    # live directly on the invoice and the invoice itself is immutable once posted.
    snapshot_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_company_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    snapshot_tax_reg_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    snapshot_payment_instructions: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    posted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    void_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # No relationship()s on these — three distinct FK paths exist between invoices and
    # journal_entries (this entry, the void-reversal entry, and the entry's own
    # reverses_entry_id back-pointer), so declaring relationship()/back_populates on all
    # of them risks AmbiguousForeignKeysError. Services use db.get(JournalEntry, ...) instead.
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)
    void_journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"), nullable=True)

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.line_number"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_invoice_lines_invoice_line_number"),
        CheckConstraint(f"discount_mode IN {DISCOUNT_MODES}", name="ck_invoice_lines_discount_mode"),
        CheckConstraint(
            "CAST(discount_amount AS NUMERIC) <= CAST(line_gross AS NUMERIC)",
            name="ck_invoice_lines_discount_not_over_gross",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(SQLiteDecimal(4), nullable=False, default=Decimal("1"))
    rate: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False, default=Decimal("0"))

    discount_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="amount")
    # discount_amount is authoritative for all math and journal entries.
    # discount_percentage is display-only, kept in sync per discount_mode so the form
    # never derives a field from its own derived counterpart (see invoice_calc.resolve_line).
    discount_amount: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    discount_percentage: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False, default=Decimal("0"))

    line_gross: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))
    line_amount: Mapped[Decimal] = mapped_column(SQLiteDecimal(0), nullable=False, default=Decimal("0"))

    # Display-only flat-fee flag: when set, the PDF hides the qty/rate columns for this
    # line. quantity still stays populated at 1 so qty*rate math never special-cases it.
    hide_qty_rate: Mapped[bool] = mapped_column(default=False, nullable=False)

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")
