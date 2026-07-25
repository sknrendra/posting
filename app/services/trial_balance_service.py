from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session as DbSession

from app.services import account_service, ledger_service


def compute(db: DbSession, as_of_date: date) -> dict:
    accounts = account_service.list_accounts(db, include_inactive=False)
    rows = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for account in accounts:
        balance = ledger_service.get_account_balance(db, account, as_of_date=as_of_date)
        if balance == 0:
            continue
        # Place the balance in its normal-balance column; an abnormal (opposite-sign)
        # balance is placed as a positive number in the other column so both columns
        # always sum to something comparable, rather than showing a negative number.
        if account.normal_balance == "debit":
            debit_col = balance if balance >= 0 else Decimal("0")
            credit_col = -balance if balance < 0 else Decimal("0")
        else:
            credit_col = balance if balance >= 0 else Decimal("0")
            debit_col = -balance if balance < 0 else Decimal("0")
        rows.append({"account": account, "debit": debit_col, "credit": credit_col})
        total_debit += debit_col
        total_credit += credit_col

    return {
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }
