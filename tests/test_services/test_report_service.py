import itertools
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.services import account_service, journal_service, reconciliation_service, report_service
from app.services.journal_service import LineInput
from app.services.report_service import ReportGenerationError, _serialize

_seq = itertools.count()

TODAY = date.today()
_PREV_MONTH_ANCHOR = TODAY.replace(day=1) - timedelta(days=1)
PERIOD_START = _PREV_MONTH_ANCHOR.replace(day=1)
PERIOD_END = _PREV_MONTH_ANCHOR


def _account(db, account_type, is_cash=False):
    return account_service.create_account(
        db, f"RS-{account_type[:3].upper()}-{next(_seq)}", f"Test {account_type}", account_type, is_cash_account=is_cash
    )


def _post(db, debit_id, credit_id, amount, entry_date):
    journal_service.post_entry(
        db,
        entry_date=entry_date,
        memo="x",
        lines=[
            LineInput(account_id=debit_id, debit_amount=amount, credit_amount=D(0)),
            LineInput(account_id=credit_id, debit_amount=D(0), credit_amount=amount),
        ],
        source="manual",
    )


def _within_period():
    return PERIOD_START + timedelta(days=1)


@pytest.fixture()
def no_cash_accounts(db):
    """Cash accounts persist across tests, so full-reconciliation gating tests
    need a clean slate to make `get_period_status` deterministic."""
    active = reconciliation_service.get_cash_accounts(db)
    for account in active:
        account_service.set_active(db, account, False)
    yield
    for account in active:
        account_service.set_active(db, account, True)


# --- compute_profit_loss ------------------------------------------------------


def test_profit_loss_revenue_and_expense_in_period(db):
    cash = _account(db, "asset")
    revenue = _account(db, "revenue")
    expense = _account(db, "expense")
    _post(db, cash.id, revenue.id, D(1000), _within_period())
    _post(db, expense.id, cash.id, D(300), _within_period())

    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result["total_revenue"] == D(1000)
    assert result["total_expenses"] == D(300)
    assert result["net_income"] == D(700)


def test_profit_loss_excludes_non_revenue_expense_accounts(db):
    cash = _account(db, "asset")
    equity = _account(db, "equity")
    _post(db, cash.id, equity.id, D(5000), _within_period())
    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result["revenue"] == []
    assert result["expenses"] == []


def test_profit_loss_zero_net_activity_in_period_excluded(db):
    cash = _account(db, "asset")
    revenue = _account(db, "revenue")
    _post(db, cash.id, revenue.id, D(100), _within_period())
    _post(db, revenue.id, cash.id, D(100), _within_period())  # reverses within same period -> net 0
    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result["revenue"] == []
    assert result["total_revenue"] == D(0)


def test_profit_loss_activity_outside_period_not_counted(db):
    cash = _account(db, "asset")
    revenue = _account(db, "revenue")
    _post(db, cash.id, revenue.id, D(100), PERIOD_START - timedelta(days=5))
    _post(db, cash.id, revenue.id, D(100), PERIOD_END + timedelta(days=5))
    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result["total_revenue"] == D(0)


def test_profit_loss_no_activity_empty(db):
    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result == {
        "revenue": [],
        "total_revenue": D(0),
        "expenses": [],
        "total_expenses": D(0),
        "net_income": D(0),
    }


def test_profit_loss_expenses_exceed_revenue_negative_net_income(db):
    cash = _account(db, "asset")
    revenue = _account(db, "revenue")
    expense = _account(db, "expense")
    _post(db, cash.id, revenue.id, D(100), _within_period())
    _post(db, expense.id, cash.id, D(400), _within_period())
    result = report_service.compute_profit_loss(db, PERIOD_START, PERIOD_END)
    assert result["net_income"] == D(-300)


# --- compute_balance_sheet ----------------------------------------------------


def test_balance_sheet_buckets_asset_liability_equity(db):
    asset = _account(db, "asset")
    liability = _account(db, "liability")
    _post(db, asset.id, liability.id, D(400), _within_period())
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    assert result["total_assets"] == D(400)
    assert result["total_liabilities"] == D(400)


def test_balance_sheet_revenue_feeds_retained_earnings_not_own_bucket(db):
    asset = _account(db, "asset")
    revenue = _account(db, "revenue")
    _post(db, asset.id, revenue.id, D(600), _within_period())
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    assert result["assets"][0]["amount"] == D(600)
    equity_codes = {row["account_code"] for row in result["equity"]}
    assert revenue.code not in equity_codes
    retained = next(r for r in result["equity"] if "Retained Earnings" in r["account_name"])
    assert retained["amount"] == D(600)


