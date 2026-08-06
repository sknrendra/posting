import itertools
from datetime import date, timedelta
from decimal import Decimal as D

from app.services import account_service, journal_service, ledger_service
from app.services.journal_service import LineInput

_seq = itertools.count()


def _accounts(db):
    n = next(_seq)
    debit_normal = account_service.create_account(db, f"TL-CASH-{n}", "Test Cash", "asset")
    credit_normal = account_service.create_account(db, f"TL-REV-{n}", "Test Revenue", "revenue")
    return debit_normal, credit_normal


def _post(db, debit_account_id, credit_account_id, amount, entry_date):
    journal_service.post_entry(
        db,
        entry_date=entry_date,
        memo="x",
        lines=[
            LineInput(account_id=debit_account_id, debit_amount=amount, credit_amount=D(0)),
            LineInput(account_id=credit_account_id, debit_amount=D(0), credit_amount=amount),
        ],
        source="manual",
    )


# --- get_lines_in_range -----------------------------------------------------


def test_get_lines_in_range_no_filters_returns_all(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(200), date(2026, 2, 1))
    lines = ledger_service.get_lines_in_range(db, cash.id)
    assert len(lines) == 2


def test_get_lines_in_range_date_from_only(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(200), date(2026, 2, 1))
    lines = ledger_service.get_lines_in_range(db, cash.id, date_from=date(2026, 1, 15))
    assert [l.debit_amount for l in lines] == [D(200)]


def test_get_lines_in_range_date_to_only(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(200), date(2026, 2, 1))
    lines = ledger_service.get_lines_in_range(db, cash.id, date_to=date(2026, 1, 15))
    assert [l.debit_amount for l in lines] == [D(100)]


def test_get_lines_in_range_inclusive_bounds(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(200), date(2026, 1, 31))
    lines = ledger_service.get_lines_in_range(
        db, cash.id, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)
    )
    assert len(lines) == 2


def test_get_lines_in_range_inverted_range_returns_empty(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 15))
    lines = ledger_service.get_lines_in_range(
        db, cash.id, date_from=date(2026, 2, 1), date_to=date(2026, 1, 1)
    )
    assert lines == []


def test_get_lines_in_range_no_activity_returns_empty(db):
    cash, _revenue = _accounts(db)
    assert ledger_service.get_lines_in_range(db, cash.id) == []


def test_get_lines_in_range_excludes_other_accounts(db):
    cash, revenue = _accounts(db)
    other_cash, other_revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, other_cash.id, other_revenue.id, D(999), date(2026, 1, 1))
    lines = ledger_service.get_lines_in_range(db, cash.id)
    assert [l.debit_amount for l in lines] == [D(100)]


# --- get_account_balance ----------------------------------------------------


def test_balance_debit_normal_more_debits_than_credits(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(300), date(2026, 1, 1))
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 2),
        memo="partial credit back",
        lines=[
            LineInput(account_id=revenue.id, debit_amount=D(100), credit_amount=D(0)),
            LineInput(account_id=cash.id, debit_amount=D(0), credit_amount=D(100)),
        ],
        source="manual",
    )
    assert ledger_service.get_account_balance(db, cash) == D(200)


def test_balance_debit_normal_abnormal_negative(db):
    cash, revenue = _accounts(db)
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 1),
        memo="credit only cash (abnormal)",
        lines=[
            LineInput(account_id=cash.id, debit_amount=D(0), credit_amount=D(50)),
            LineInput(account_id=revenue.id, debit_amount=D(50), credit_amount=D(0)),
        ],
        source="manual",
    )
    assert ledger_service.get_account_balance(db, cash) == D(-50)


def test_balance_credit_normal_more_credits_than_debits(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(300), date(2026, 1, 1))
    assert ledger_service.get_account_balance(db, revenue) == D(300)


def test_balance_credit_normal_abnormal_negative(db):
    cash, revenue = _accounts(db)
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 1),
        memo="debit only revenue (abnormal)",
        lines=[
            LineInput(account_id=revenue.id, debit_amount=D(40), credit_amount=D(0)),
            LineInput(account_id=cash.id, debit_amount=D(0), credit_amount=D(40)),
        ],
        source="manual",
    )
    assert ledger_service.get_account_balance(db, revenue) == D(-40)


def test_balance_as_of_date_excludes_future_lines(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(50), date(2026, 6, 1))
    assert ledger_service.get_account_balance(db, cash, as_of_date=date(2026, 3, 1)) == D(100)


def test_balance_as_of_date_none_includes_everything(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(50), date(2026, 6, 1))
    assert ledger_service.get_account_balance(db, cash, as_of_date=None) == D(150)


def test_balance_zero_activity(db):
    cash, _revenue = _accounts(db)
    assert ledger_service.get_account_balance(db, cash) == D(0)


def test_balance_precise_decimal(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D("12345.67"), date(2026, 1, 1))
    balance = ledger_service.get_account_balance(db, cash)
    assert balance == D("12345.67")
    assert str(balance) == "12345.67"


