import itertools
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.services import account_service, journal_service, reconciliation_service
from app.services.journal_service import LineInput
from app.services.reconciliation_service import ReconciliationError
from app.utils import periods as period_utils

_seq = itertools.count()

TODAY = date.today()
# A period guaranteed to have already closed (period_end < today), regardless
# of what day of the month "today" happens to be.
_PREV_MONTH_ANCHOR = TODAY.replace(day=1) - timedelta(days=1)
CLOSED_START, CLOSED_END = period_utils.month_bounds(_PREV_MONTH_ANCHOR)
# The current month is never closed (period_end >= today always).
OPEN_START, OPEN_END = period_utils.month_bounds(TODAY)


def _cash_account(db, **kwargs):
    return account_service.create_account(
        db, f"TR-CASH-{next(_seq)}", "Test Cash", "asset", is_cash_account=True, **kwargs
    )


@pytest.fixture()
def no_cash_accounts(db):
    """Accounts (including the seeded default cash accounts) aren't cleaned
    between tests, so a genuinely empty cash-account set has to be forced by
    temporarily deactivating whatever's currently active."""
    active = reconciliation_service.get_cash_accounts(db)
    for account in active:
        account_service.set_active(db, account, False)
    yield
    for account in active:
        account_service.set_active(db, account, True)


def _post_in_closed_period(db, cash, amount=D(100)):
    revenue = account_service.create_account(db, f"TR-REV-{next(_seq)}", "Test Revenue", "revenue")
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


# --- get_cash_accounts -----------------------------------------------------


def test_get_cash_accounts_only_active_cash(db):
    cash = _cash_account(db)
    non_cash = account_service.create_account(db, f"TR-NC-{next(_seq)}", "Not cash", "asset")
    inactive_cash = _cash_account(db)
    account_service.set_active(db, inactive_cash, False)

    result_ids = {a.id for a in reconciliation_service.get_cash_accounts(db)}
    assert cash.id in result_ids
    assert non_cash.id not in result_ids
    assert inactive_cash.id not in result_ids


def test_get_cash_accounts_ordered_by_code(db):
    account_service.create_account(db, "TR-ZZZZ", "Z Cash", "asset", is_cash_account=True)
    account_service.create_account(db, "TR-AAAA", "A Cash", "asset", is_cash_account=True)
    codes = [a.code for a in reconciliation_service.get_cash_accounts(db)]
    assert codes == sorted(codes)


# --- get_activity_range -----------------------------------------------------


def test_activity_range_no_entries_returns_today_today(db):
    from app.models.journal_entry import JournalEntry

    # relies on _clean_slate having wiped journal_entries; guard in case of leakage
    assert db.query(JournalEntry).count() == 0
    earliest, latest = reconciliation_service.get_activity_range(db)
    assert earliest == TODAY
    assert latest == TODAY


def test_activity_range_past_entries_latest_clamped_to_today(db):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash)
    earliest, latest = reconciliation_service.get_activity_range(db)
    assert earliest == CLOSED_START
    assert latest == TODAY


def test_activity_range_future_entry_not_clamped_down(db):
    cash = _cash_account(db)
    revenue = account_service.create_account(db, f"TR-REV-{next(_seq)}", "Test Revenue", "revenue")
    future_date = TODAY + timedelta(days=30)
    journal_service.post_entry(
        db,
        entry_date=future_date,
        memo="future",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(10), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D(10)),
        ],
        source="manual",
    )
    earliest, latest = reconciliation_service.get_activity_range(db)
    assert latest == future_date


# --- list_periods -----------------------------------------------------------


def test_list_periods_only_closed_periods_included(db):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash)
    periods = reconciliation_service.list_periods(db, "monthly")
    assert (CLOSED_START, CLOSED_END) in periods
    assert (OPEN_START, OPEN_END) not in periods


