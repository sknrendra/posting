from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SQLiteDecimal, TimestampMixin

PERIOD_TYPES = ("monthly", "quarterly", "annual")
STATUSES = ("open", "completed")


class Reconciliation(Base, TimestampMixin):
    __tablename__ = "reconciliations"
    __table_args__ = (
        CheckConstraint(f"period_type IN {PERIOD_TYPES}", name="ck_reconciliations_period_type"),
        CheckConstraint(f"status IN {STATUSES}", name="ck_reconciliations_status"),
        UniqueConstraint(
            "period_type", "period_start", "period_end", "account_id",
            name="uq_reconciliations_period_account",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    statement_balance: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False)
    book_balance: Mapped[Decimal | None] = mapped_column(SQLiteDecimal(2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reconciled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
