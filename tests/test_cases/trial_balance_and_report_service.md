# Test cases — `app/services/trial_balance_service.py` and `app/services/report_service.py`

## `trial_balance_service.compute`

| # | Case | Expected |
|---|------|----------|
| P1 | Accounts with nonzero balances, mix of normal/abnormal | Each row placed in its normal-balance column; `total_debit == sum(debit_col)`, `total_credit == sum(credit_col)` |
| P2 | Account with a zero balance | Excluded from `rows` entirely |
| P3 | Debit-normal account with an abnormal (credit/negative) balance | Placed as a **positive** number in the credit column, not a negative debit |
| P4 | Credit-normal account with an abnormal (debit/positive-raw) balance | Placed as a positive number in the debit column |
| P5 | Balanced books (any valid set of posted journal entries) | `total_debit == total_credit`, `balanced=True` |
| P6 | Inactive accounts | Excluded (`account_service.list_accounts(include_inactive=False)`) |
| P7 | No accounts have activity | `rows=[]`, both totals `0`, `balanced=True` |
| P8 | `as_of_date` excludes later activity | Balances match `ledger_service.get_account_balance(..., as_of_date)` semantics — verify by posting an entry after `as_of_date` and confirming it's excluded |
| N1 | Deliberately corrupt state where debits != credits system-wide (shouldn't be reachable via `journal_service.post_entry`, but simulate by direct DB manipulation in the test) | `balanced=False` — confirms the function surfaces imbalance rather than masking it |

## `report_service.compute_profit_loss`

| # | Case | Expected |
|---|------|----------|
| P1 | Revenue and expense accounts with activity in `[period_start, period_end]` | Split into `revenue`/`expenses` lists, `net_income = total_revenue - total_expenses` |
| P2 | Asset/liability/equity accounts with activity in the period | Excluded entirely (only revenue/expense considered) |
| P3 | Account with zero net activity in the period (even if nonzero all-time balance) | Excluded from output — uses `get_period_net_activity`, not `get_account_balance` |
| P4 | Activity outside `[period_start, period_end]` | Not counted (period-scoped, not cumulative) |
| P5 | No revenue/expense activity in the period | `revenue=[]`, `expenses=[]`, `net_income=0` |
| P6 | Expenses exceed revenue | `net_income` negative |

## `report_service.compute_balance_sheet`

| # | Case | Expected |
|---|------|----------|
| P1 | Asset/liability/equity accounts with nonzero balances as of `period_end` | Bucketed correctly, totals summed |
| P2 | Revenue/expense accounts feed into computed `retained_earnings`, not their own bucket | Revenue increases retained earnings, expense decreases it; neither appears in `assets`/`liabilities`/`equity` rows directly |
| P3 | `retained_earnings != 0` | A synthetic `"Retained Earnings (computed)"` row (empty `account_code`) appended to `equity`, added to `total_equity` |
| P4 | `retained_earnings == 0` exactly (revenue == expenses) | No synthetic row appended |
| P5 | Balanced result after any valid combination of posted invoices/manual entries | `total_assets == total_liabilities + total_equity`, `balanced=True` (this is the core accounting-equation invariant — the most important property test for this whole app) |
| P6 | Zero-balance asset/liability/equity account | Excluded from its bucket's rows (but still contributes `0` either way) |
| P7 | `period_end` in the past relative to some activity | Only activity through that date counted (uses `as_of_date=period_end`) |

## `report_service.compute_cash_flow`

| # | Case | Expected |
|---|------|----------|
| P1 | Single cash account, single contra account per entry (simple case: cash debit, revenue credit) | `net_change = closing - opening`, one breakdown row attributing the full movement to the contra account |
| P2 | One journal entry touches cash + two contra accounts (e.g. revenue + fee split) | `contra_totals` split proportionally by each contra line's share of the total contra-side absolute amount, not duplicated in full to each |
| P3 | Multiple journal entries touching the same contra account within the period | Amounts accumulate into a single breakdown row per contra account |
| P4 | No cash accounts configured | `accounts=[]`, `total_net_change=0` |
| P5 | Cash account with no activity in the period | `opening == closing`, `net_change=0`, `breakdown=[]` |
| P6 | `opening_balance` computed as of `period_start - 1 day` | Verify the day-before boundary explicitly (activity exactly on `period_start` counted in the period, not in opening) |
| P7 | Breakdown rows sorted by `account_code` | Deterministic ordering in output |
| P8 | A cash-to-cash transfer (both lines are cash accounts) | Each cash account's own ledger shows the movement; verify the "contra" side attribution behaves sanely (contra_lines excludes only the *same line*, not same-account other lines — check whether a transfer between two cash accounts double counts or behaves as expected; this is worth a dedicated test given the comment about proportional attribution) |
| N1 | Division by `contra_total_abs` when a contra line's amount is exactly 0 | Guarded by `if contra_total_abs == 0: continue` — assert no `ZeroDivisionError` even in a constructed edge case |

## `report_service.get_generated_report`

| # | Case | Expected |
|---|------|----------|
| P1 | Exact match on `report_type`/`period_type`/`period_start`/`period_end` | Returns the row |
| N1 | No match (never generated) | Returns `None` |
| N2 | Match on 3 of 4 filters (e.g. wrong `period_end`) | Returns `None` — confirms all filters are ANDed, not fuzzy |

## `report_service.generate_reports`

| # | Case | Expected |
|---|------|----------|
| P1 | Reconciliation for the period is complete | Generates and persists `profit_loss`, `balance_sheet`, `cash_flow` `GeneratedReport` rows, returns all three |
| P2 | Regenerating for the same period (already has reports) | Existing rows updated in place (`data`, `generated_at`, `generated_by_user_id`), not duplicated — same primary keys reused |
| P3 | Regenerating after new activity was posted since the last generation | `data` reflects the *new* numbers (not stale) — confirms it always recomputes from `compute_*`, never reuses cached data |
| N1 | Reconciliation for the period is not complete | `ReportGenerationError("Reconciliation for this period is not complete")`, no `GeneratedReport` rows created or updated |
| P4 | `_serialize`: `Decimal` values | Serialized to 2-decimal-quantized strings (e.g. `Decimal("100")` → `"100.00"`) |
| P5 | `_serialize`: nested dicts/lists (as produced by `compute_cash_flow`'s `breakdown`) | Recursively serialized, structure preserved |
| P6 | `_serialize`: non-Decimal, non-dict, non-list values (strings, account codes) | Passed through unchanged |