def test_list_periods_no_closed_periods_when_only_current_month_activity(db):
    cash = _cash_account(db)
    revenue = account_service.create_account(db, f"TR-REV-{next(_seq)}", "Test Revenue", "revenue")
    journal_service.post_entry(
        db,
        entry_date=TODAY,
        memo="today",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(10), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D(10)),
        ],
        source="manual",
    )
    periods = reconciliation_service.list_periods(db, "monthly")
    assert periods == []


def test_list_periods_no_activity_returns_empty(db):
    assert reconciliation_service.list_periods(db, "monthly") == []


# --- get_reconciliation_rows -------------------------------------------------


def test_reconciliation_rows_no_existing_row_computes_live_balance(db):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash, amount=D(250))
    rows = reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END)
    row = next(r for r in rows if r["account"].id == cash.id)
    assert row["status"] == "open"
    assert row["book_balance"] == D(250)
    assert row["reconciliation"] is None


def test_reconciliation_rows_open_recon_uses_live_balance(db, test_user):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash, amount=D(250))
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(250), test_user
    )
    # Post more activity after submitting the statement balance.
    _post_in_closed_period(db, cash, amount=D(50))
    rows = reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END)
    row = next(r for r in rows if r["account"].id == cash.id)
    assert row["status"] == "open"
    assert row["book_balance"] == D(300)  # live, not frozen


def test_reconciliation_rows_completed_recon_uses_frozen_balance(db, test_user):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash, amount=D(250))
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(250), test_user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user)
    # New activity after confirmation must not change the frozen book_balance.
    _post_in_closed_period(db, cash, amount=D(999))
    rows = reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END)
    row = next(r for r in rows if r["account"].id == cash.id)
    assert row["status"] == "completed"
    assert row["book_balance"] == D(250)


def test_reconciliation_rows_multiple_accounts_partial_recon(db, test_user):
    cash1 = _cash_account(db)
    cash2 = _cash_account(db)
    _post_in_closed_period(db, cash1, amount=D(10))
    _post_in_closed_period(db, cash2, amount=D(20))
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash1.id, D(10), test_user
    )
    rows = reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END)
    by_id = {r["account"].id: r for r in rows}
    assert by_id[cash1.id]["reconciliation"] is not None
    assert by_id[cash2.id]["reconciliation"] is None
    assert by_id[cash2.id]["status"] == "open"


def test_reconciliation_rows_no_cash_accounts_empty(db, no_cash_accounts):
    assert reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END) == []


# --- submit_statement_balance -------------------------------------------------


def test_submit_statement_balance_first_submission(db, test_user):
    cash = _cash_account(db)
    recon = reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(500), test_user
    )
    assert recon.status == "open"
    assert recon.statement_balance == D(500)
    assert recon.submitted_by_user_id == test_user.id


def test_submit_statement_balance_resubmission_updates_in_place(db, test_user):
    cash = _cash_account(db)
    first = reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(500), test_user
    )
    second = reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(600), test_user
    )
    assert first.id == second.id
    assert second.statement_balance == D(600)
    rows = reconciliation_service.get_reconciliation_rows(db, "monthly", CLOSED_START, CLOSED_END)
    assert len([r for r in rows if r["account"].id == cash.id and r["reconciliation"]]) == 1


def test_submit_statement_balance_period_not_closed_raises(db, test_user):
    cash = _cash_account(db)
    with pytest.raises(ReconciliationError, match="hasn't closed yet"):
        reconciliation_service.submit_statement_balance(
            db, "monthly", OPEN_START, OPEN_END, cash.id, D(100), test_user
        )


def test_submit_statement_balance_already_completed_raises(db, test_user):
    cash = _cash_account(db)
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(0), test_user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user)
    with pytest.raises(ReconciliationError, match="already reconciled"):
        reconciliation_service.submit_statement_balance(
            db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(999), test_user
        )


def test_submit_statement_balance_negative_accepted(db, test_user):
    cash = _cash_account(db)
    recon = reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(-50), test_user
    )
    assert recon.statement_balance == D(-50)


# --- confirm_reconciliation -------------------------------------------------


