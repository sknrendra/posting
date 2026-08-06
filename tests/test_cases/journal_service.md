# Test cases — `app/services/journal_service.py`

Covers `post_entry`, `list_entries`, `get_entry`. Not yet implemented — this is
the case inventory to review before writing `tests/test_services/test_journal_service.py`.

## `post_entry`

### Positive

| # | Case | Expected |
|---|------|----------|
| P1 | Two balanced lines (one debit, one credit, equal amounts) on an active account | Entry created, `created=True`, lines persisted in input order |
| P2 | More than two lines, debits split across several accounts but total debit == total credit | Entry created |
| P3 | `commit=False` | Entry is flushed (has `id`) but not committed; caller's own commit is what persists it — assert a rollback after `post_entry(commit=False)` leaves no row |
| P4 | `external_reference` set and not previously used | New entry created, `created=True` |
| P5 | `external_reference=None` (manual entries never dedupe) | Two separate `post_entry` calls with identical lines/memo both create separate entries |
| P6 | Repeat call with the same `external_reference` as an existing entry | Returns the **existing** entry unchanged, `created=False`, no new row, lines not re-validated (e.g. can pass garbage `lines=[]` on the retry and it still short-circuits before line validation) |
| P7 | `source` each of `"manual"`, `"webhook"`, `"invoice"` | Accepted (matches `SOURCES` check constraint) |
| P8 | `reverses_entry_id` pointing at an existing entry | Persisted on the new entry, no validation that lines mirror the reversed entry (service trusts caller) |
| P9 | `invoice_id` set | Persisted, retrievable via `invoice_service.get_invoice_by_journal_entry_id` |
| P10 | Line with only `debit_amount` set (`credit_amount=0`) | Accepted |
| P11 | Line with only `credit_amount` set (`debit_amount=0`) | Accepted |
| P12 | `created_by_user_id` and `created_by_api_key_id` both `None` (system/seed entries) | Accepted |

### Negative

| # | Case | Expected |
|---|------|----------|
| N1 | `lines=[]` | `InvalidJournalEntryError("A journal entry needs at least 2 lines")` |
| N2 | `lines` with exactly 1 line | Same as N1 |
| N3 | A line with both `debit_amount` and `credit_amount` nonzero | `InvalidJournalEntryError("...cannot have both a debit and a credit amount")` |
| N4 | A line with `debit_amount=0` and `credit_amount=0` | `InvalidJournalEntryError("...must have a nonzero debit or credit amount")` |
| N5 | A line referencing a nonexistent `account_id` | `InvalidJournalEntryError("Account {id} is not active")` |
| N6 | A line referencing an existing but `is_active=False` account | Same as N5 |
| N7 | Unbalanced entry (`total_debit != total_credit`) | `UnbalancedEntryError`, subclass of `InvalidJournalEntryError` — no row persisted |
| N8 | Off-by-cent imbalance (e.g. 100.00 debit vs 99.99 credit) via `Decimal`, not float | `UnbalancedEntryError` raised (confirms `Decimal` exactness — no float rounding masks a 1-cent gap) |
| N9 | Two lines referencing the *same* account_id, one debit one credit, that net to a nonzero balance | Still validated the same as any other line — not a special case, included for completeness |
| N10 | Duplicate `external_reference` used concurrently by two calls that both check-then-act (race) | Documented gap: `first()` + insert is not atomic — not enforced at the DB level by a unique constraint on a *retry*; the unique constraint on `external_reference` will raise `IntegrityError` on the second `db.commit()` if both slip past the initial check. Worth a test asserting the DB-level `UniqueConstraint` catches what the app-level check misses (simulate by inserting directly then calling `post_entry` with `commit=True` again for the same ref before the `existing` lookup would see it — or simplest: assert the unique constraint exists / a second raw insert with the same external_reference raises IntegrityError) |
| N11 | Negative `debit_amount` or `credit_amount` | Not rejected by `post_entry` itself, but the DB `CheckConstraint` `ck_journal_lines_nonzero`/`ck_journal_lines_one_sided` should reject at flush — assert `IntegrityError` (or whatever SQLAlchemy raises) on commit for a negative-amount line, since neither Python-side check inspects sign |

## `list_entries`

| # | Case | Expected |
|---|------|----------|
| L1 | Multiple entries with different `entry_date`/`id` | Returned ordered by `entry_date desc, id desc` |
| L2 | More entries than `limit` | Only `limit` rows returned |
| L3 | Default `limit=200` | No `limit` kwarg needed for < 200 rows |
| L4 | No entries exist | Returns `[]` |

## `get_entry`

| # | Case | Expected |
|---|------|----------|
| G1 | Existing `entry_id` | Returns the `JournalEntry` with `.lines` populated |
| G2 | Nonexistent `entry_id` | Returns `None` (no exception) |

## Cross-cutting / integration-flavored

| # | Case | Expected |
|---|------|----------|
| X1 | Full round trip: post a balanced entry, then `get_entry` it back | Lines match what was submitted, `lines` ordered by `JournalLine.id` (insertion order) regardless of input line order |
| X2 | `Decimal` precision preserved through `SQLiteDecimal(2)` storage (e.g. `Decimal("100.10")` in, `Decimal("100.10")` out, not `100.1` or float-drifted) | Exact equality after refresh from DB |
