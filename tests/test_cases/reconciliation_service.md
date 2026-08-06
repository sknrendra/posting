# Test cases — `app/services/reconciliation_service.py`

Covers `get_cash_accounts`, `get_activity_range`, `list_periods`,
`get_reconciliation_rows`, `submit_statement_balance`, `confirm_reconciliation`,
`get_period_status`. Also exercises `app/utils/periods.py` indirectly (see
`periods_util.md` for that module's own direct unit tests).

`date.today()` appears repeatedly (`get_activity_range`, `_ensure_period_closed`,
`list_periods`'s "already closed" filter) — tests needing a deterministic
"today" should either freeze time (e.g. `freezegun`, if available) or
construct period boundaries relative to the real `date.today()` at test-run
time rather than hardcoding dates. Flag this as a dependency to confirm before
implementation (check `requirements-dev.txt`).

## `get_cash_accounts`

| # | Case | Expected |
|---|------|----------|
| P1 | Multiple accounts, some `is_cash_account=True`/`False`, some `is_active=True`/`False` | Only active cash accounts returned, ordered by `code` |
| P2 | No cash accounts exist | Returns `[]` |

## `get_activity_range`

| # | Case | Expected |
|---|------|----------|
| P1 | No journal entries exist at all | Returns `(today, today)` |
| P2 | Entries exist, all dated in the past | Returns `(min(entry_date), today)` — latest is clamped up to today even if last entry is older |
| P3 | An entry dated in the future exists | Returns `(min(entry_date), max_future_date)` — latest is *not* clamped down to today |

## `list_periods`

| # | Case | Expected |
|---|------|----------|
| P1 | Activity spanning multiple months, `period_type="monthly"` | One period per calendar month covering the range, only periods with `period_end < today` included |
| P2 | Current (not-yet-closed) period excluded | The in-progress month/quarter/year does not appear even though it overlaps activity |
| P3 | `period_type="quarterly"` / `"annual"` | Correct bucketing (delegates to `periods.list_periods` — cross-check against that module's direct tests) |
| P4 | No closed periods yet (e.g. all activity is in the current month) | Returns `[]` |

## `get_reconciliation_rows`

| # | Case | Expected |
|---|------|----------|
| P1 | Cash account with no existing `Reconciliation` row for the period | `status="open"`, `book_balance` computed live via `ledger_service.get_account_balance` |
| P2 | Cash account with an existing `open` reconciliation (statement submitted, not confirmed) | `book_balance` computed live (not from the stored row, since `recon.book_balance is None` until confirmed) |
| P3 | Cash account with a `completed` reconciliation | `book_balance` read from the stored `recon.book_balance` snapshot, not recomputed — even if new journal entries were posted for that account afterward (frozen at confirm-time) |
| P4 | Multiple cash accounts, only one has a reconciliation row | Rows list covers *all* cash accounts, reconciliation-less ones show `status="open"` |
| P5 | No cash accounts | Returns `[]` |

## `submit_statement_balance`

| # | Case | Expected |
|---|------|----------|
| P1 | First submission for a period/account | New `Reconciliation` row created with `status="open"` |
| P2 | Resubmission before confirming (correcting a typo) | Existing row's `statement_balance`/`submitted_by_user_id` updated in place, no duplicate row (unique constraint on period+account) |
| N1 | Period not yet closed (`period_end >= date.today()`) | `ReconciliationError("This period hasn't closed yet and cannot be reconciled")`, no row created/updated |
| N2 | Resubmitting after the reconciliation is already `status="completed"` | `ReconciliationError("This account is already reconciled for this period")` |
| P3 | `statement_balance` as a negative `Decimal` (overdrawn account) | Accepted — no sign validation in the service |

## `confirm_reconciliation`

| # | Case | Expected |
|---|------|----------|
| P1 | Open reconciliation exists, period closed | `status` flips to `completed`, `book_balance` snapshotted from `ledger_service.get_account_balance` as of `period_end`, `reconciled_by_user_id`/`reconciled_at` set |
| P2 | Confirming an already-`completed` reconciliation (idempotent re-confirm) | Returns the existing row unchanged (no error, no re-snapshot — verify `book_balance`/`reconciled_at` are NOT overwritten on a second confirm) |
| N1 | No reconciliation row exists yet (never submitted a statement balance) | `ReconciliationError("Enter a statement balance before confirming")` |
| N2 | Period not yet closed | `ReconciliationError("...hasn't closed yet...")`, checked *before* the "no row" check (order matters if both conditions are true) |

## `get_period_status`

| # | Case | Expected |
|---|------|----------|
| P1 | No cash accounts at all | `{"completed": 0, "total": 0, "is_complete": False}` — zero accounts is explicitly NOT complete (guards `total > 0`) |
| P2 | Cash accounts exist, none reconciled | `completed=0`, `is_complete=False` |
| P3 | Some but not all cash accounts reconciled for the period | `is_complete=False` |
| P4 | All cash accounts reconciled (`status="completed"`) for the period | `is_complete=True` |
| P5 | Reconciliations exist for a *different* period | Not counted toward this period's status |
| P6 | An `open` (submitted-but-not-confirmed) reconciliation | Not counted as completed |

## Integration-flavored

| # | Case | Expected |
|---|------|----------|
| X1 | Full lifecycle: submit → confirm → `report_service.generate_reports` gating | `generate_reports` for the same period succeeds only after `get_period_status(...).is_complete` is `True` (cross-reference `trial_balance_and_report_service.md`) |
| X2 | New cash account added *after* a period was fully reconciled | `get_period_status` for that already-closed period now reports `is_complete=False` again (new account has no reconciliation row) — documents that "complete" is dynamic, not a frozen flag |
