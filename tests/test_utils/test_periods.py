from datetime import date

import pytest

from app.utils.periods import (
    list_periods,
    month_bounds,
    period_bounds,
    quarter_bounds,
    year_bounds,
)

# --- month_bounds ------------------------------------------------------------


@pytest.mark.parametrize(
    "d,expected",
    [
        (date(2026, 7, 15), (date(2026, 7, 1), date(2026, 7, 31))),
        (date(2026, 7, 1), (date(2026, 7, 1), date(2026, 7, 31))),
        (date(2026, 7, 31), (date(2026, 7, 1), date(2026, 7, 31))),
        (date(2026, 2, 10), (date(2026, 2, 1), date(2026, 2, 28))),  # non-leap year
        (date(2028, 2, 10), (date(2028, 2, 1), date(2028, 2, 29))),  # leap year
        (date(2026, 12, 5), (date(2026, 12, 1), date(2026, 12, 31))),
    ],
)
def test_month_bounds(d, expected):
    assert month_bounds(d) == expected


# --- quarter_bounds ------------------------------------------------------------


@pytest.mark.parametrize(
    "d,expected",
    [
        (date(2026, 1, 15), (date(2026, 1, 1), date(2026, 3, 31))),
        (date(2026, 3, 31), (date(2026, 1, 1), date(2026, 3, 31))),
        (date(2026, 4, 1), (date(2026, 4, 1), date(2026, 6, 30))),
        (date(2026, 7, 4), (date(2026, 7, 1), date(2026, 9, 30))),
        (date(2026, 10, 20), (date(2026, 10, 1), date(2026, 12, 31))),
        (date(2026, 12, 31), (date(2026, 10, 1), date(2026, 12, 31))),
    ],
)
def test_quarter_bounds(d, expected):
    assert quarter_bounds(d) == expected


def test_quarter_bounds_every_month_in_quarter_maps_same():
    jan, feb, mar = quarter_bounds(date(2026, 1, 1)), quarter_bounds(date(2026, 2, 1)), quarter_bounds(date(2026, 3, 1))
    assert jan == feb == mar


# --- year_bounds ------------------------------------------------------------


@pytest.mark.parametrize(
    "d",
    [date(2026, 1, 1), date(2026, 6, 15), date(2026, 12, 31)],
)
def test_year_bounds(d):
    assert year_bounds(d) == (date(2026, 1, 1), date(2026, 12, 31))


# --- period_bounds dispatch ---------------------------------------------------


def test_period_bounds_monthly():
    assert period_bounds("monthly", date(2026, 7, 15)) == month_bounds(date(2026, 7, 15))


def test_period_bounds_quarterly():
    assert period_bounds("quarterly", date(2026, 7, 15)) == quarter_bounds(date(2026, 7, 15))


def test_period_bounds_annual():
    assert period_bounds("annual", date(2026, 7, 15)) == year_bounds(date(2026, 7, 15))


def test_period_bounds_invalid_type_raises_key_error():
    with pytest.raises(KeyError):
        period_bounds("weekly", date(2026, 7, 15))


# --- list_periods --------------------------------------------------------


def test_list_periods_single_day_monthly():
    periods = list_periods("monthly", date(2026, 7, 15), date(2026, 7, 15))
    assert periods == [(date(2026, 7, 1), date(2026, 7, 31))]


def test_list_periods_two_months():
    periods = list_periods("monthly", date(2026, 6, 20), date(2026, 7, 10))
    assert periods == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
    ]


def test_list_periods_three_months_partial_coverage():
    periods = list_periods("monthly", date(2026, 5, 25), date(2026, 7, 5))
    assert periods == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
    ]


def test_list_periods_quarterly_two_quarters():
    periods = list_periods("quarterly", date(2026, 3, 1), date(2026, 4, 1))
    assert periods == [
        (date(2026, 4, 1), date(2026, 6, 30)),
        (date(2026, 1, 1), date(2026, 3, 31)),
    ]


def test_list_periods_annual_three_years():
    periods = list_periods("annual", date(2024, 6, 1), date(2026, 6, 1))
    assert periods == [
        (date(2026, 1, 1), date(2026, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
    ]


def test_list_periods_earliest_on_period_start_boundary_no_duplicate():
    periods = list_periods("monthly", date(2026, 6, 1), date(2026, 7, 15))
    assert periods == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
    ]
    assert len(periods) == len(set(periods))


def test_list_periods_inverted_range_returns_single_period():
    periods = list_periods("monthly", date(2026, 7, 1), date(2026, 1, 1))
    assert periods == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_list_periods_spans_year_boundary_monthly():
    periods = list_periods("monthly", date(2025, 11, 15), date(2026, 2, 10))
    assert periods == [
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2025, 11, 1), date(2025, 11, 30)),
    ]


def test_list_periods_spans_leap_year_february_monthly():
    periods = list_periods("monthly", date(2028, 1, 15), date(2028, 3, 15))
    assert (date(2028, 2, 1), date(2028, 2, 29)) in periods
    assert len(periods) == 3
