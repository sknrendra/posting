from datetime import date
from decimal import Decimal as D

from app.models.account import Account
from app.services import journal_service, reconciliation_service
from app.services.journal_service import LineInput


def _account(db, code):
    return db.query(Account).filter(Account.code == code).first()


def _post(db, entry_date, cash_code, other_code, amount, cash_is_debit, memo="txn"):
    cash = _account(db, cash_code)
    other = _account(db, other_code)
    if cash_is_debit:
        cash_debit, cash_credit = amount, D("0")
        other_debit, other_credit = D("0"), amount
    else:
        cash_debit, cash_credit = D("0"), amount
        other_debit, other_credit = amount, D("0")
    entry, _created = journal_service.post_entry(
        db,
        entry_date=entry_date,
        memo=memo,
        lines=[
            LineInput(account_id=cash.id, debit_amount=cash_debit, credit_amount=cash_credit, memo=memo),
            LineInput(account_id=other.id, debit_amount=other_debit, credit_amount=other_credit, memo=memo),
        ],
        source="manual",
    )
    return entry


def _start(client, account_id, statement_date, statement_ending_balance):
    client.get(f"/reconciliation/{account_id}/new")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        f"/reconciliation/{account_id}/new",
        data={
            "csrf_token": csrf,
            "statement_date": statement_date,
            "statement_ending_balance": statement_ending_balance,
        },
        follow_redirects=False,
    )
    reconciliation_id = int(resp.headers["location"].split("/reconciliation/")[1].split("?")[0])
    return reconciliation_id, csrf


def test_start_work_toggle_complete_flow(client, db):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)

    reconciliation_id, csrf = _start(client, account.id, "2026-01-31", "500.00")

    work_resp = client.get(f"/reconciliation/{reconciliation_id}")
    assert work_resp.status_code == 200
    assert "Complete reconciliation" in work_resp.text

    line = next(l for l in entry.lines if l.account_id == account.id)
    toggle_resp = client.post(
        f"/reconciliation/{reconciliation_id}/lines/{line.id}/toggle-cleared",
        data={"csrf_token": csrf, "cleared": "true"},
        follow_redirects=False,
    )
    assert toggle_resp.status_code == 303

    complete_resp = client.post(
        f"/reconciliation/{reconciliation_id}/complete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert complete_resp.status_code == 303
    assert "error" not in complete_resp.headers["location"]

    summary_resp = client.get(f"/reconciliation/{reconciliation_id}")
    assert summary_resp.status_code == 200
    assert "Completed" in summary_resp.text


def test_add_missing_transaction_mid_flow(client, db):
    account = _account(db, "1020")
    fee_account = _account(db, "5100")

    reconciliation_id, csrf = _start(client, account.id, "2026-01-31", "-25.00")

    add_resp = client.post(
        f"/reconciliation/{reconciliation_id}/add-transaction",
        data={
            "csrf_token": csrf,
            "entry_date": "2026-01-20",
            "movement": "withdrawal",
            "amount": "25.00",
            "counter_account_id": str(fee_account.id),
            "memo": "Bank fee",
        },
        follow_redirects=False,
    )
    assert add_resp.status_code == 303
    assert "error" not in add_resp.headers["location"]

    work_resp = client.get(f"/reconciliation/{reconciliation_id}")
    assert "Bank fee" in work_resp.text

    recon = reconciliation_service.get_reconciliation(db, reconciliation_id)
    diff = reconciliation_service.compute_difference(db, recon)
    assert diff["is_balanced"]


def test_complete_out_of_balance_redirects_with_error(client, db):
    account = _account(db, "1020")
    _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)

    reconciliation_id, csrf = _start(client, account.id, "2026-01-31", "500.00")

    complete_resp = client.post(
        f"/reconciliation/{reconciliation_id}/complete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert complete_resp.status_code == 303
    assert "error=" in complete_resp.headers["location"]


def test_undo_requires_admin(client, admin_client, db):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)

    reconciliation_id, csrf = _start(client, account.id, "2026-01-31", "500.00")
    line = next(l for l in entry.lines if l.account_id == account.id)
    client.post(
        f"/reconciliation/{reconciliation_id}/lines/{line.id}/toggle-cleared",
        data={"csrf_token": csrf, "cleared": "true"},
        follow_redirects=False,
    )
    client.post(
        f"/reconciliation/{reconciliation_id}/complete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    non_admin_resp = client.get(f"/reconciliation/{reconciliation_id}/undo")
    assert non_admin_resp.status_code == 403

    admin_get = admin_client.get(f"/reconciliation/{reconciliation_id}/undo")
    assert admin_get.status_code == 200
    admin_csrf = admin_client.cookies.get("csrf_token")
    admin_post = admin_client.post(
        f"/reconciliation/{reconciliation_id}/undo",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert admin_post.status_code == 303
    assert "error" not in admin_post.headers["location"]

    history_resp = admin_client.get(f"/reconciliation/{account.id}/history")
    assert "undone" in history_resp.text.lower()


def test_reports_index_still_renders_after_rewrite(client):
    resp = client.get("/reports")
    assert resp.status_code == 200
