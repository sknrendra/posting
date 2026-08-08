import itertools
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import account_service, journal_service
from app.services.journal_service import LineInput

_seq = itertools.count()


def test_trial_balance_default_today(client):
    resp = client.get("/balance/trial-balance")
    assert resp.status_code == 200


def test_trial_balance_explicit_as_of(client, db):
    cash = account_service.create_account(db, f"BAL-CASH-{next(_seq)}", "Cash", "asset")
    revenue = account_service.create_account(db, f"BAL-REV-{next(_seq)}", "Revenue", "revenue")
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 1),
        memo="Balance test",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(500), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D(500)),
        ],
        source="manual",
    )
    resp = client.get("/balance/trial-balance?as_of=2026-01-01")
    assert resp.status_code == 200
    assert "500" in resp.text


def test_trial_balance_invalid_as_of_currently_crashes(client):
    """Documents an existing gap: trial_balance parses as_of with a bare
    date.fromisoformat(...), no try/except — raises uncaught."""
    with pytest.raises(ValueError):
        client.get("/balance/trial-balance?as_of=garbage")


def test_balance_unauthenticated_redirects_to_login():
    unauth_client = TestClient(app)
    resp = unauth_client.get("/balance/trial-balance", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