def test_balance_sheet_expense_decreases_retained_earnings(db):
    asset = _account(db, "asset")
    expense = _account(db, "expense")
    equity = _account(db, "equity")
    # Seed equity so assets aren't left at 0 while forcing a negative retained-earnings line.
    _post(db, asset.id, equity.id, D(1000), _within_period())
    _post(db, expense.id, asset.id, D(200), _within_period())
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    retained = next(r for r in result["equity"] if "Retained Earnings" in r["account_name"])
    assert retained["amount"] == D(-200)


def test_balance_sheet_no_synthetic_row_when_retained_earnings_zero(db):
    asset = _account(db, "asset")
    liability = _account(db, "liability")
    _post(db, asset.id, liability.id, D(100), _within_period())
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    assert all("Retained Earnings" not in r["account_name"] for r in result["equity"])


def test_balance_sheet_accounting_equation_always_balances(db):
    asset = _account(db, "asset")
    revenue = _account(db, "revenue")
    expense = _account(db, "expense")
    equity = _account(db, "equity")
    liability = _account(db, "liability")
    _post(db, asset.id, revenue.id, D(1000), _within_period())
    _post(db, expense.id, asset.id, D(250), _within_period())
    _post(db, asset.id, liability.id, D(400), _within_period())
    _post(db, asset.id, equity.id, D(2000), _within_period())

    result = report_service.compute_balance_sheet(db, PERIOD_END)
    assert result["total_assets"] == result["total_liabilities"] + result["total_equity"]
    assert result["balanced"] is True


def test_balance_sheet_zero_balance_account_excluded(db):
    asset = _account(db, "asset")
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    codes = {r["account_code"] for r in result["assets"]}
    assert asset.code not in codes


def test_balance_sheet_period_end_cutoff(db):
    asset = _account(db, "asset")
    liability = _account(db, "liability")
    _post(db, asset.id, liability.id, D(100), PERIOD_START)
    _post(db, asset.id, liability.id, D(50), PERIOD_END + timedelta(days=10))
    result = report_service.compute_balance_sheet(db, PERIOD_END)
    assert result["total_assets"] == D(100)


# --- compute_cash_flow ---------------------------------------------------


def test_cash_flow_simple_single_contra(db):
    cash = _account(db, "asset", is_cash=True)
    revenue = _account(db, "revenue")
    _post(db, cash.id, revenue.id, D(500), _within_period())
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    assert row["net_change"] == D(500)
    assert row["breakdown"] == [
        {"account_code": revenue.code, "account_name": revenue.name, "amount": D(500)}
    ]
    assert result["total_net_change"] == D(500)


def test_cash_flow_splits_proportionally_across_two_contras(db):
    cash = _account(db, "asset", is_cash=True)
    revenue = _account(db, "revenue")
    fee = _account(db, "expense")
    journal_service.post_entry(
        db,
        entry_date=_within_period(),
        memo="sale with fee",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(900), credit_amount=D(0)),
            LineInput(account_id=fee.id, debit_amount=D(100), credit_amount=D(0)),
            LineInput(account_id=revenue.id, debit_amount=D(0), credit_amount=D(1000)),
        ],
        source="manual",
    )
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    breakdown_by_code = {b["account_code"]: b["amount"] for b in row["breakdown"]}
    # cash movement (900) split proportionally by each contra's share of the
    # contra-side total (1000 revenue-credit + 100 fee-debit = 1100 abs total).
    assert breakdown_by_code[revenue.code] == D(900) * D(1000) / D(1100)
    assert breakdown_by_code[fee.code] == D(900) * D(100) / D(1100)


def test_cash_flow_accumulates_same_contra_across_entries(db):
    cash = _account(db, "asset", is_cash=True)
    revenue = _account(db, "revenue")
    _post(db, cash.id, revenue.id, D(100), _within_period())
    _post(db, cash.id, revenue.id, D(200), _within_period())
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    assert row["breakdown"] == [
        {"account_code": revenue.code, "account_name": revenue.name, "amount": D(300)}
    ]


def test_cash_flow_no_cash_accounts(db):
    active_cash = reconciliation_service.get_cash_accounts(db)
    for acc in active_cash:
        account_service.set_active(db, acc, False)
    try:
        result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
        assert result == {"accounts": [], "total_net_change": D(0)}
    finally:
        for acc in active_cash:
            account_service.set_active(db, acc, True)


