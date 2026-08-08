# Test cases — controllers: journal_entries, ledger_accounts, reconciliation, balance, reports

HTTP-level tests using the `client`/`test_user` fixtures from `tests/conftest.py`
(pattern established in `tests/test_controllers/test_invoice_flow.py`: GET a
form to harvest the `csrf_token` cookie, then POST with it). All routes below
require `require_login`; two-request auth/csrf case lists are collected once
at the bottom rather than repeated per-controller.

## `journal_entries_controller`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /journal-entries` | 200, lists entries (posted via service beforehand) |
| P2 | `GET /journal-entries/new` | 200, form pre-filled with 2 blank rows and today's date |
| P3 | `POST /journal-entries/new` with 2 balanced lines, valid CSRF | 303 redirect to `/journal-entries/{id}?ok=...` |
| P4 | `GET /journal-entries/{id}` after creating | 200, shows entry detail |
| P5 | `GET /journal-entries/{id}` for an entry created via `invoice_service` (has a related invoice) | 200, `related_invoice` populated in context (assert page references the invoice, e.g. its number) |
| N1 | `POST /journal-entries/new` with invalid `entry_date` (unparseable string) | 422, re-renders form with `error="Invalid entry date"`, submitted values echoed back |
| N2 | `POST /journal-entries/new` with blank `memo` | 422, `error="Memo is required"` |
| N3 | `POST /journal-entries/new` with unbalanced lines | 422, `error` contains the `UnbalancedEntryError` message |
| N4 | `POST /journal-entries/new` with a non-numeric `debit_amount` (e.g. `"abc"`) | 422 — `_parse_decimal` raises `InvalidJournalEntryError`, caught and rendered as form error |
| N5 | `POST /journal-entries/new` with an `account_id` that isn't a valid int (e.g. blank string that isn't filtered by the `if not row["account_id"]` guard, or non-numeric) | Confirm actual behavior: blank account_id rows are skipped (`continue`), but a garbage non-numeric account_id raises `ValueError` on `int(...)` — uncaught, surfaces as a 500. Document as a gap or assert current 500 behavior explicitly. |
| N6 | `GET /journal-entries/{id}` for a nonexistent id | 404 |
| N7 | `POST /journal-entries/new` with fewer than 2 non-blank line rows (e.g. only 1 account_id filled in) | Falls through to `journal_service.post_entry`'s own `InvalidJournalEntryError("...needs at least 2 lines")`, rendered as a form error (422) |
| N8 | `POST /journal-entries/new` missing/invalid `csrf_token` | 403 (via `verify_csrf` dependency) |
| N9 | `POST /journal-entries/new` unauthenticated (no session cookie) | Redirect/401 per `require_login`/`NotAuthenticatedError` behavior (check `app/exceptions.py` handler — confirm exact status) |