def test_balance_returns_to_zero_after_void_reversal(db):
    from app.services import invoice_service, invoice_settings_service
    from app.services.invoice_service import InvoiceHeaderInput, InvoiceLineInput

    settings = invoice_settings_service.get_settings(db)
    ar_account = account_service.get_account(db, settings.ar_account_id)

    invoice = invoice_service.create_draft(
        db,
        InvoiceHeaderInput(
            invoice_date=date(2026, 7, 29),
            customer_name="Acme",
            lines=[InvoiceLineInput(description="Consulting", quantity=D(1), rate=D(1000))],
        ),
        user_id=None,
    )
    posted = invoice_service.post_invoice(db, invoice, user_id=None)
    balance_after_post = ledger_service.get_account_balance(db, ar_account)
    assert balance_after_post == D(1000)

    invoice_service.void_invoice(db, posted, "test void", user_id=None)
    assert ledger_service.get_account_balance(db, ar_account) == D(0)


# --- get_balances_as_of ------------------------------------------------------


def test_balances_as_of_multiple_accounts(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(500), date(2026, 1, 1))
    balances = ledger_service.get_balances_as_of(db, [cash, revenue])
    assert balances == {cash.id: D(500), revenue.id: D(500)}


def test_balances_as_of_empty_list(db):
    assert ledger_service.get_balances_as_of(db, []) == {}


def test_balances_as_of_duplicate_account_in_list(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(10), date(2026, 1, 1))
    balances = ledger_service.get_balances_as_of(db, [cash, cash])
    assert balances == {cash.id: D(10)}


# --- get_period_net_activity -------------------------------------------------


def test_period_net_activity_only_counts_window(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 15))
    net = ledger_service.get_period_net_activity(db, cash, date(2026, 1, 1), date(2026, 1, 31))
    assert net == D(100)


def test_period_net_activity_excludes_activity_before_window(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2025, 12, 1))
    net = ledger_service.get_period_net_activity(db, cash, date(2026, 1, 1), date(2026, 1, 31))
    assert net == D(0)


def test_period_net_activity_excludes_activity_after_window(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 2, 1))
    net = ledger_service.get_period_net_activity(db, cash, date(2026, 1, 1), date(2026, 1, 31))
    assert net == D(0)


def test_period_net_activity_no_activity_in_window_but_activity_outside(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(1000), date(2025, 1, 1))
    net = ledger_service.get_period_net_activity(db, cash, date(2026, 1, 1), date(2026, 1, 31))
    assert net == D(0)


def test_period_net_activity_single_day_window(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 15))
    _post(db, cash.id, revenue.id, D(50), date(2026, 1, 16))
    net = ledger_service.get_period_net_activity(db, cash, date(2026, 1, 15), date(2026, 1, 15))
    assert net == D(100)


# --- get_running_ledger -------------------------------------------------------


def test_running_ledger_no_date_from_opening_balance_zero(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    result = ledger_service.get_running_ledger(db, cash)
    assert result["opening_balance"] == D(0)


def test_running_ledger_date_from_with_prior_activity(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(50), date(2026, 2, 1))
    result = ledger_service.get_running_ledger(db, cash, date_from=date(2026, 2, 1))
    assert result["opening_balance"] == D(100)
    assert len(result["rows"]) == 1


def test_running_ledger_date_from_no_prior_activity(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(50), date(2026, 2, 1))
    result = ledger_service.get_running_ledger(db, cash, date_from=date(2026, 2, 1))
    assert result["opening_balance"] == D(0)


def test_running_ledger_accumulates_debit_normal(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 2),
        memo="partial credit",
        lines=[
            LineInput(account_id=revenue.id, debit_amount=D(30), credit_amount=D(0)),
            LineInput(account_id=cash.id, debit_amount=D(0), credit_amount=D(30)),
        ],
        source="manual",
    )
    result = ledger_service.get_running_ledger(db, cash)
    balances = [row["balance"] for row in result["rows"]]
    assert balances == [D(100), D(70)]
    assert result["closing_balance"] == D(70)


def test_running_ledger_accumulates_credit_normal_sign_flip(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    result = ledger_service.get_running_ledger(db, revenue)
    assert result["rows"][0]["balance"] == D(100)
    assert result["closing_balance"] == D(100)


def test_running_ledger_no_lines_in_range(db):
    cash, _revenue = _accounts(db)
    result = ledger_service.get_running_ledger(db, cash, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    assert result["rows"] == []
    assert result["closing_balance"] == result["opening_balance"] == D(0)


def test_running_ledger_rows_ordered_chronologically(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(20), date(2026, 3, 1))
    _post(db, cash.id, revenue.id, D(10), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(30), date(2026, 2, 1))
    result = ledger_service.get_running_ledger(db, cash)
    amounts = [row["line"].debit_amount for row in result["rows"]]
    assert amounts == [D(10), D(30), D(20)]


def test_running_ledger_date_to_only_opening_balance_is_zero_but_closing_correct(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 1, 1))
    _post(db, cash.id, revenue.id, D(50), date(2026, 2, 1))
    result = ledger_service.get_running_ledger(db, cash, date_to=date(2026, 2, 1))
    # date_to alone does not trigger an opening-balance lookup — opening_balance
    # stays 0 even though the range includes all prior activity via `rows`.
    assert result["opening_balance"] == D(0)
    assert result["closing_balance"] == D(150)


# --- boundary ------------------------------------------------------------


def test_balance_as_of_date_before_any_activity_is_zero(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2026, 6, 1))
    assert ledger_service.get_account_balance(db, cash, as_of_date=date(2026, 1, 1)) == D(0)


def test_running_ledger_opening_balance_crosses_year_boundary(db):
    cash, revenue = _accounts(db)
    _post(db, cash.id, revenue.id, D(100), date(2025, 12, 31))
    result = ledger_service.get_running_ledger(db, cash, date_from=date(2026, 1, 1))
    assert result["opening_balance"] == D(100)
