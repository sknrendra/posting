from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC datetime — SQLite's DateTime type stores naive values, and
    mixing naive/aware datetimes raises on comparison, so we standardize on
    naive-UTC everywhere in this app."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SQLiteDecimal(TypeDecorator):
    """Stores Decimal as exact text, bypassing SQLite's NUMERIC type affinity
    (which can silently coerce through float on round-trip)."""

    impl = String
    cache_ok = True

    def __init__(self, scale: int = 2, **kw):
        super().__init__(**kw)
        self.scale = scale

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(Decimal(value).quantize(Decimal(1).scaleb(-self.scale)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)
