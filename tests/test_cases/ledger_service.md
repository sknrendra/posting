# Test cases — `app/services/ledger_service.py`

Covers `get_lines_in_range`, `get_account_balance`, `get_balances_as_of`,
`get_period_net_activity`, `get_running_ledger`.

All of these depend on `journal_service.post_entry` to seed data, so tests will
post real journal entries via the fixture-provided `db` session rather than
inserting rows directly (keeps them honest to the FK/constraint shape).

## `get_lines_in_range`

| # | Case | Expected |
|---|------|----------|
| P1 | No date filters | All lines for the account, ordered by `entry_date, JournalLine.id` |
| P2 | `date_from` only | Only lines on/after that date |
| P3 | `date_to` only | Only lines on/before that date |
| P4 | Both bounds, inclusive | Lines exactly on `date_from` and `date_to` are included (boundary test) |
| P5 | `date_from > date_to` | Returns `[]` (no error — just an empty range) |
| P6 | Account with no lines at all | Returns `[]` |
| P7 | Lines from a different account excluded | Filter is exact on `account_id` |

## `get_account_balance`

| # | Case | Expected |
|---|------|----------|
| P1 | Debit-normal account (asset/expense) with more debits than credits | Positive balance = `sum(debit - credit)` |
| P2 | Debit-normal account with more credits than debits | Negative balance (abnormal balance, not clamped to zero) |
| P3 | Credit-normal account (liability/equity/revenue) with more credits than debits | Positive balance = `-(sum(debit - credit))` |
| P4 | Credit-normal account with more debits than credits | Negative balance |
| P5 | `as_of_date` excludes future-dated lines | Balance reflects only lines with `entry_date <= as_of_date` |
| P6 | `as_of_date=None` | All lines counted (no upper bound) |
| P7 | Account with zero activity | Balance is `Decimal("0")` |
| P8 | Mixed-currency-precision amounts (e.g. `12345.67`) | Exact `Decimal` result, no float drift |
| P9 | Voided invoice: original + reversing entry both posted | Net balance returns to pre-invoice state (reversal fully cancels) |

## `get_balances_as_of`

| # | Case | Expected |
|---|------|----------|
| P1 | List of several accounts | Returns `dict[account_id] -> Decimal`, one entry per account, matching individual `get_account_balance` calls |
| P2 | Empty `accounts` list | Returns `{}` |
| P3 | Duplicate account in the list | Dict just has one key (last write wins, same value either way) |

## `get_period_net_activity`

| # | Case | Expected |
|---|------|----------|
| P1 | Activity inside `[date_from, date_to]` only | Net movement for the window, sign-adjusted like `get_account_balance` |
| P2 | Activity that predates `date_from` (opening balance) | **Not** included — this is period-only, not cumulative (key difference from `get_account_balance`) |
| P3 | Activity that postdates `date_to` | Not included |
| P4 | No activity in the window but activity exists outside it | Returns `Decimal("0")`, not the account's all-time balance |
| P5 | Single-day window (`date_from == date_to`) | Only that day's lines counted |

## `get_running_ledger`

| # | Case | Expected |
|---|------|----------|
| P1 | `date_from=None` | `opening_balance == Decimal("0")` (no prior-period lookup performed) |
| P2 | `date_from` set, with prior activity before it | `opening_balance == get_account_balance(..., as_of_date=date_from - 1 day)` |
| P3 | `date_from` set, no prior activity | `opening_balance == 0` |
| P4 | Running balance accumulates correctly across multiple lines, debit-normal account | Each row's `balance` = previous running total + signed movement of that line |
| P5 | Running balance accumulates correctly, credit-normal account | Sign flips relative to raw debit/credit (uses `sign = -1`) |
| P6 | `closing_balance` equals last row's `balance` | When rows is non-empty |
| P7 | No lines in range | `rows == []`, `closing_balance == opening_balance` |
| P8 | Rows ordering | Chronological by `entry_date`, then `JournalLine.id` for same-day entries (stable tie-break) |
| P9 | `date_to` provided, `date_from=None` | Opening balance still `0` (only `date_from` triggers the opening-balance lookup — verify this isn't inconsistent/a latent bug: a `date_to`-only "get everything through this date" call has opening_balance 0 but includes pre-existing activity in `rows`, so closing_balance still ends up correct, but "opening_balance" is not "balance right before rows start" in that case) |

## Boundary / negative

| # | Case | Expected |
|---|------|----------|
| B1 | `as_of_date` earlier than any journal activity | Balance is `0` |
| B2 | Timezone-naive date arithmetic (`timedelta(days=1)` around DST-irrelevant plain `date`) | No exceptions, correct day rollback across month/year boundaries (e.g. `date_from = Jan 1` → opening balance as-of `Dec 31` prior year) |
