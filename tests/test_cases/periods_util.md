# Test cases — `app/utils/periods.py`

Pure functions, no DB — good candidates for fast, exhaustive `pytest.mark.parametrize` coverage.

## `month_bounds`

| # | Case | Expected |
|---|------|----------|
| P1 | Mid-month date (e.g. `2026-07-15`) | `(2026-07-01, 2026-07-31)` |
| P2 | First day of month | Same bounds as any other day in that month |
| P3 | Last day of month | Same bounds |
| P4 | February, non-leap year (e.g. 2026) | End `2026-02-28` |
| P5 | February, leap year (e.g. 2028) | End `2028-02-29` |
| P6 | December | End `12-31`, doesn't roll into next year |

## `quarter_bounds`

| # | Case | Expected |
|---|------|----------|
| P1 | January (Q1) | `(YYYY-01-01, YYYY-03-31)` |
| P2 | March (still Q1) | Same as P1 |
| P3 | April (Q2) | `(YYYY-04-01, YYYY-06-30)` |
| P4 | July (Q3) | `(YYYY-07-01, YYYY-09-30)` |
| P5 | October (Q4) | `(YYYY-10-01, YYYY-12-31)` |
| P6 | December (Q4) | End `YYYY-12-31` |
| P7 | Each month within a quarter maps to the same bounds | e.g. Jan/Feb/Mar all → Q1 bounds |

## `year_bounds`

| # | Case | Expected |
|---|------|----------|
| P1 | Any date in a year | `(YYYY-01-01, YYYY-12-31)` regardless of month/day |

## `period_bounds` (dispatch)

| # | Case | Expected |
|---|------|----------|
| P1 | `period_type="monthly"` | Delegates to `month_bounds` |
| P2 | `period_type="quarterly"` | Delegates to `quarter_bounds` |
| P3 | `period_type="annual"` | Delegates to `year_bounds` |
| N1 | Invalid `period_type` (e.g. `"weekly"`) | `KeyError` from `BOUNDS_FN[period_type]` — no explicit validation, document actual behavior |

## `list_periods`

| # | Case | Expected |
|---|------|----------|
| P1 | `earliest == latest` (single day), monthly | Returns exactly one period covering that day |
| P2 | `earliest`/`latest` span exactly 2 calendar months | Returns 2 periods, most-recent-first (`latest`'s period is index 0) |
| P3 | `earliest`/`latest` span parts of 3 calendar months | Returns 3 periods even though the first/last are only partially covered |
| P4 | Quarterly with a range spanning 2 quarters | Returns 2 quarter periods |
| P5 | Annual with a range spanning 3 years | Returns 3 year periods |
| P6 | `earliest` falls exactly on a period start boundary | Loop terminates correctly (the `if start <= earliest: break` condition), no infinite loop, no duplicate/missing period |
| P7 | `earliest > latest` (inverted range) | Since the loop starts at `latest` and walks backward, and `earliest > latest` means `start <= earliest` is true on the very first iteration — returns exactly 1 period (the one containing `latest`). Worth asserting this explicit degenerate behavior rather than assuming it errors. |
| P8 | Range spanning a year boundary (e.g. Nov earliest, Feb latest), monthly | Correctly walks Dec→Jan→Feb without off-by-one on year rollover |
| P9 | Range spanning a leap-year February, monthly | `Feb 29` handled without `ValueError` from `date()` construction (the `cursor - timedelta(days=1)` stepping back from March 1 must land on Feb 28/29 correctly) |
