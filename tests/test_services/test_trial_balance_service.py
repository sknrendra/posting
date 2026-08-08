import itertools
from datetime import date
from decimal import Decimal as D

from app.models.journal_entry import JournalEntry, JournalLine
from app.services import account_service, journal_service, trial_balance_service
from app.services.journal_service import LineInput

_seq = itertools.count()

AS_OF = date(2026, 7, 31)


def _accounts(db):
    n = next(_seq)
    asset = account_service.create_account(db, f"TB-ASSET-{n}", "Test Asset", "asset")
    revenue = account_service.create_account(db, f"TB-REV-{n}", "Test Revenue", "revenue")
    return asset, revenue


def _post(db, debit_id, credit_id, amount, entry_date=AS_OF):
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


def test_compute_places_normal_balances_in_correct_columns(db):
    asset, revenue = _accounts(db)
    _post(db, asset.id, revenue.id, D(500))
    result = trial_balance_service.compute(db, AS_OF)
    by_code = {r["account"].code: r for r in result["rows"]}
    assert by_code[asset.code]["debit"] == D(500)
    assert by_code[asset.code]["credit"] == D(0)
    assert by_code[revenue.code]["credit"] == D(500)
    assert by_code[revenue.code]["debit"] == D(0)


def test_compute_zero_balance_account_excluded(db):
    asset, _revenue = _accounts(db)
    result = trial_balance_service.compute(db, AS_OF)
    codes = {r["account"].code for r in result["rows"]}
    assert asset.code not in codes


def test_compute_debit_normal_abnormal_balance_shown_positive_in_credit_column(db):
    asset, revenue = _accounts(db)
    # Credit the asset account more than it's debited -> abnormal (negative raw) balance.
    journal_service.post_entry(
        db,
        entry_date=AS_OF,
        memo="abnormal",
        lines=[
            LineInput(account_id=asset.id, debit_amount=D(0), credit_amount=D(75)),
            LineInput(account_id=revenue.id, debit_amount=D(75), credit_amount=D(0)),
        ],
        source="manual",
    )
    result = trial_balance_service.compute(db, AS_OF)
    row = next(r for r in result["rows"] if r["account"].code == asset.code)
    assert row["debit"] == D(0)
    assert row["credit"] == D(75)


def test_compute_credit_normal_abnormal_balance_shown_positive_in_debit_column(db):
    asset, revenue = _accounts(db)
    journal_service.post_entry(
        db,
        entry_date=AS_OF,
        memo="abnormal",
        lines=[
            LineInput(account_id=revenue.id, debit_amount=D(60), credit_amount=D(0)),
            LineInput(account_id=asset.id, debit_amount=D(0), credit_amount=D(60)),
        ],
        source="manual",
    )
    result = trial_balance_service.compute(db, AS_OF)
    row = next(r for r in result["rows"] if r["account"].code == revenue.code)
    assert row["credit"] == D(0)
    assert row["debit"] == D(60)


def test_compute_balanced_books(db):
    asset, revenue = _accounts(db)
    _post(db, asset.id, revenue.id, D(1234))
    result = trial_balance_service.compute(db, AS_OF)
    assert result["total_debit"] == result["total_credit"]
    assert result["balanced"] is True


def test_compute_inactive_account_excluded(db):
    asset, revenue = _accounts(db)
    _post(db, asset.id, revenue.id, D(100))
    account_service.set_active(db, asset, False)
    result = trial_balance_service.compute(db, AS_OF)
    codes = {r["account"].code for r in result["rows"]}
    assert asset.code not in codes


def test_compute_no_activity_empty(db):
    result = trial_balance_service.compute(db, AS_OF)
    assert result["rows"] == []
    assert result["total_debit"] == D(0)
    assert result["total_credit"] == D(0)
    assert result["balanced"] is True


def test_compute_as_of_date_excludes_later_activity(db):
    asset, revenue = _accounts(db)
    _post(db, asset.id, revenue.id, D(100), entry_date=date(2026, 1, 1))
    _post(db, asset.id, revenue.id, D(50), entry_date=date(2026, 12, 1))
    result = trial_balance_service.compute(db, date(2026, 6, 1))
    row = next(r for r in result["rows"] if r["account"].code == asset.code)
    assert row["debit"] == D(100)


def test_compute_reports_imbalance_when_books_are_corrupt(db):
    """journal_service.post_entry always keeps debits == credits; simulate a
    corrupted ledger by inserting a lone unbalanced journal line directly,
    bypassing the service, to confirm compute() surfaces it rather than
    silently balancing."""
    asset, revenue = _accounts(db)
    entry = JournalEntry(entry_date=AS_OF, memo="corrupt", source="manual")
    entry.lines.append(JournalLine(account_id=asset.id, debit_amount=D(100), credit_amount=D(0)))
    db.add(entry)
    db.commit()
    result = trial_balance_service.compute(db, AS_OF)
    assert result["balanced"] is False
    assert result["total_debit"] != result["total_credit"]
