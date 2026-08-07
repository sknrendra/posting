from datetime import date
from decimal import Decimal as D

import pytest

from app.models.account import Account
from app.services import journal_service, reconciliation_service
from app.services.journal_service import LineInput
from app.services.reconciliation_service import ReconciliationError


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


def test_start_reconciliation_defaults_starting_balance_to_zero_when_no_prior(db, test_user):
    account = _account(db, "1020")
    recon, warning = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("100.00"), test_user
    )
    assert recon.starting_balance == D("0.00")
    assert warning is None


def test_start_reconciliation_locks_starting_balance_to_previous_completed_ending_balance(db, test_user):
    account = _account(db, "1020")
    recon1, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0.00"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon1, test_user)

    recon2, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 2, 28), D("0.00"), test_user
    )
    assert recon2.starting_balance == recon1.statement_ending_balance


def test_start_reconciliation_rejects_second_in_progress_for_same_account(db, test_user):
    account = _account(db, "1020")
    reconciliation_service.start_reconciliation(db, account.id, date(2026, 1, 31), D("0"), test_user)
    with pytest.raises(ReconciliationError):
        reconciliation_service.start_reconciliation(db, account.id, date(2026, 2, 28), D("0"), test_user)


def test_start_reconciliation_rejects_non_increasing_statement_date(db, test_user):
    account = _account(db, "1020")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon, test_user)
    with pytest.raises(ReconciliationError):
        reconciliation_service.start_reconciliation(db, account.id, date(2026, 1, 15), D("0"), test_user)


def test_start_reconciliation_rejects_duplicate_statement_date(db, test_user):
    account = _account(db, "1020")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon, test_user)
    with pytest.raises(ReconciliationError):
        reconciliation_service.start_reconciliation(db, account.id, date(2026, 1, 31), D("0"), test_user)


def test_successful_zero_out_completion(db, test_user):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line = next(l for l in entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon, line.id, True)

    diff = reconciliation_service.compute_difference(db, recon)
    assert diff["is_balanced"]

    completed = reconciliation_service.complete_reconciliation(db, recon, test_user)
    assert completed.status == "completed"
    assert completed.completed_by_user_id == test_user.id
    assert completed.completed_at is not None
    db.refresh(line)
    assert line.reconciliation_id == recon.id


def test_complete_reconciliation_rejects_when_out_of_balance(db, test_user):
    account = _account(db, "1020")
    _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    with pytest.raises(ReconciliationError):
        reconciliation_service.complete_reconciliation(db, recon, test_user)


def test_continuity_warning_is_none_on_happy_path(db, test_user):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line = next(l for l in entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon, line.id, True)
    reconciliation_service.complete_reconciliation(db, recon, test_user)

    assert reconciliation_service.get_continuity_warning(db, account) is None


def test_continuity_warning_flags_integrity_mismatch(db, test_user):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line = next(l for l in entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon, line.id, True)
    reconciliation_service.complete_reconciliation(db, recon, test_user)

    # Simulate the locked line's linkage drifting without going through undo_reconciliation.
    line.reconciliation_id = None
    db.commit()

    warning = reconciliation_service.get_continuity_warning(db, account)
    assert warning is not None
    assert str(recon.id) in warning


def test_outstanding_items_carry_forward_into_next_reconciliation(db, test_user):
    account = _account(db, "1020")
    cleared_entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    outstanding_entry = _post(db, date(2026, 1, 20), "1020", "5100", D("50.00"), cash_is_debit=False)

    recon_a, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    cleared_line = next(l for l in cleared_entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon_a, cleared_line.id, True)
    reconciliation_service.complete_reconciliation(db, recon_a, test_user)

    recon_b, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 2, 28), D("450.00"), test_user
    )
    clearable_ids = {l.id for l in reconciliation_service.get_clearable_lines(db, recon_b)}
    outstanding_line = next(l for l in outstanding_entry.lines if l.account_id == account.id)
    assert outstanding_line.id in clearable_ids


def test_undo_reconciliation_unlocks_lines(db, test_user, admin_user):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line = next(l for l in entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon, line.id, True)
    reconciliation_service.complete_reconciliation(db, recon, test_user)

    result = reconciliation_service.undo_reconciliation(db, recon, admin_user)
    assert result["reconciliation"].status == "undone"
    assert result["affected_later_reconciliations"] == []
    db.refresh(line)
    assert line.reconciliation_id is None
    assert line.cleared_status == "cleared"

    recon_b, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 2, 28), D("0"), test_user
    )
    clearable_ids = {l.id for l in reconciliation_service.get_clearable_lines(db, recon_b)}
    assert line.id in clearable_ids