def test_confirm_reconciliation_success(db, test_user):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash, amount=D(100))
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(100), test_user
    )
    confirmed = reconciliation_service.confirm_reconciliation(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user
    )
    assert confirmed.status == "completed"
    assert confirmed.book_balance == D(100)
    assert confirmed.reconciled_by_user_id == test_user.id
    assert confirmed.reconciled_at is not None


def test_confirm_reconciliation_idempotent_reconfirm_does_not_resnapshot(db, test_user):
    cash = _cash_account(db)
    _post_in_closed_period(db, cash, amount=D(100))
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(100), test_user
    )
    first_confirm = reconciliation_service.confirm_reconciliation(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user
    )
    first_reconciled_at = first_confirm.reconciled_at
    first_book_balance = first_confirm.book_balance

    _post_in_closed_period(db, cash, amount=D(9999))  # would change balance if resnapshotted
    second_confirm = reconciliation_service.confirm_reconciliation(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user
    )
    assert second_confirm.reconciled_at == first_reconciled_at
    assert second_confirm.book_balance == first_book_balance


def test_confirm_reconciliation_no_submission_raises(db, test_user):
    cash = _cash_account(db)
    with pytest.raises(ReconciliationError, match="Enter a statement balance"):
        reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash.id, test_user)


def test_confirm_reconciliation_period_not_closed_raises_before_no_row_check(db, test_user):
    cash = _cash_account(db)
    with pytest.raises(ReconciliationError, match="hasn't closed yet"):
        reconciliation_service.confirm_reconciliation(db, "monthly", OPEN_START, OPEN_END, cash.id, test_user)


# --- get_period_status -------------------------------------------------------


def test_period_status_zero_cash_accounts_not_complete(db, no_cash_accounts):
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status == {"completed": 0, "total": 0, "is_complete": False}


def test_period_status_none_reconciled(db):
    _cash_account(db)
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status["completed"] == 0
    assert status["is_complete"] is False


def test_period_status_partial_not_complete(db, test_user, no_cash_accounts):
    cash1 = _cash_account(db)
    _cash_account(db)
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash1.id, D(0), test_user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash1.id, test_user)
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status["completed"] == 1
    assert status["total"] == 2
    assert status["is_complete"] is False


def test_period_status_all_complete(db, test_user, no_cash_accounts):
    cash1 = _cash_account(db)
    cash2 = _cash_account(db)
    for acc in (cash1, cash2):
        reconciliation_service.submit_statement_balance(
            db, "monthly", CLOSED_START, CLOSED_END, acc.id, D(0), test_user
        )
        reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, acc.id, test_user)
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status == {"completed": 2, "total": 2, "is_complete": True}


def test_period_status_ignores_other_periods(db, test_user):
    cash = _cash_account(db)
    other_anchor = _PREV_MONTH_ANCHOR.replace(day=1) - timedelta(days=1)
    other_start, other_end = period_utils.month_bounds(other_anchor)
    reconciliation_service.submit_statement_balance(db, "monthly", other_start, other_end, cash.id, D(0), test_user)
    reconciliation_service.confirm_reconciliation(db, "monthly", other_start, other_end, cash.id, test_user)
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status["completed"] == 0


def test_period_status_open_recon_not_counted_as_completed(db, test_user):
    cash = _cash_account(db)
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash.id, D(0), test_user
    )
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status["completed"] == 0
    assert status["is_complete"] is False


# --- integration -------------------------------------------------------------


def test_new_cash_account_after_full_reconciliation_reopens_period(db, test_user, no_cash_accounts):
    cash1 = _cash_account(db)
    reconciliation_service.submit_statement_balance(
        db, "monthly", CLOSED_START, CLOSED_END, cash1.id, D(0), test_user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", CLOSED_START, CLOSED_END, cash1.id, test_user)
    assert reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)["is_complete"] is True

    _cash_account(db)  # new cash account added after the period was "done"
    status = reconciliation_service.get_period_status(db, "monthly", CLOSED_START, CLOSED_END)
    assert status["is_complete"] is False
