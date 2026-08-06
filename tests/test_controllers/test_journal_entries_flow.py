import itertools
from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.main import app
from app.services import account_service, invoice_service, journal_service
from app.services.invoice_service import InvoiceHeaderInput, InvoiceLineInput

_seq = itertools.count()


def _accounts(db):
    n = next(_seq)
    cash = account_service.create_account(db, f"JC-CASH-{n}", "Test Cash", "asset")
    revenue = account_service.create_account(db, f"JC-REV-{n}", "Test Revenue", "revenue")
    return cash, revenue


def _new_entry_form(csrf, cash_id, revenue_id, **overrides):
    data = {
        "csrf_token": csrf,
        "entry_date": "2026-07-29",
        "memo": "Test entry",
        "account_id": [str(cash_id), str(revenue_id)],
        "debit_amount": ["100", ""],
        "credit_amount": ["", "100"],
        "line_memo": ["", ""],
    }
    data.update(overrides)
    return data


def test_list_journal_entries(client, db):
    from app.services.journal_service import LineInput

    cash, revenue = _accounts(db)
    journal_service.post_entry(
        db,
        entry_date=date(2026, 7, 29),
        memo="Existing entry",
        lines=[
            LineInput(account_id=cash.id, debit_amount=50, credit_amount=0),
            LineInput(account_id=revenue.id, debit_amount=0, credit_amount=50),
        ],
        source="manual",
    )
    resp = client.get("/journal-entries")
    assert resp.status_code == 200
    assert "Existing entry" in resp.text


def test_new_journal_entry_form(client):
    resp = client.get("/journal-entries/new")
    assert resp.status_code == 200
    assert "csrf_token" in resp.text


def test_create_journal_entry_success(client, db):
    cash, revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new", data=_new_entry_form(csrf, cash.id, revenue.id), follow_redirects=False
    )
    assert resp.status_code == 303
    assert "ok=" in resp.headers["location"]


def test_journal_entry_detail_with_related_invoice(client, db, test_user):
    header = InvoiceHeaderInput(
        invoice_date=date(2026, 7, 29),
        customer_name="Acme",
        lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=D(1000))],
    )
    invoice = invoice_service.create_draft(db, header, user_id=test_user.id)
    posted = invoice_service.post_invoice(db, invoice, user_id=test_user.id)
    resp = client.get(f"/journal-entries/{posted.journal_entry_id}")
    assert resp.status_code == 200
    assert posted.invoice_number in resp.text


def test_create_journal_entry_invalid_date_returns_422(client, db):
    cash, revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new",
        data=_new_entry_form(csrf, cash.id, revenue.id, entry_date="not-a-date"),
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Invalid entry date" in resp.text


def test_create_journal_entry_blank_memo_returns_422(client, db):
    cash, revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new",
        data=_new_entry_form(csrf, cash.id, revenue.id, memo=""),
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Memo is required" in resp.text


def test_create_journal_entry_unbalanced_returns_422(client, db):
    cash, revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new",
        data=_new_entry_form(csrf, cash.id, revenue.id, debit_amount=["100", ""], credit_amount=["", "90"]),
        follow_redirects=False,
    )
    assert resp.status_code == 422


def test_create_journal_entry_non_numeric_amount_returns_422(client, db):
    cash, revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new",
        data=_new_entry_form(csrf, cash.id, revenue.id, debit_amount=["abc", ""]),
        follow_redirects=False,
    )
    assert resp.status_code == 422


def test_journal_entry_detail_nonexistent_404(client):
    resp = client.get("/journal-entries/999999")
    assert resp.status_code == 404


def test_create_journal_entry_fewer_than_two_lines_returns_422(client, db):
    cash, _revenue = _accounts(db)
    client.get("/journal-entries/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/journal-entries/new",
        data={
            "csrf_token": csrf,
            "entry_date": "2026-07-29",
            "memo": "Single line",
            "account_id": [str(cash.id)],
            "debit_amount": ["100"],
            "credit_amount": [""],
            "line_memo": [""],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 422


def test_create_journal_entry_missing_csrf_returns_403(client, db):
    cash, revenue = _accounts(db)
    resp = client.post(
        "/journal-entries/new",
        data=_new_entry_form("wrong-token", cash.id, revenue.id),
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_journal_entries_unauthenticated_redirects_to_login():
    unauth_client = TestClient(app)
    resp = unauth_client.get("/journal-entries", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
