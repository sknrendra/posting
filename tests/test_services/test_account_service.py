import itertools

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import account_service, reconciliation_service
from app.services.account_service import DEFAULT_CHART_OF_ACCOUNTS, TYPE_NORMAL_BALANCE

_seq = itertools.count()


def _code():
    return f"TA-{next(_seq)}"


# --- list_accounts -----------------------------------------------------------


def test_list_accounts_include_inactive_default(db):
    active = account_service.create_account(db, _code(), "Active", "asset")
    inactive = account_service.create_account(db, _code(), "Inactive", "asset")
    account_service.set_active(db, inactive, False)
    codes = {a.code for a in account_service.list_accounts(db)}
    assert active.code in codes
    assert inactive.code in codes


def test_list_accounts_exclude_inactive(db):
    inactive = account_service.create_account(db, _code(), "Inactive", "asset")
    account_service.set_active(db, inactive, False)
    codes = {a.code for a in account_service.list_accounts(db, include_inactive=False)}
    assert inactive.code not in codes


def test_list_accounts_ordered_by_code(db):
    account_service.create_account(db, "TA-Z-99999", "Z Account", "asset")
    account_service.create_account(db, "TA-A-00000", "A Account", "asset")
    accounts = account_service.list_accounts(db)
    codes = [a.code for a in accounts]
    assert codes == sorted(codes)


def test_list_accounts_default_chart_present(db):
    codes = {a.code for a in account_service.list_accounts(db)}
    for code, *_rest in DEFAULT_CHART_OF_ACCOUNTS:
        assert code in codes


# --- get_account ---------------------------------------------------------


def test_get_account_existing(db):
    account = account_service.create_account(db, _code(), "Foo", "asset")
    fetched = account_service.get_account(db, account.id)
    assert fetched.id == account.id


def test_get_account_nonexistent_returns_none(db):
    assert account_service.get_account(db, 999999) is None


# --- create_account --------------------------------------------------------


@pytest.mark.parametrize(
    "account_type,expected_normal",
    [
        ("asset", "debit"),
        ("liability", "credit"),
        ("equity", "credit"),
        ("revenue", "credit"),
        ("expense", "debit"),
    ],
)
def test_create_account_normal_balance_by_type(db, account_type, expected_normal):
    account = account_service.create_account(db, _code(), "X", account_type)
    assert account.normal_balance == expected_normal
    assert TYPE_NORMAL_BALANCE[account_type] == expected_normal


def test_create_account_is_cash_account_flag(db):
    account = account_service.create_account(db, _code(), "Cash", "asset", is_cash_account=True)
    assert account.is_cash_account is True
    assert account in reconciliation_service.get_cash_accounts(db)


def test_create_account_description_none(db):
    account = account_service.create_account(db, _code(), "X", "asset", description=None)
    assert account.description is None


def test_create_account_defaults_active(db):
    account = account_service.create_account(db, _code(), "X", "asset")
    assert account.is_active is True
    assert account.id in {a.id for a in account_service.list_accounts(db, include_inactive=False)}


def test_create_account_duplicate_code_raises_integrity_error(db):
    code = _code()
    account_service.create_account(db, code, "First", "asset")
    with pytest.raises(IntegrityError):
        account_service.create_account(db, code, "Second", "asset")
    db.rollback()


def test_create_account_invalid_type_raises_key_error(db):
    with pytest.raises(KeyError):
        account_service.create_account(db, _code(), "Bogus", "bogus")


# --- update_account --------------------------------------------------------


def test_update_account_fields(db):
    account = account_service.create_account(db, _code(), "Old Name", "asset", description="old")
    updated = account_service.update_account(db, account, "New Name", "new desc", True)
    assert updated.name == "New Name"
    assert updated.description == "new desc"
    assert updated.is_cash_account is True
    assert updated.account_type == "asset"
    assert updated.normal_balance == "debit"


def test_update_account_clears_description(db):
    account = account_service.create_account(db, _code(), "X", "asset", description="something")
    account_service.update_account(db, account, "X", None, False)
    assert account.description is None


def test_update_account_toggle_cash_off_removes_from_cash_accounts(db):
    account = account_service.create_account(db, _code(), "X", "asset", is_cash_account=True)
    assert account in reconciliation_service.get_cash_accounts(db)
    account_service.update_account(db, account, "X", None, False)
    assert account not in reconciliation_service.get_cash_accounts(db)


# --- set_active --------------------------------------------------------


def test_set_active_deactivate(db):
    account = account_service.create_account(db, _code(), "X", "asset")
    account_service.set_active(db, account, False)
    assert account.is_active is False
    assert account.id not in {a.id for a in account_service.list_accounts(db, include_inactive=False)}


def test_set_active_reactivate(db):
    account = account_service.create_account(db, _code(), "X", "asset")
    account_service.set_active(db, account, False)
    account_service.set_active(db, account, True)
    assert account.is_active is True
    assert account.id in {a.id for a in account_service.list_accounts(db, include_inactive=False)}


def test_set_active_deactivating_account_with_history_succeeds(db):
    from datetime import date

    from app.services import journal_service
    from app.services.journal_service import LineInput

    cash = account_service.create_account(db, _code(), "Cash", "asset")
    revenue = account_service.create_account(db, _code(), "Revenue", "revenue")
    journal_service.post_entry(
        db,
        entry_date=date(2026, 1, 1),
        memo="x",
        lines=[
            LineInput(account_id=cash.id, debit_amount=100, credit_amount=0),
            LineInput(account_id=revenue.id, debit_amount=0, credit_amount=100),
        ],
        source="manual",
    )
    account_service.set_active(db, cash, False)
    assert cash.is_active is False


def test_set_active_deactivating_mapped_invoice_settings_account_has_no_guard(db):
    from app.services import invoice_settings_service

    settings = invoice_settings_service.get_settings(db)
    ar_account = account_service.get_account(db, settings.ar_account_id)
    account_service.set_active(db, ar_account, False)
    try:
        assert ar_account.is_active is False
        # No exception raised, no automatic un-mapping — the service has no guard here.
        assert invoice_settings_service.get_settings(db).ar_account_id == ar_account.id
    finally:
        account_service.set_active(db, ar_account, True)


# --- seed_default_chart_of_accounts --------------------------------------


def test_seed_default_chart_all_codes_present_with_correct_fields(db):
    # conftest already seeds this once at session start; re-verify the invariant.
    accounts_by_code = {a.code: a for a in account_service.list_accounts(db)}
    for code, name, account_type, is_cash in DEFAULT_CHART_OF_ACCOUNTS:
        account = accounts_by_code[code]
        assert account.name == name
        assert account.account_type == account_type
        assert account.normal_balance == TYPE_NORMAL_BALANCE[account_type]
        assert account.is_cash_account is is_cash


def test_seed_default_chart_idempotent(db):
    before_count = len(account_service.list_accounts(db))
    account_service.seed_default_chart_of_accounts(db)
    account_service.seed_default_chart_of_accounts(db)
    after_count = len(account_service.list_accounts(db))
    assert after_count == before_count


def test_seed_default_chart_leaves_unrelated_accounts_untouched(db):
    unrelated = account_service.create_account(db, _code(), "Unrelated", "asset")
    account_service.seed_default_chart_of_accounts(db)
    refetched = account_service.get_account(db, unrelated.id)
    assert refetched.name == "Unrelated"
