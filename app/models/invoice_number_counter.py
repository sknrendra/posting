from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InvoiceNumberCounter(Base):
    """Per-day sequence counter for invoice numbers. Rows are created/incremented
    atomically via an UPSERT in invoice_service, never read-then-written."""

    __tablename__ = "invoice_number_counters"

    invoice_date: Mapped[date] = mapped_column(Date, primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
