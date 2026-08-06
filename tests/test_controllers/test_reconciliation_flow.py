import itertools
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import account_service, journal_service, reconciliation_service
from app.services.journal_service import LineInput
from app.utils import periods as period_utils

_seq = itertools.count()

TODAY = date.today()
_PREV_MONTH_ANCHOR = TODAY.replace(day=1) - timedelta(days=1)
CLOSED_START, CLOSED_END = period_utils.month_bounds(_PREV_MONTH_ANCHOR)
OPEN_START, OPEN_END = period_utils.month_bounds(TODAY)


def _cash_account(db):
    return account_service.create_account(
        db, f"RC-CASH-{next(_seq)}", "Test Cash", "asset", is_cash_account=True
    )


def _post_closed_period_activity(db, cash, amount=D(100)):
    revenue = account_service.create_account(db, f"RC-REV-{next(_seq)}", "Test Revenue", "revenue")
    journal_service.post_entry(
        db,
        entry_date=CLOSED_START,
        memo="x",
        lines=[
            LineInput(account_id=cash.id, debit_amount=amount, credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=amount),
        ],
        source="manual",
    )


def test_reconciliation_index_default(client):
    resp = client.get("/reconciliation")
    assert resp.status_code == 200


def test_reconciliation_index_quarterly_annual(client):
    assert client.get("/reconciliation?period_type=quarterly").status_code == 200
    assert client.get("/reconciliation?period_type=annual").status_code == 200


def test_reconciliation_index_invalid_period_type_404(client):
    resp = client.get("/reconciliation?period_type=weekly")
    assert resp.status_code == 404


def test_reconciliation_detail_closed_period(client, db):
    cash = _cash_account(db)
    _post_closed_period_activity(db, cash)
    resp = client.get(f"/reconciliation/monthly/{CLOSED_START.isoformat()}")
    assert resp.status_code == 200


def test_reconciliation_detail_invalid_period_type_404(client):
    resp = client.get(f"/reconciliation/weekly/{CLOSED_START.isoformat()}")
    assert resp.status_code == 404


def test_submit_statement_balance_success(client, db):
    cash = _cash_account(db)
    client.get("/reconciliation")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/reconciliation/monthly/{CLOSED_START.isoformat()}/{cash.id}",
        data={"csrf_token": csrf, "statement_balance": "500"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]


def test_submit_statement_balance_invalid_amount(client, db):
    cash = _cash_account(db)
    client.get("/reconciliation")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/reconciliation/monthly/{CLOSED_START.isoformat()}/{cash.id}",
        data={"csrf_token": csrf, "statement_balance": "not-a-number"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Invalid+statement+balance" in resp.headers["location"] or "Invalid%20statement%20balance" in resp.headers["location"]


def test_submit_statement_balance_period_not_closed(client, db):
    cash = _cash_account(db)
    client.get("/reconciliation")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/reconciliation/monthly/{OPEN_START.isoformat()}/{cash.id}",
        data={"csrf_token": csrf, "statement_balance": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_confirm_reconciliation_success(client, db):
    cash = _cash_account(db)
    _post_closed_period_activity(db, cash)
    client.get("/reconciliation")
    csrf = client.cookies.get("csrf_token")
    client.post(
        f"/reconciliation/monthly/{CLOSED_START.isoformat()}/{cash.id}",
        data={"csrf_token": csrf, "statement_balance": "100"},
        follow_redirects=False,
    )
    resp = client.post(
        f"/reconciliation/monthly/{CLOSED_START.isoformat()}/{cash.id}/confirm",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=Reconciled" in resp.headers["location"]


def test_confirm_reconciliation_without_submission_error(client, db):
    cash = _cash_account(db)
    client.get("/reconciliation")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/reconciliation/monthly/{CLOSED_START.isoformat()}/{cash.id}/confirm",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_reconciliation_detail_invalid_period_start_currently_crashes(client):
    """Documents an existing gap: reconciliation_detail parses period_start
    with a bare date.fromisoformat(...), no try/except — raises uncaught."""
    with pytest.raises(ValueError):
        client.get("/reconciliation/monthly/not-a-date")


def test_reconciliation_unauthenticated_redirects_to_login():
    unauth_client = TestClient(app)
    resp = unauth_client.get("/reconciliation", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
