# Test cases — auth, api_key, user, settings, webhook

Covers `app/services/auth_service.py`, `app/services/api_key_service.py`,
`app/services/user_service.py`, `app/utils/security.py`, `app/dependencies.py`,
`app/controllers/auth_controller.py`, `app/controllers/settings_controller.py`,
`app/controllers/webhook_controller.py`, `app/middleware.py` (CSRF cookie).

## `app/utils/security.py`

| # | Case | Expected |
|---|------|----------|
| P1 | `hash_password` / `verify_password` round trip, correct password | `True` |
| N1 | `verify_password` wrong password | `False` (caught `VerifyMismatchError`, no exception propagates) |
| P2 | `generate_token` produces unique values across calls | Two calls differ |
| P3 | `sha256_hex` deterministic | Same input → same output, matches a known hash of a fixed string |
| P4 | `generate_api_key` returns `(token, prefix, hash)` | `prefix == token[:12]`, `token.startswith("pk_")`, `hash == sha256_hex(token)` |
| P5 | `constant_time_eq` equal strings | `True` |
| N2 | `constant_time_eq` different strings, same length | `False` |
| N3 | `constant_time_eq` different lengths | `False`, no exception |

## `auth_service`

| # | Case | Expected |
|---|------|----------|
| P1 | `authenticate` correct email + password, active user | Returns the `User` |
| N1 | `authenticate` wrong password | Returns `None` |
| N2 | `authenticate` nonexistent email | Returns `None` |
| N3 | `authenticate` correct credentials but `is_active=False` | Returns `None` (filtered out of the query entirely) |
| P2 | `create_session` | Returns a token; a `Session` row exists with matching `token_hash = sha256_hex(token)`, `expires_at = now + SESSION_LIFETIME` |
| P3 | `get_user_for_session_token` valid, unexpired token | Returns the `User`, and side-effects: `last_seen_at`/`expires_at` both bumped forward (sliding expiration) |
| N4 | `get_user_for_session_token` unknown token | Returns `None` |
| N5 | `get_user_for_session_token` expired token (`expires_at < now`) | Returns `None`, and does **not** slide the expiration forward (verify session row untouched) |
| N6 | `get_user_for_session_token` valid token but user has since been deactivated | Returns `None` |
| N7 | `get_user_for_session_token` valid token but user has since been deleted | Returns `None` (`db.get` returns `None`, no crash) |
| P4 | `destroy_session` valid token | Session row deleted, subsequent `get_user_for_session_token` with that token returns `None` |
| P5 | `destroy_session` unknown/already-destroyed token | No error, no-op delete (0 rows matched) |
| P6 | `create_user` | `User` created with `is_active=True`, `is_admin` defaults `False`, `password_hash` is not the plaintext password |
| P7 | `create_user(is_admin=True)` | `is_admin=True` |
| N8 | `create_user` with an email that already exists | `IntegrityError` if `email` has a unique constraint (check `app/models/user.py`) — assert actual DB behavior |
| P8 | `set_password` | `password_hash` changes; old password no longer verifies, new one does |

## `api_key_service`

| # | Case | Expected |
|---|------|----------|
| P1 | `create_api_key` | Returns `(ApiKey, plaintext_token)`; stored `key_hash` matches `sha256_hex(token)`, `key_prefix == token[:12]`, `revoked_at is None` |
| P2 | `list_api_keys` | Ordered by `created_at desc` |
| P3 | `get_api_key` existing id | Returns the row |
| N1 | `get_api_key` nonexistent id | Returns `None` |
| P4 | `revoke` | Sets `revoked_at`; a subsequent `verify_token` with that key's token returns `None` |
| P5 | `verify_token` valid, non-revoked token | Returns the `ApiKey`, and bumps `last_used_at` |
| N2 | `verify_token` unknown token | Returns `None` |
| N3 | `verify_token` revoked token (`revoked_at is not None`) | Returns `None` even though the hash still matches a row |
| N4 | `verify_token` empty string | Returns `None`, no crash |

## `user_service`

| # | Case | Expected |
|---|------|----------|
| P1 | `list_users` | Ordered by `email` |
| P2 | `get_user` existing / `get_user_by_email` existing | Returns the row |
| N1 | `get_user` / `get_user_by_email` nonexistent | Returns `None` |
| P3 | `set_active(False)` then `(True)` | Toggles correctly |

## `app/dependencies.py`

| # | Case | Expected |
|---|------|----------|
| P1 | `require_login` valid session cookie | Returns the `User` |
| N1 | `require_login` no cookie | Raises `NotAuthenticatedError` |
| N2 | `require_login` cookie present but invalid/expired token | Raises `NotAuthenticatedError` |
| P2 | `require_admin` user with `is_admin=True` | Returns the user |
| N3 | `require_admin` user with `is_admin=False` | `HTTPException(403, "Admin access required")` |
| P3 | `require_api_key` valid `Authorization: Bearer <token>` header | Returns the `ApiKey` |
| N4 | `require_api_key` missing header | `HTTPException(401, "Missing or invalid Authorization header")` |
| N5 | `require_api_key` header present but not `Bearer `-prefixed (e.g. `Basic ...`) | Same 401 |
| N6 | `require_api_key` `Bearer ` with an invalid/revoked token | `HTTPException(401, "Invalid or revoked API key")` |
| N7 | `require_api_key` case-insensitive scheme (`bearer` lowercase) | Accepted — check uses `.lower().startswith("bearer ")` |
| P4 | `verify_csrf` matching cookie + form token | Passes (no exception) |
| N8 | `verify_csrf` missing cookie | `HTTPException(403)` |
| N9 | `verify_csrf` missing form field | `HTTPException(403)` |
| N10 | `verify_csrf` mismatched values | `HTTPException(403)` |

