from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SQLiteDecimal, TimestampMixin

SOURCES = ("manual", "webhook", "invoice")


class JournalEntry(Base, TimestampMixin):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint(f"source IN {SOURCES}", name="ck_journal_entries_source"),
        UniqueConstraint("external_reference", name="uq_journal_entries_external_reference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    memo: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id"), nullable=True
    )
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # No relationship() here on purpose — see the comment on Invoice.journal_entry_id
    # about the multiple FK paths between invoices and journal_entries.
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True, index=True)
    reverses_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id"), nullable=True
    )

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="journal_entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        # debit_amount/credit_amount are stored as exact-text (see SQLiteDecimal) so they have
        # TEXT affinity in SQLite; comparing text against a bare numeric literal (e.g. `= 0`)
        # compares '0.00' as a string against '0' and always fails. CAST forces numeric comparison.
        CheckConstraint(
            "CAST(debit_amount AS NUMERIC) = 0 OR CAST(credit_amount AS NUMERIC) = 0",
            name="ck_journal_lines_one_sided",
        ),
        CheckConstraint(
            "CAST(debit_amount AS NUMERIC) > 0 OR CAST(credit_amount AS NUMERIC) > 0",
            name="ck_journal_lines_nonzero",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    debit_amount: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), default=Decimal("0"), nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), default=Decimal("0"), nullable=False)
    memo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    journal_entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship()
