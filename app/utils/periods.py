from calendar import monthrange
from datetime import date, timedelta

PERIOD_TYPES = ("monthly", "quarterly", "annual")


def month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    end = d.replace(day=monthrange(d.year, d.month)[1])
    return start, end


def quarter_bounds(d: date) -> tuple[date, date]:
    quarter = (d.month - 1) // 3
    start_month = quarter * 3 + 1
    start = date(d.year, start_month, 1)
    end_month = start_month + 2
    end = date(d.year, end_month, monthrange(d.year, end_month)[1])
    return start, end


def year_bounds(d: date) -> tuple[date, date]:
    return date(d.year, 1, 1), date(d.year, 12, 31)


BOUNDS_FN = {"monthly": month_bounds, "quarterly": quarter_bounds, "annual": year_bounds}


def period_bounds(period_type: str, d: date) -> tuple[date, date]:
    return BOUNDS_FN[period_type](d)


def list_periods(period_type: str, earliest: date, latest: date) -> list[tuple[date, date]]:
    """Calendar periods covering [earliest, latest], most recent first."""
    fn = BOUNDS_FN[period_type]
    periods = []
    cursor = latest
    while True:
        start, end = fn(cursor)
        periods.append((start, end))
        if start <= earliest:
            break
        cursor = start - timedelta(days=1)
    return periods
