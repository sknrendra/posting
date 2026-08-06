import itertools

from fastapi.testclient import TestClient

from app.main import app
from app.services import account_service, api_key_service

_seq = itertools.count()


def _accounts(db):
    n = next(_seq)
    cash = account_service.create_account(db, f"WH-CASH-{n}", "Test Cash", "asset")
    revenue = account_service.create_account(db, f"WH-REV-{n}", "Test Revenue", "revenue")
    return cash, revenue


def _api_client(db, test_user):
    _api_key, token = api_key_service.create_api_key(db, "Webhook Test Key", test_user.id)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _payload(cash_id, revenue_id, **overrides):
    payload = {
        "entry_date": "2026-07-29",
        "memo": "Webhook entry",
        "lines": [
            {"account_id": cash_id, "debit_amount": "100", "credit_amount": "0"},
            {"account_id": revenue_id, "debit_amount": "0", "credit_amount": "100"},
        ],
    }
    payload.update(overrides)
    return payload


def test_create_journal_entry_valid(db, test_user):
    cash, revenue = _accounts(db)
    c = _api_client(db, test_user)
    resp = c.post("/api/v1/journal-entries", json=_payload(cash.id, revenue.id, external_reference="wh-1"))
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "webhook"
    assert len(body["lines"]) == 2


def test_create_journal_entry_idempotent_retry(db, test_user):
    cash, revenue = _accounts(db)
    c = _api_client(db, test_user)
    payload = _payload(cash.id, revenue.id, external_reference="wh-retry")
    first = c.post("/api/v1/journal-entries", json=payload)
    assert first.status_code == 201
    second = c.post("/api/v1/journal-entries", json=payload)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_create_journal_entry_no_external_reference_not_idempotent(db, test_user):
    cash, revenue = _accounts(db)
    c = _api_client(db, test_user)
    payload = _payload(cash.id, revenue.id)
    first = c.post("/api/v1/journal-entries", json=payload)
    second = c.post("/api/v1/journal-entries", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_create_journal_entry_missing_auth_header_401():
    c = TestClient(app)
    resp = c.post("/api/v1/journal-entries", json=_payload(1, 2))
    assert resp.status_code == 401


def test_create_journal_entry_revoked_key_401(db, test_user):
    api_key, token = api_key_service.create_api_key(db, "To Revoke", test_user.id)
    api_key_service.revoke(db, api_key)
    cash, revenue = _accounts(db)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    resp = c.post("/api/v1/journal-entries", json=_payload(cash.id, revenue.id))
    assert resp.status_code == 401


def test_create_journal_entry_fewer_than_two_lines_422(db, test_user):
    cash, _revenue = _accounts(db)
    c = _api_client(db, test_user)
    payload = {
        "entry_date": "2026-07-29",
        "memo": "x",
        "lines": [{"account_id": cash.id, "debit_amount": "100", "credit_amount": "0"}],
    }
    resp = c.post("/api/v1/journal-entries", json=payload)
    assert resp.status_code == 422


def test_create_journal_entry_unbalanced_422(db, test_user):
    cash, revenue = _accounts(db)
    c = _api_client(db, test_user)
    payload = _payload(cash.id, revenue.id)
    payload["lines"][1]["credit_amount"] = "50"
    resp = c.post("/api/v1/journal-entries", json=payload)
    assert resp.status_code == 422


def test_create_journal_entry_inactive_account_422(db, test_user):
    cash, revenue = _accounts(db)
    account_service.set_active(db, revenue, False)
    c = _api_client(db, test_user)
    resp = c.post("/api/v1/journal-entries", json=_payload(cash.id, revenue.id))
    assert resp.status_code == 422


def test_create_journal_entry_malformed_json_422():
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer whatever"
    resp = c.post("/api/v1/journal-entries", content="not json", headers={"content-type": "application/json"})
    assert resp.status_code == 422


def test_create_journal_entry_non_numeric_amount_422(db, test_user):
    cash, revenue = _accounts(db)
    c = _api_client(db, test_user)
    payload = _payload(cash.id, revenue.id)
    payload["lines"][0]["debit_amount"] = "not-a-number"
    resp = c.post("/api/v1/journal-entries", json=payload)
    assert resp.status_code == 422


def test_list_accounts_valid_key(db, test_user):
    cash, _revenue = _accounts(db)
    inactive = account_service.create_account(db, f"WH-INACTIVE-{next(_seq)}", "Inactive", "asset")
    account_service.set_active(db, inactive, False)
    c = _api_client(db, test_user)
    resp = c.get("/api/v1/accounts")
    assert resp.status_code == 200
    codes = {a["code"] for a in resp.json()}
    assert cash.code in codes
    assert inactive.code not in codes


def test_list_accounts_missing_key_401():
    c = TestClient(app)
    resp = c.get("/api/v1/accounts")
    assert resp.status_code == 401