def test_cash_flow_cash_account_no_activity(db):
    cash = _account(db, "asset", is_cash=True)
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    assert row["opening_balance"] == row["closing_balance"] == D(0)
    assert row["net_change"] == D(0)
    assert row["breakdown"] == []


def test_cash_flow_opening_balance_is_day_before_period_start(db):
    cash = _account(db, "asset", is_cash=True)
    revenue = _account(db, "revenue")
    _post(db, cash.id, revenue.id, D(50), PERIOD_START - timedelta(days=1))
    _post(db, cash.id, revenue.id, D(30), PERIOD_START)
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    assert row["opening_balance"] == D(50)
    assert row["closing_balance"] == D(80)


def test_cash_flow_breakdown_sorted_by_account_code(db):
    cash = _account(db, "asset", is_cash=True)
    contra_z = account_service.create_account(db, f"RS-ZZZ-{next(_seq)}", "Z contra", "revenue")
    contra_a = account_service.create_account(db, f"RS-AAA-{next(_seq)}", "A contra", "revenue")
    _post(db, cash.id, contra_z.id, D(10), _within_period())
    _post(db, cash.id, contra_a.id, D(20), _within_period())
    result = report_service.compute_cash_flow(db, PERIOD_START, PERIOD_END)
    row = next(r for r in result["accounts"] if r["account_code"] == cash.code)
    codes = [b["account_code"] for b in row["breakdown"]]
    assert codes == sorted(codes)


# --- get_generated_report -----------------------------------------------


def test_get_generated_report_no_match_returns_none(db):
    assert report_service.get_generated_report(db, "profit_loss", "monthly", PERIOD_START, PERIOD_END) is None


def test_get_generated_report_partial_filter_match_returns_none(db, test_user, no_cash_accounts):
    _make_reconciled_period(db, test_user)
    report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    wrong_end = PERIOD_END - timedelta(days=1)
    assert report_service.get_generated_report(db, "profit_loss", "monthly", PERIOD_START, wrong_end) is None


# --- generate_reports -----------------------------------------------------


def _make_reconciled_period(db, user):
    cash = _account(db, "asset", is_cash=True)
    reconciliation_service.submit_statement_balance(
        db, "monthly", PERIOD_START, PERIOD_END, cash.id, D(0), user
    )
    reconciliation_service.confirm_reconciliation(db, "monthly", PERIOD_START, PERIOD_END, cash.id, user)
    return cash


def test_generate_reports_success_creates_all_three(db, test_user, no_cash_accounts):
    _make_reconciled_period(db, test_user)
    reports = report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    assert {r.report_type for r in reports} == {"profit_loss", "balance_sheet", "cash_flow"}
    for report_type in ("profit_loss", "balance_sheet", "cash_flow"):
        assert report_service.get_generated_report(db, report_type, "monthly", PERIOD_START, PERIOD_END) is not None


def test_generate_reports_not_complete_raises_and_persists_nothing(db, test_user, no_cash_accounts):
    # Cash account exists but was never reconciled.
    _account(db, "asset", is_cash=True)
    with pytest.raises(ReportGenerationError, match="not complete"):
        report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    assert report_service.get_generated_report(db, "profit_loss", "monthly", PERIOD_START, PERIOD_END) is None


def test_generate_reports_regenerating_updates_in_place(db, test_user, no_cash_accounts):
    _make_reconciled_period(db, test_user)
    first = report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    first_ids = {r.report_type: r.id for r in first}
    second = report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    second_ids = {r.report_type: r.id for r in second}
    assert first_ids == second_ids


def test_generate_reports_regenerating_reflects_new_activity(db, test_user, no_cash_accounts):
    _make_reconciled_period(db, test_user)
    revenue = _account(db, "revenue")
    asset = _account(db, "asset")
    report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)

    _post(db, asset.id, revenue.id, D(777), _within_period())
    updated = report_service.generate_reports(db, "monthly", PERIOD_START, PERIOD_END, test_user)
    pl = next(r for r in updated if r.report_type == "profit_loss")
    assert pl.data["total_revenue"] == "777.00"


def test_serialize_decimal_quantized_to_two_places():
    assert _serialize(D(100)) == "100.00"
    assert _serialize(D("12.5")) == "12.50"


def test_serialize_nested_dict_and_list():
    payload = {"a": D(1), "rows": [{"amount": D("2.5")}, {"amount": D(0)}]}
    assert _serialize(payload) == {"a": "1.00", "rows": [{"amount": "2.50"}, {"amount": "0.00"}]}


def test_serialize_passthrough_non_decimal():
    assert _serialize("hello") == "hello"
    assert _serialize(42) == 42
    assert _serialize(None) is None
