# Test cases — `app/services/reconciliation_service.py`

> **Rewritten 2026-08-07**: the original version of this document described a
> period-based reconciliation flow (`get_cash_accounts`, `submit_statement_balance`,
> `confirm_reconciliation`, `get_reconciliation_rows`) that was replaced by an
> itemized, statement-anchored flow in the `claude/posting-reconciliation-update-0s2b5n`
> merge. This is the updated case list for the current implementation. All
> cases below are implemented in `tests/test_services/test_reconciliation_service.py`
> and `tests/test_controllers/test_reconciliation_flow.py`.

Reconciliations now work per-account, per-statement: `start_reconciliation`
opens a working reconciliation anchored to a statement date and ending
balance, individual journal lines get toggled cleared/uncleared against it
(`toggle_line_cleared`), missing transactions can be added mid-flow
(`add_missing_transaction`), and `complete_reconciliation` locks the cleared
lines in once the computed ending balance matches the statement — with
`undo_reconciliation` (admin-only) available to reopen a completed one.
`get_activity_range`/`list_periods`/`get_period_status` remain as a thin
compatibility layer for `reports_controller`'s calendar-period gating, now
redefined against the new completed-reconciliation history.

## Continuity: `get_latest_completed`, `get_expected_starting_balance`, `verify_reconciliation_integrity`, `get_continuity_warning`

| # | Case | Expected |
|---|------|----------|
| P1 | No prior completed reconciliation for the account | `get_expected_starting_balance` returns `0`, `get_continuity_warning` returns `None` |
| P2 | A prior completed reconciliation exists | Starting balance = that reconciliation's `statement_ending_balance` |
| P3 | A completed reconciliation's locked lines still sum correctly against its recorded starting/ending balance | `verify_reconciliation_integrity` returns `0`, `get_continuity_warning` returns `None` |
| N1 | A completed reconciliation's locked-line linkage was altered after completion (simulated data drift, not reachable via the normal API) | `verify_reconciliation_integrity` returns a nonzero delta; `get_continuity_warning` returns a message naming the reconciliation id |

## `start_reconciliation`

| # | Case | Expected |
|---|------|----------|
| P1 | First reconciliation ever for an account | `starting_balance == 0`, `warning is None` |
| P2 | Second reconciliation, prior one completed | `starting_balance` == prior's `statement_ending_balance` |
| N1 | Account doesn't exist or is inactive | `ReconciliationError("Account not found or inactive")` |
| N2 | Another reconciliation is already `in_progress` for the same account | `ReconciliationError("An in-progress reconciliation already exists...")` — also enforced at the DB level by the partial unique index on `(account_id) WHERE status='in_progress'` |
| N3 | `statement_date` is on/before the latest completed reconciliation's `statement_date` | `ReconciliationError("Statement date must be after...")` |
| N4 | `statement_date` duplicates an existing reconciliation's date for this account | `ReconciliationError("...already exists")` — backed by the `uq_reconciliations_account_statement_date` unique constraint |

## Working a reconciliation: `get_clearable_lines`, `toggle_line_cleared`, `add_missing_transaction`, `compute_difference`