def test_undo_reconciliation_warns_about_later_completed_reconciliations(db, test_user, admin_user):
    account = _account(db, "1020")
    entry1 = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon1, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line1 = next(l for l in entry1.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon1, line1.id, True)
    reconciliation_service.complete_reconciliation(db, recon1, test_user)

    recon2, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 2, 28), D("500.00"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon2, test_user)

    result = reconciliation_service.undo_reconciliation(db, recon1, admin_user)
    assert [r.id for r in result["affected_later_reconciliations"]] == [recon2.id]


def test_undo_reconciliation_rejects_non_completed_status(db, test_user, admin_user):
    account = _account(db, "1020")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    with pytest.raises(ReconciliationError):
        reconciliation_service.undo_reconciliation(db, recon, admin_user)


def test_add_missing_transaction_creates_balanced_entry(db, test_user):
    account = _account(db, "1020")
    fee_account = _account(db, "5100")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("-25.00"), test_user
    )
    entry = reconciliation_service.add_missing_transaction(
        db,
        recon,
        counter_account_id=fee_account.id,
        amount=D("25.00"),
        movement="withdrawal",
        entry_date=date(2026, 1, 20),
        memo="Bank fee",
        user=test_user,
    )
    assert len(entry.lines) == 2
    total_debit = sum((l.debit_amount for l in entry.lines), D("0"))
    total_credit = sum((l.credit_amount for l in entry.lines), D("0"))
    assert total_debit == total_credit == D("25.00")


def test_add_missing_transaction_defaults_to_cleared(db, test_user):
    account = _account(db, "1020")
    fee_account = _account(db, "5100")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("-25.00"), test_user
    )
    entry = reconciliation_service.add_missing_transaction(
        db,
        recon,
        counter_account_id=fee_account.id,
        amount=D("25.00"),
        movement="withdrawal",
        entry_date=date(2026, 1, 20),
        memo="Bank fee",
        user=test_user,
    )
    account_line = next(l for l in entry.lines if l.account_id == account.id)
    assert account_line.cleared_status == "cleared"
    counter_line = next(l for l in entry.lines if l.account_id == fee_account.id)
    assert counter_line.cleared_status == "uncleared"

    diff = reconciliation_service.compute_difference(db, recon)
    assert diff["is_balanced"]


def test_add_missing_transaction_rejects_when_not_in_progress(db, test_user):
    account = _account(db, "1020")
    fee_account = _account(db, "5100")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon, test_user)
    with pytest.raises(ReconciliationError):
        reconciliation_service.add_missing_transaction(
            db,
            recon,
            counter_account_id=fee_account.id,
            amount=D("10.00"),
            movement="withdrawal",
            entry_date=date(2026, 1, 20),
            memo="late fee",
            user=test_user,
        )


def test_add_missing_transaction_rejects_entry_date_after_statement_date(db, test_user):
    account = _account(db, "1020")
    fee_account = _account(db, "5100")
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    with pytest.raises(ReconciliationError):
        reconciliation_service.add_missing_transaction(
            db,
            recon,
            counter_account_id=fee_account.id,
            amount=D("10.00"),
            movement="withdrawal",
            entry_date=date(2026, 2, 1),
            memo="late fee",
            user=test_user,
        )


def test_toggle_line_cleared_rejects_line_locked_into_other_reconciliation(db, test_user):
    account = _account(db, "1020")
    entry = _post(db, date(2026, 1, 5), "1020", "4000", D("500.00"), cash_is_debit=True)
    recon1, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("500.00"), test_user
    )
    line = next(l for l in entry.lines if l.account_id == account.id)
    reconciliation_service.toggle_line_cleared(db, recon1, line.id, True)
    reconciliation_service.complete_reconciliation(db, recon1, test_user)

    recon2, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 2, 28), D("500.00"), test_user
    )
    with pytest.raises(ReconciliationError):
        reconciliation_service.toggle_line_cleared(db, recon2, line.id, False)


def test_get_period_status_reflects_new_completed_reconciliation_semantics(db, test_user):
    accounts = reconciliation_service.list_reconcilable_accounts(db)
    total = len(accounts)

    status = reconciliation_service.get_period_status(
        db, "monthly", date(2026, 1, 1), date(2026, 1, 31)
    )
    assert status == {"completed": 0, "total": total, "is_complete": False}

    account = accounts[0]
    recon, _ = reconciliation_service.start_reconciliation(
        db, account.id, date(2026, 1, 31), D("0"), test_user
    )
    reconciliation_service.complete_reconciliation(db, recon, test_user)

    status2 = reconciliation_service.get_period_status(
        db, "monthly", date(2026, 1, 1), date(2026, 1, 31)
    )
    assert status2["completed"] == 1
    assert status2["is_complete"] == (total == 1)