## `auth_controller`

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /login` | 200 |
| P2 | `POST /login` valid credentials + CSRF | 303 to `/`, `posting_session` cookie set (httponly, matches `settings.session_cookie_name`) |
| N1 | `POST /login` wrong password | 401, re-renders form with `error="Invalid email or password"`, `email` echoed back but not password |
| N2 | `POST /login` nonexistent email | Same 401 as N1 (no user enumeration via different error message) |
| N3 | `POST /login` deactivated user, correct password | Same 401 (authenticate returns `None` for inactive users) |
| N4 | `POST /login` missing/invalid CSRF | 403 |
| P3 | `POST /logout` while logged in, valid CSRF | 303 to `/login`, session cookie deleted, session row destroyed server-side (verify a reused pre-logout token no longer authenticates) |
| P4 | `POST /logout` with no session cookie at all | 303 to `/login`, no crash (guarded by `if token:`) |

## `settings_controller`

All routes except `/settings/password` require `require_admin`.

| # | Case | Expected |
|---|------|----------|
| P1 | `GET /settings/api-keys` as admin | 200, lists keys |
| N1 | `GET /settings/api-keys` as non-admin logged-in user | 403 |
| N2 | `GET /settings/api-keys` unauthenticated | 303 to `/login` (auth checked before admin check, since `require_admin` depends on `require_login`) |
| P2 | `POST /settings/api-keys` as admin, valid CSRF | 303, new key created, `flash_new_api_key` cookie set with the plaintext token |
| P3 | `GET /settings/api-keys` immediately after creating one (cookie present) | 200, `new_token` shown once, cookie deleted in the response (verify it's gone on a follow-up request) |
| P4 | `POST /settings/api-keys/{id}/revoke` as admin | 303, key revoked |
| N3 | `POST /settings/api-keys/{id}/revoke` nonexistent id | 404 |
| P5 | `GET /settings/users` as admin | 200 |
| P6 | `POST /settings/users` valid new email, password >= 8 chars | 303, `?ok=User created` |
| N4 | `POST /settings/users` password < 8 chars | 303, `?error=Password must be at least 8 characters`, no user created |
| N5 | `POST /settings/users` duplicate email (case-insensitive: service lowercases+strips first) | 303, `?error=Email already in use` |
| P7 | `POST /settings/users` email with mixed case / surrounding whitespace | Normalized to `strip().lower()` before the duplicate check and creation |
| P8 | `POST /settings/users/{id}/deactivate` targeting another user | 303, `?ok=User deactivated` |
| N6 | `POST /settings/users/{id}/deactivate` targeting **self** (`user_id == current_user.id`) | 303, `?error=You cannot deactivate your own account`, no change made |
| N7 | `POST /settings/users/{id}/deactivate` nonexistent id | 404 |
| P9 | `POST /settings/users/{id}/activate` | 303, `?ok=User activated` |
| P10 | `GET /settings/password` — only needs `require_login`, works for non-admin | 200 |
| P11 | `POST /settings/password` correct current password, matching new/confirm, >= 8 chars | 303, `?ok=Password updated`; old password no longer works, new one does |
| N8 | `POST /settings/password` wrong current password | 401, re-rendered form with error, password unchanged |
| N9 | `POST /settings/password` new != confirm | 422, `error="New passwords do not match"` |
| N10 | `POST /settings/password` new password < 8 chars | 422, `error="New password must be at least 8 characters"` |

## `webhook_controller` (`/api/v1`, API-key authenticated)

| # | Case | Expected |
|---|------|----------|
| P1 | `POST /api/v1/journal-entries` valid payload, valid `Authorization: Bearer <key>`, balanced lines, new `external_reference` | 201, serialized entry in response body, `source="webhook"` |
| P2 | Same request repeated with the same `external_reference` (retry/idempotency) | 200 (not 201 — `created=False`), same entry `id` returned, no duplicate |
| P3 | Payload with `external_reference=None` | 201 each time (no idempotency without a reference) |
| N1 | Missing/invalid `Authorization` header | 401 |
| N2 | Valid header, revoked API key | 401 |
| N3 | Payload with fewer than 2 lines | 422 — caught at the Pydantic level (`Field(min_length=2)`) before reaching the service, not `InvalidJournalEntryError` |
| N4 | Payload with unbalanced lines | 422, `HTTPException` wrapping `InvalidJournalEntryError`'s message |
| N5 | Payload referencing an inactive/nonexistent `account_id` | 422 |
| N6 | Malformed JSON body / wrong content-type | 422 (FastAPI/Pydantic validation) |
| N7 | `debit_amount`/`credit_amount` as non-numeric strings in JSON | 422 (Pydantic `Decimal` coercion failure) |
| P4 | `GET /api/v1/accounts` valid API key | 200, only active accounts, serialized fields match |
| N8 | `GET /api/v1/accounts` missing/invalid API key | 401 |
| P5 | Webhook-created entries appear in `journal_service.list_entries` / the `/journal-entries` UI | Cross-check: `source="webhook"` entries are not treated specially by any UI listing |
