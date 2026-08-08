# Test cases — invoice module gaps

Cross-referenced against existing coverage in `tests/test_services/test_invoice_calc.py`,
`test_invoice_service.py`, `test_invoice_numbering.py`, and
`tests/test_controllers/test_invoice_flow.py`. This file lists what's **not**
covered yet — do not duplicate the cases already in those files.

## `invoice_calc.resolve_line` — untested branches

| # | Case | Expected | Why it's a gap |
|---|------|----------|-----------------|
| N1 | Negative `quantity` or `rate` | `line_gross` goes negative; falls into the `amount < 0` clamp only if the *discount* ends up negative, not the gross itself — gross itself is never clamped. Document actual (possibly surprising) behavior: a negative-gross line produces a negative `line_amount` with no guard. | No existing test exercises negative inputs at all |
| N2 | `discount_percentage > 100` in percent mode | `amount > line_gross` after computing `line_gross * pct / 100` → clamped to `line_gross`, `pct` forced to `"100.00"` | Only in-range percentages tested so far |
| N3 | `discount_percentage` with more than 2 decimal places (e.g. `33.333`) | Confirm rounding behavior of the percent→amount conversion (`round_rupiah`, whole-unit) vs. the amount→percent conversion (`.quantize(Decimal("0.01"))`) — these use *different* precision, worth a dedicated test showing the asymmetry | Existing idempotency tests use "nice" round numbers |
| N4 | `discount_amount` exactly equal to `line_gross` (100% discount via amount mode) | `line_amount == 0`, not the `> line_gross` clamp path (boundary, not over) | Only the "amount exceeds gross" case is tested (`test_discount_amount_clamped_to_line_gross` uses discount > gross, not ==) |
| P1 | `round_rupiah` with a value already an exact whole number | No change | Only `.5`/`.4` boundary cases tested |
| P2 | `resolve_header` with an empty `line_results` list | All totals `0`, `balance_due = -deposit_applied` if a deposit was somehow set with no lines | Not tested — `create_draft`/`post_invoice` with zero lines is covered at the service level (`InvoiceValidationError` on `post_invoice`), but `resolve_header([], ...)` itself isn't tested in isolation |
| N5 | `tax_rate` producing a fractional tax that rounds against the subtotal (verify `round_rupiah` uses `ROUND_HALF_UP` consistently, e.g. subtotal `50` at `tax_rate=1` → `0.50` → rounds to `1`, not `0`) | `tax_amount` rounds half-up | Existing header test uses `tax_rate=11` on a large subtotal where the half-up boundary never gets hit |

## `invoice_service` — untested branches