## `ledger_accounts_controller` — `/accounts`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /accounts` | 200, lists all accounts (active + inactive) |
| P2 | `GET /accounts/new` | 200 |
| P3 | `POST /accounts/new` with valid `account_type` | 303 redirect, account created |
| P4 | `GET /accounts/{id}/edit` for existing account | 200 |
| P5 | `POST /accounts/{id}/edit` | 303, name/description/is_cash_account updated |
| P6 | `POST /accounts/{id}/deactivate` then `/activate` | Each redirects 303, `is_active` toggles correctly |
| N1 | `POST /accounts/new` with `account_type` not in `ACCOUNT_TYPES` | 422 `HTTPException("Invalid account type")` — validated **before** calling the service (unlike `create_account`'s own `KeyError` path documented in account_service.md N2) |
| N2 | `GET /accounts/{id}/edit` for nonexistent id | 404 |
| N3 | `POST /accounts/{id}/edit` for nonexistent id | 404 |
| N4 | `POST /accounts/{id}/deactivate` for nonexistent id | 404 |
| N5 | `POST /accounts/new` with duplicate `code` | Uncaught `IntegrityError` from the service → 500 (no try/except around `account_service.create_account` here) — document as current behavior |

## `ledger_accounts_controller` — `/ledger`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /ledger` | 200, lists active accounts only |
| P2 | `GET /ledger/{account_id}` no date filters | 200, full running ledger shown |
| P3 | `GET /ledger/{account_id}?date_from=...&date_to=...` valid ISO dates | 200, filtered ledger |
| N1 | `GET /ledger/{account_id}` nonexistent account | 404 |
| N2 | `GET /ledger/{account_id}?date_from=not-a-date` | `date_cls.fromisoformat` raises `ValueError`, uncaught → 500. Document as a gap (no try/except like the POST journal-entry path has) |
| N3 | `GET /ledger/{inactive_account_id}` (deactivated account, direct link) | 200 still works — `/ledger` picker excludes inactive accounts from the list, but the detail route itself has no active-only guard; worth confirming intentional |

## `reconciliation_controller`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /reconciliation` default `period_type=monthly` | 200 |
| P2 | `GET /reconciliation?period_type=quarterly` / `annual` | 200 |
| P3 | `GET /reconciliation/{period_type}/{period_start}` for a closed period | 200, rows shown |
| P4 | `POST /reconciliation/{period_type}/{period_start}/{account_id}` valid statement balance | 303 redirect back to detail (no `error` query param) |
| P5 | `POST .../confirm` after a statement balance was submitted, period closed | 303, `?ok=Reconciled` |
| N1 | `GET /reconciliation?period_type=bogus` | 404 |
| N2 | `POST .../{account_id}` with non-numeric `statement_balance` | 303 redirect with `?error=Invalid statement balance` (caught `InvalidOperation`) |
| N3 | `POST .../{account_id}` for a period that hasn't closed yet | 303 redirect with `?error=...hasn't closed yet...` (service-level `ReconciliationError` caught) |
| N4 | `POST .../confirm` with no statement balance submitted yet | 303 redirect, `?error=Enter a statement balance before confirming` |
| N5 | `GET /reconciliation/{period_type}/{period_start}` with unparseable `period_start` | Uncaught `ValueError` from `fromisoformat` → 500 (no try/except) |
| N6 | `POST /reconciliation/{period_type}/{period_start}/{account_id}` for a nonexistent `account_id` | Service doesn't validate account existence before insert — likely `IntegrityError` on the FK, uncaught → 500; document actual behavior |

## `balance_controller`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /balance/trial-balance` no `as_of` | 200, uses today |
| P2 | `GET /balance/trial-balance?as_of=2026-01-01` | 200, filtered as of that date |
| N1 | `GET /balance/trial-balance?as_of=garbage` | Uncaught `ValueError` → 500 (no try/except around `fromisoformat`) |

## `reports_controller`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /reports` | 200, tables built per period type |
| P2 | `POST /reports/generate` with a fully-reconciled period | 303, `?ok=Reports generated` |
| P3 | `GET /reports/{report_type}/{period_type}/{period_start}` after generation | 200, shows the report data |
| N1 | `POST /reports/generate` with `period_type` not in `PERIOD_TYPES` | 404 |
| N2 | `POST /reports/generate` for a period not fully reconciled | 303, `?error=Reconciliation for this period is not complete` |
| N3 | `GET /reports/{report_type}/...` with `report_type` not in `REPORT_TYPES` | 404 |
| N4 | `GET /reports/{report_type}/{period_type}/{period_start}` before generation (no `GeneratedReport` row) | 404 |
| N5 | `POST /reports/generate` with unparseable `period_start` | Uncaught `ValueError` → 500 |

## Cross-cutting: auth & CSRF (apply to every POST/GET above)

| # | Case | Expected |
|---|------|----------|
| A1 | Any `GET`/`POST` without a valid session cookie | 303 redirect to `/login` (confirmed via `app/main.py`'s `NotAuthenticatedError` handler) |
| A2 | Any `POST` with `dependencies=[Depends(verify_csrf)]` and a missing `csrf_token` form field | 403 |
| A3 | Any such `POST` with a `csrf_token` that doesn't match the `csrf_token` cookie | 403 |
| A4 | Any such `POST` with an expired/nonexistent session but a technically-valid CSRF token | `require_login` still rejects with a 303 to `/login` — the CSRF dependency (`verify_csrf`, no auth check of its own) is declared at the route level via `dependencies=[...]` while `require_login` is a normal parameter dependency; confirm actual precedence empirically rather than assuming |
