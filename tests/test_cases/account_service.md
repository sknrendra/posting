# Test cases — `app/services/account_service.py`

Covers `list_accounts`, `get_account`, `create_account`, `update_account`,
`set_active`, `seed_default_chart_of_accounts`.

Note: `conftest.py` already seeds the default chart of accounts once at test
session start and never deletes `accounts` between tests (`_clean_slate` only
clears invoices/journal entries/users). Tests here must create their own
accounts with unique `code`s rather than assuming a pristine accounts table.

## `list_accounts`

| # | Case | Expected |
|---|------|----------|
| P1 | `include_inactive=True` (default) | Returns both active and inactive accounts |
| P2 | `include_inactive=False` | Only `is_active=True` accounts returned |
| P3 | Ordering | Sorted by `code` ascending, e.g. `"1000"` before `"2000"` |
| P4 | Seeded default chart present | All 11 `DEFAULT_CHART_OF_ACCOUNTS` codes appear at minimum |

## `get_account`

| # | Case | Expected |
|---|------|----------|
| P1 | Existing id | Returns the `Account` |
| N1 | Nonexistent id | Returns `None` |

## `create_account`

| # | Case | Expected |
|---|------|----------|
| P1 | `account_type="asset"` | `normal_balance` auto-set to `"debit"` |
| P2 | `account_type="liability"` | `normal_balance` auto-set to `"credit"` |
| P3 | `account_type="equity"` | `normal_balance="credit"` |
| P4 | `account_type="revenue"` | `normal_balance="credit"` |
| P5 | `account_type="expense"` | `normal_balance="debit"` |
| P6 | `is_cash_account=True` | Persisted; account later appears in `reconciliation_service.list_reconcilable_accounts` |
| P7 | `description=None` | Persisted as `NULL`, no error |
| P8 | New account defaults `is_active=True` | Immediately visible in `list_accounts(include_inactive=False)` |
| N1 | Duplicate `code` | `IntegrityError` from the `unique=True` constraint on `Account.code` (not caught/translated by the service — assert the raw DB exception propagates) |
| N2 | Invalid `account_type` not in `ACCOUNT_TYPES` (e.g. `"bogus"`) | `KeyError` from `TYPE_NORMAL_BALANCE[account_type]` lookup **before** any DB constraint is hit — service has no explicit validation here, this documents the actual failure mode |

## `update_account`

| # | Case | Expected |
|---|------|----------|
| P1 | Update `name`, `description`, `is_cash_account` on an existing account | Fields updated, `code`/`account_type`/`normal_balance` untouched (not settable via this function) |
| P2 | `description=None` clears a previously-set description | Persisted as `NULL` |
| P3 | Toggling `is_cash_account` True→False | Account drops out of `list_reconcilable_accounts` results |

## `set_active`

| # | Case | Expected |
|---|------|----------|
| P1 | Deactivate an active account | `is_active=False`; excluded from `list_accounts(include_inactive=False)` |
| P2 | Reactivate | `is_active=True`; reappears |
| P3 | Deactivating an account that already has posted journal lines | Succeeds (deactivation doesn't touch historical lines) — but subsequent attempts to post *new* entries against it must fail via `journal_service.post_entry`'s active-account check (covered in journal_service.md N6; cross-referenced here for the account lifecycle angle) |
| N1 | Deactivating an account referenced by `invoice_settings` mappings (e.g. the configured AR account) | No guard in this service — succeeds, silently breaking invoice posting downstream (`MissingAccountMappingError`/`InvalidJournalEntryError` surfaces later, not here). Worth a test documenting this gap explicitly. |

## `seed_default_chart_of_accounts`

| # | Case | Expected |
|---|------|----------|
| P1 | Called on an empty `accounts` table | All 11 default accounts created with correct type/normal_balance/is_cash_account |
| P2 | Called twice in a row (idempotency) | Second call creates zero new rows — checked per-code, not per-table-emptiness |
| P3 | Table already has *some* of the default codes (e.g. pre-seeded by a migration) but not all | Only the missing codes get created; existing rows untouched |
| P4 | Table has unrelated accounts with different codes | Default accounts still get added alongside them |