| # | Case | Expected | Why it's a gap |
|---|------|----------|-----------------|
| N1 | `update_draft` on an invoice with `status != "draft"` | Covered already (`test_editing_posted_invoice_fails`) — **skip**, listed only to confirm no duplicate needed | — |
| P1 | `update_draft` replacing all lines (fewer lines, different lines entirely) | `invoice.lines.clear()` then rebuilt — old `InvoiceLine` rows deleted (cascade), new ones inserted with fresh `line_number`s starting at 1 | Not tested — only `create_draft`/posting is covered, never a genuine multi-line edit |
| N2 | `_recalculate_header`: `deposit_applied > total` | `InvoiceValidationError("Deposit applied cannot exceed the invoice total")` | No test at all for this guard — easy to trigger via `create_draft` or `update_draft` with a large `deposit_applied` and small line total |
| P2 | `list_invoices` with `status` filter | Only matching-status invoices returned | Not tested (only single-invoice flows exist) |
| P3 | `list_invoices` with `search` matching `invoice_number` (case-insensitive `ilike`) | Matches | Not tested |
| P4 | `list_invoices` with `search` matching `customer_name` (case-insensitive) | Matches | Not tested |
| P5 | `list_invoices` with `search` matching neither field | `[]` | Not tested |
| P6 | `list_invoices` no filters | All invoices, ordered `invoice_date desc, id desc` | Not tested |
| N3 | `post_invoice` with tax but no `tax_payable_account_id` configured | `MissingAccountMappingError` mentioning tax | Only the AR-missing case is tested (`test_missing_account_mapping_blocks_posting`); the tax and unearned-revenue variants aren't |
| N4 | `post_invoice` with a deposit but no `unearned_revenue_account_id` configured | `MissingAccountMappingError` mentioning unearned revenue | Same gap |
| N5 | `post_invoice` with no `service_revenue_account_id` configured | `MissingAccountMappingError` mentioning service revenue | Same gap |
| P7 | `post_invoice` where `total_discount == 0` even though `sales_discounts_account_id` **is** configured | Falls to `method="net"` (the `and invoice.total_discount > 0` condition), not `"gross"` | The gross-method test always has a nonzero discount; the "configured but unused" branch isn't verified |
| P8 | `_line_or_none` filters out zero-amount lines (e.g. no deposit → no unearned-revenue line in the journal entry) | Verify the posted journal entry has exactly the expected line count, not padded with zero-amount lines (zero-amount lines would fail `ck_journal_lines_nonzero` anyway if they slipped through — this test proves they don't) | Implicitly covered by balance assertions but never asserts line *count* |
| N6 | `void_invoice` where the original `JournalEntry` was somehow deleted (`invoice.journal_entry_id` orphaned) | `InvoiceError("The invoice's original journal entry could not be found")` | Not tested — would need to delete the entry directly via `db` in the test |
| P9 | `get_invoice_by_journal_entry_id` matching via `void_journal_entry_id` (not just `journal_entry_id`) | Returns the invoice when looked up by its *void* entry's id | Not tested — only implicitly exercised through the controller's journal-entry-detail page, not asserted directly |
| N7 | `create_draft`/`update_draft` with an `InvoiceLineInput` list containing duplicate descriptions or zero-quantity lines | No validation exists — should succeed, `line_gross=0` per `resolve_line`'s zero-gross branch | Not tested, documents "any input is a valid draft line" |

## `invoice_settings_service` — entirely untested

| # | Case | Expected |
|---|------|----------|
| P1 | `get_settings` first call ever (no row exists) | Creates and returns a default singleton with `id=1` |
| P2 | `get_settings` subsequent calls | Returns the same row, doesn't recreate |
| P3 | `update_settings` with all mapping fields `None` | Succeeds, no validation triggered (`_validate_mapping` short-circuits on `None`) |
| N1 | `update_settings` with `ar_account_id` pointing at a nonexistent account | `InvalidAccountMappingError("...account not found")` |
| N2 | `update_settings` with `ar_account_id` pointing at an account whose `account_type` isn't in the expected set (e.g. a revenue account mapped to AR, which expects `asset`) | `InvalidAccountMappingError` naming the expected type(s) and the actual account |
| P4 | `update_settings` with `sales_discounts_account_id` pointing at either a `revenue` **or** `expense` account | Both accepted (only mapping with >1 valid type) |
| N3 | `update_settings` validation failure on one field | Confirm **no partial update** — none of the other fields get persisted either, since validation runs for all fields before any assignment (verify by checking `settings.company_name` etc. are unchanged after a failed call) |
| P5 | `backfill_default_mappings` on a settings row with all mappings already set | No-op, `changed=False` path, no commit issued (can't directly assert no-commit, but assert values unchanged) |
| P6 | `backfill_default_mappings` where the expected default account codes (`1100`, `4000`, `2020`, `2030`) don't exist yet | Leaves those mapping fields `None`, no crash |
| P7 | `backfill_default_mappings` fills only the unset fields, leaving an explicitly-set field (even if it points elsewhere) untouched | Never overwrites an explicit choice |
| P8 | `compose_payment_instructions` with all fields set | All parts joined with newlines in the fixed field order (bank, account name, account number, methods, reference instruction) |
| P9 | `compose_payment_instructions` with all fields `None`/blank | Returns `None` |
| P10 | `compose_payment_instructions` with only some fields set | Only those parts included, no blank lines |
| P11 | `save_logo` writes the file to `LOGO_UPLOAD_DIR` with a normalized name (`invoice-logo{suffix}`) | File exists on disk, `settings.company_logo_path` set to the web path; **note**: caller must `db.commit()` separately — service doesn't commit |
| P12 | `save_logo` with a filename that has no extension | Falls back to `.png` suffix |
| P13 | `save_logo` called twice with different logos | Second call overwrites the same `invoice-logo{suffix}` disk file (filename is derived from the *new* upload's suffix, not a hash — a `.jpg` upload followed by a `.png` upload leaves both files on disk but `company_logo_path` only points at the most recent; worth asserting this doesn't silently serve a stale logo) |

## `invoice_pdf_service` — entirely untested at the unit level

(`test_invoice_flow.py` covers PDF generation only via the full HTTP round trip and asserts `%PDF` magic bytes — no test isolates the service's own branching.)

| # | Case | Expected |
|---|------|----------|
| P1 | `render_invoice_pdf` for a `draft` invoice | Uses **live** `invoice_settings_service.compose_payment_instructions(settings)`, not any stored snapshot |
| P2 | `render_invoice_pdf` for a `posted`/`void` invoice | Uses the **frozen** `invoice.snapshot_payment_instructions`, even if settings have changed since posting |
| P3 | `_logo_file_uri` with no `company_logo_path` configured | Falls back to the default app icon, `is_default=True` |
| P4 | `_logo_file_uri` with `company_logo_path` set and the file present on disk | Returns that file's `file://` URI, `is_default=False` |
| P5 | `_logo_file_uri` with `company_logo_path` set but the file missing from disk (e.g. deleted externally) | Falls back to the default icon rather than raising |
| N1 | `_logo_file_uri` when even the default icon file is missing (contrived: point `_DEFAULT_LOGO_PATH` at a nonexistent path via monkeypatch) | Returns `(None, False)`, template must tolerate a `None` logo_url without crashing |

## `invoice_controller` — untested HTTP branches

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /invoices?status=draft` / `?search=Acme` | 200, filtered list (exercises `list_invoices` query params end-to-end) |
| P2 | `POST /invoices/new` with `action=post` (create-and-post in one step) | 303 to detail with `?ok=Invoice saved`; invoice ends up `status="posted"`, not left as draft |
| N1 | `POST /invoices/new` with `action=post` but validation fails at the `post_invoice` step (e.g. blank customer name) | 422, re-renders the **create** form (not silently losing the draft — confirm whether a draft was already committed before the post attempt failed, i.e. does a half-succeeded "create draft, fail to post" leave an orphan draft row?) |
| P3 | `GET /invoices/{id}/edit` for a draft | 200, form pre-filled from the invoice's current lines |
| N2 | `GET /invoices/{id}/edit` for a non-draft invoice | 303 redirect to detail with `?error=Only draft invoices can be edited` (not a 4xx — a redirect) |
| P4 | `POST /invoices/{id}/edit` normal edit, `action=draft` | 303, invoice updated, still draft |
| P5 | `POST /invoices/{id}/edit` with `action=post` | 303, invoice updated AND posted in one request |
| N3 | `POST /invoices/{id}/edit` for a nonexistent id | 404 |
| N4 | `POST /invoices/{id}/edit` with an invalid date string in any date field | 422, `InvoiceValidationError` from `_parse_date`, form re-rendered with the invoice context (note: `invoice` in the error-path context is the *original* fetched object, not `None` like the create path — worth asserting the template gets the right one) |
| N5 | `POST /invoices/{id}/post` for a non-draft invoice | 303 with `?error=Only draft invoices can be posted` |
| N6 | `POST /invoices/{id}/void` with blank `void_reason` | 303 with `?error=A void reason is required` |
| N7 | `POST /invoices/{id}/void` for a non-posted invoice | 303 with `?error=Only posted invoices can be voided` |
| N8 | `GET /invoices/{id}/pdf` for a nonexistent id | 404 |
| P6 | `GET /invoices/{id}/pdf` for a posted invoice | `Content-Disposition` filename uses `invoice.invoice_number`, not `draft-invoice-{id}` |
| P7 | `GET /invoices/{id}/pdf` for a draft invoice | Filename falls back to `draft-invoice-{id}` (no invoice_number yet) |
| P8 | `GET /invoices/settings` | 200, shows current settings + active accounts for the mapping dropdowns |
| P9 | `POST /invoices/settings` valid mapping account ids | 303, `?ok=Invoice settings saved` |
| N9 | `POST /invoices/settings` with an invalid account mapping | 422, `error` rendered (from `InvalidAccountMappingError`) |
| N10 | `POST /invoices/settings` with a non-integer `default_payment_terms_days` | Uncaught `ValueError` from bare `int(...)` (no try/except, unlike `_parse_decimal`/`_parse_date`) → 500. Document as a gap. |
| P10 | `POST /invoices/settings` with a logo file upload | 303, file saved, `company_logo_path` updated and committed |
| P11 | `POST /invoices/settings` with no logo field / empty upload | No crash, `isinstance(logo, UploadFile) and logo.filename` guard skips saving |
| P12 | `POST /invoices/settings` account mapping validation failure **and** a logo upload in the same request | Logo is NOT saved (validation error returns before reaching the logo-handling block) — worth asserting explicitly since the code path could regress into saving files despite a failed validation |