| # | Case | Expected |
|---|------|----------|
| P1 | Lines dated on/before the statement date, not yet locked into any reconciliation | Appear in `get_clearable_lines` |
| P2 | A line locked into a *different* (earlier, completed) reconciliation | Excluded from `get_clearable_lines` for this reconciliation |
| P3 | An outstanding (uncleared) line from a prior period | Still shows up as clearable in the next reconciliation ("carries forward") |
| P4 | Toggle a line cleared, then uncleared | `cleared_status` flips both ways |
| N1 | Toggle a line on a reconciliation that isn't `in_progress` | `ReconciliationError("Only an in-progress reconciliation can be edited")` |
| N2 | Toggle a nonexistent `line_id` | `ReconciliationError("...not found for this account")` |
| N3 | Toggle a line belonging to a different account | Same "not found" error (account-scoped lookup) |
| N4 | Toggle a line dated after the statement date | `ReconciliationError("...dated after the statement date")` |
| N5 | Toggle a line already locked into a *different* reconciliation | `ReconciliationError("...locked into a different reconciliation")` |
| P5 | `add_missing_transaction` with `movement="withdrawal"` | Posts a balanced 2-line entry; the reconciled account's line is on the credit-equivalent side and auto-marked `cleared`, the counter line stays `uncleared` |
| P6 | `add_missing_transaction` with `movement="deposit"` | Reconciled account's line debits (for a debit-normal account) — sign wiring verified independently of the withdrawal case |
| N6 | `movement` not in `("deposit", "withdrawal")` | `ReconciliationError("Movement must be one of...")` |
| N7 | `amount <= 0` | `ReconciliationError("...greater than zero")` |
| N8 | `counter_account_id == reconciliation.account_id` | `ReconciliationError("Pick a different account...")` |
| N9 | `entry_date` after the statement date | `ReconciliationError("...dated after the statement date")` |
| N10 | Reconciliation not `in_progress` | `ReconciliationError("Only an in-progress reconciliation can be edited")` |
| P7 | `compute_difference` on a balanced reconciliation | `is_balanced=True`, `difference == 0` |
| P8 | `compute_difference` with cleared total short of the statement balance | `is_balanced=False`, `difference` reflects the shortfall |

## `complete_reconciliation`

| # | Case | Expected |
|---|------|----------|
| P1 | Cleared lines sum to exactly the statement ending balance | `status="completed"`, `completed_by_user_id`/`completed_at` set, cleared lines' `reconciliation_id` locked to this reconciliation |
| N1 | Out of balance | `ReconciliationError("...out of balance by {difference}")`, nothing locked/completed |
| N2 | Reconciliation not `in_progress` (e.g. already completed) | `ReconciliationError("Only an in-progress reconciliation can be completed")` |

## `undo_reconciliation`

| # | Case | Expected |
|---|------|----------|
| P1 | Completed reconciliation, no later completed ones for the account | `status="undone"`, all its locked lines' `reconciliation_id` cleared back to `None`, `affected_later_reconciliations == []` |
| P2 | Later completed reconciliations exist for the same account | Returned (not touched/undone themselves) in `affected_later_reconciliations`, as a warning surface for the caller |
| P3 | A line unlocked by undo | Reappears in `get_clearable_lines` for a subsequent reconciliation, `cleared_status` unchanged (still `cleared`, just unlocked) |
| N1 | Reconciliation not `completed` (e.g. still `in_progress`) | `ReconciliationError("Only a completed reconciliation can be undone")` |

## `get_history`

| # | Case | Expected |
|---|------|----------|
| P1 | Multiple reconciliations (any status) for an account | Ordered by `statement_date desc` |

## `list_reconcilable_accounts`

| # | Case | Expected |
|---|------|----------|
| P1 | Mix of cash/non-cash, active/inactive accounts | Only active cash accounts returned, ordered by `code` |

## Reports-controller compatibility: `get_activity_range`, `list_periods`, `get_period_status`

| # | Case | Expected |
|---|------|----------|
| P1 | `get_activity_range`/`list_periods` | Unchanged behavior from before the rewrite — see git history for the original case list; still gates `reports_controller`'s period picker |
| P2 | `get_period_status`: no reconcilable accounts | `{"completed": 0, "total": 0, "is_complete": False}` |
| P3 | `get_period_status`: an account has a completed reconciliation with `statement_date >= period_end` | Counts as reconciled for that period |
| N1 | `get_period_status`: a completed reconciliation with `statement_date < period_end` | Does **not** count — the period isn't covered by a statement dated on/after its end. Documents a real behavior shift from the old model: this is coarser than before (doesn't verify every line strictly inside the calendar period was cleared, since outstanding items can legitimately roll into a later statement) |
