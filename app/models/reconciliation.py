from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SQLiteDecimal, TimestampMixin

STATUSES = ("in_progress", "completed", "undone")


class Reconciliation(Base, TimestampMixin):
    __tablename__ = "reconciliations"
    __table_args__ = (
        CheckConstraint(f"status IN {STATUSES}", name="ck_reconciliations_status"),
        UniqueConstraint(
            "account_id", "statement_date", name="uq_reconciliations_account_statement_date"
        ),
        # Partial unique index: at most one in-progress reconciliation per account at a time,
        # since "the previous completed one" and "which lines are clearable" only make sense
        # serialized per account.
        Index(
            "uq_reconciliations_account_in_progress",
            "account_id",
            unique=True,
            sqlite_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)
    statement_ending_balance: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(SQLiteDecimal(2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    account: Mapped["Account"] = relationship()
    completed_by_user: Mapped["User | None"] = relationship(foreign_keys=[completed_by_user_id])
    created_by_user: Mapped["User"] = relationship(foreign_keys=[created_by_user_id])
    cleared_lines: Mapped[list["JournalLine"]] = relationship(back_populates="reconciliation")
