from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.reconciliation import PERIOD_TYPES

REPORT_TYPES = ("profit_loss", "balance_sheet", "cash_flow")


class GeneratedReport(Base, TimestampMixin):
    __tablename__ = "generated_reports"
    __table_args__ = (
        CheckConstraint(f"report_type IN {REPORT_TYPES}", name="ck_generated_reports_report_type"),
        CheckConstraint(f"period_type IN {PERIOD_TYPES}", name="ck_generated_reports_period_type"),
        UniqueConstraint(
            "report_type", "period_type", "period_start", "period_end",
            name="uq_generated_reports_period",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    generated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
