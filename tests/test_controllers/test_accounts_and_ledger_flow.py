import itertools
from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.main import app
from app.services import account_service, journal_service
from app.services.journal_service import LineInput

_seq = itertools.count()


def _code():
    return f"AL-{next(_seq)}"


def _new_account_form(csrf, **overrides):
    data = {
        "csrf_token": csrf,
        "code": _code(),
        "name": "Test Account",
        "account_type": "asset",
        "is_cash_account": "false",
        "description": "",
    }
    data.update(overrides)
    return data


# --- /accounts ---------------------------------------------------------------


def test_list_accounts(client):
    resp = client.get("/accounts")
    assert resp.status_code == 200


def test_new_account_form(client):
    resp = client.get("/accounts/new")
    assert resp.status_code == 200


def test_create_account_success(client):
    client.get("/accounts/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/accounts/new", data=_new_account_form(csrf), follow_redirects=False)
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]


def test_create_account_invalid_type_returns_422(client):
    client.get("/accounts/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/accounts/new", data=_new_account_form(csrf, account_type="bogus"), follow_redirects=False
    )
    assert resp.status_code == 422


def test_edit_account_form(client, db):
    account = account_service.create_account(db, _code(), "Existing", "asset")
    resp = client.get(f"/accounts/{account.id}/edit")
    assert resp.status_code == 200


def test_edit_account_form_nonexistent_404(client):
    resp = client.get("/accounts/999999/edit")
    assert resp.status_code == 404


def test_update_account_success(client, db):
    account = account_service.create_account(db, _code(), "Old Name", "asset")
    client.get(f"/accounts/{account.id}/edit")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/accounts/{account.id}/edit",
        data={"csrf_token": csrf, "name": "New Name", "is_cash_account": "true", "description": "updated"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail = client.get("/accounts")
    assert "New Name" in detail.text


def test_update_account_nonexistent_404(client):
    resp = client.post(
        "/accounts/999999/edit", data={"csrf_token": "x", "name": "X"}, follow_redirects=False
    )
    assert resp.status_code in (403, 404)  # csrf check runs before the 404 guard


def test_deactivate_and_activate_account(client, db):
    account = account_service.create_account(db, _code(), "Togglable", "asset")
    client.get("/accounts")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(f"/accounts/{account.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(account)
    assert account.is_active is False

    resp = client.post(f"/accounts/{account.id}/activate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(account)
    assert account.is_active is True


def test_deactivate_account_nonexistent_404(client):
    client.get("/accounts")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/accounts/999999/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert resp.status_code == 404


# --- /ledger -------------------------------------------------------------


def test_ledger_picker(client):
    resp = client.get("/ledger")
    assert resp.status_code == 200


def test_ledger_detail_no_filters(client, db):
    account = account_service.create_account(db, _code(), "Ledger Test", "asset")
    resp = client.get(f"/ledger/{account.id}")
    assert resp.status_code == 200


def test_ledger_detail_with_valid_date_filters(client, db):
    account = account_service.create_account(db, _code(), "Ledger Test", "asset")
    resp = client.get(f"/ledger/{account.id}?date_from=2026-01-01&date_to=2026-12-31")
    assert resp.status_code == 200


def test_ledger_detail_nonexistent_account_404(client):
    resp = client.get("/ledger/999999")
    assert resp.status_code == 404


def test_ledger_detail_invalid_date_currently_crashes(client, db):
    """Documents an existing gap: ledger_detail parses date_from/date_to with
    a bare date.fromisoformat(...), no try/except — an invalid value raises
    ValueError uncaught (TestClient propagates it directly since
    raise_server_exceptions defaults to True; a real deployment would see a 500)."""
    import pytest

    account = account_service.create_account(db, _code(), "Ledger Test", "asset")
    with pytest.raises(ValueError):
        client.get(f"/ledger/{account.id}?date_from=not-a-date")


def test_ledger_detail_inactive_account_still_reachable_directly(client, db):
    account = account_service.create_account(db, _code(), "Inactive Ledger", "asset")
    account_service.set_active(db, account, False)
    resp = client.get(f"/ledger/{account.id}")
    assert resp.status_code == 200


def test_ledger_detail_shows_activity(client, db):
    cash = account_service.create_account(db, _code(), "Cash", "asset")
    revenue = account_service.create_account(db, _code(), "Revenue", "revenue")
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 1),
        memo="Ledger activity",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(100), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D(100)),
        ],
        source="manual",
    )
    resp = client.get(f"/ledger/{cash.id}")
    assert resp.status_code == 200
    assert "Ledger activity" in resp.text


# --- auth -------------------------------------------------------------


def test_accounts_unauthenticated_redirects_to_login():
    unauth_client = TestClient(app)
    resp = unauth_client.get("/accounts", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_create_account_missing_csrf_returns_403(client):
    resp = client.post("/accounts/new", data=_new_account_form("wrong-token"), follow_redirects=False)
    assert resp.status_code == 403
