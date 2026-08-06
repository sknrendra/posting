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


def _fully_reconcile_a_period(db, test_user):
    cash = account_service.create_account(
        db, f"REP-CASH-{next(_seq)}", "Test Cash", "asset", is_cash_account=True
    )
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(0), test_user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user)
    return cash


@pytest.fixture()
def no_cash_accounts(db):
    active = reconciliation_service.get_cash_accounts(db)
    for account in active:
        account_service.set_active(db, account, False)
    yield
    for account in active:
        account_service.set_active(db, account, True)


def test_reports_index(client):
    resp = client.get("/reports")
    assert resp.status_code == 200


def test_generate_reports_success(client, db, test_user, no_cash_accounts):
    _fully_reconcile_a_period(db, test_user)
    client.get("/reports")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/reports/generate",
        data={"csrf_token": csrf, "period_type": "monthly", "period_start": CLOSED_START.isoformat()},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ok=Reports" in resp.headers["location"]


def test_generate_reports_invalid_period_type_404(client):
    client.get("/reports")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/reports/generate",
        data={"csrf_token": csrf, "period_type": "weekly", "period_start": CLOSED_START.isoformat()},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_generate_reports_not_reconciled_error(client, db, no_cash_accounts):
    account_service.create_account(
        db, f"REP-CASH-{next(_seq)}", "Unreconciled Cash", "asset", is_cash_account=True
    )
    client.get("/reports")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/reports/generate",
        data={"csrf_token": csrf, "period_type": "monthly", "period_start": CLOSED_START.isoformat()},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_view_report_after_generation(client, db, test_user, no_cash_accounts):
    _fully_reconcile_a_period(db, test_user)
    client.get("/reports")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/reports/generate",
        data={"csrf_token": csrf, "period_type": "monthly", "period_start": CLOSED_START.isoformat()},
        follow_redirects=False,
    )
    resp = client.get(f"/reports/profit_loss/monthly/{CLOSED_START.isoformat()}")
    assert resp.status_code == 200


def test_view_report_invalid_report_type_404(client):
    resp = client.get(f"/reports/bogus/monthly/{CLOSED_START.isoformat()}")
    assert resp.status_code == 404


def test_view_report_before_generation_404(client):
    resp = client.get(f"/reports/profit_loss/monthly/{CLOSED_START.isoformat()}")
    assert resp.status_code == 404


def test_generate_reports_invalid_period_start_currently_crashes(client):
    """Documents an existing gap: generate_reports parses period_start with
    a bare date.fromisoformat(...), no try/except — raises uncaught."""
    client.get("/reports")
    csrf = client.cookies.get("csrf_token")
    with pytest.raises(ValueError):
        client.post(
            "/reports/generate",
            data={"csrf_token": csrf, "period_type": "monthly", "period_start": "not-a-date"},
            follow_redirects=False,
        )


def test_reports_unauthenticated_redirects_to_login():
    unauth_client = TestClient(app)
    resp = unauth_client.get("/reports", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
