from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session as DbSession

from app.models.account import Account
from app.models.base import utcnow
from app.models.generated_report import GeneratedReport
from app.models.user import User
from app.services import account_service, ledger_service, reconciliation_service


class ReportGenerationError(Exception):
    pass


def compute_profit_loss(db: DbSession, period_start: date, period_end: date) -> dict:
    accounts = account_service.list_accounts(db, include_inactive=False)
    revenue, expenses = [], []
    total_revenue = total_expenses = Decimal("0")

    for account in accounts:
        if account.account_type not in ("revenue", "expense"):
            continue
        amount = ledger_service.get_period_net_activity(db, account, period_start, period_end)
        if amount == 0:
            continue
        row = {"account_code": account.code, "account_name": account.name, "amount": amount}
        if account.account_type == "revenue":
            revenue.append(row)
            total_revenue += amount
        else:
            expenses.append(row)
            total_expenses += amount

    return {
        "revenue": revenue,
        "total_revenue": total_revenue,
        "expenses": expenses,
        "total_expenses": total_expenses,
        "net_income": total_revenue - total_expenses,
    }


def compute_balance_sheet(db: DbSession, period_end: date) -> dict:
    accounts = account_service.list_accounts(db, include_inactive=False)
    assets, liabilities, equity = [], [], []
    total_assets = total_liabilities = total_equity = Decimal("0")
    retained_earnings = Decimal("0")

    for account in accounts:
        if account.account_type == "revenue":
            retained_earnings += ledger_service.get_account_balance(db, account, as_of_date=period_end)
            continue
        if account.account_type == "expense":
            retained_earnings -= ledger_service.get_account_balance(db, account, as_of_date=period_end)
            continue

        balance = ledger_service.get_account_balance(db, account, as_of_date=period_end)
        if balance == 0:
            continue
        row = {"account_code": account.code, "account_name": account.name, "amount": balance}
        if account.account_type == "asset":
            assets.append(row)
            total_assets += balance
        elif account.account_type == "liability":
            liabilities.append(row)
            total_liabilities += balance
        elif account.account_type == "equity":
            equity.append(row)
            total_equity += balance

    # There's no period-close/closing-entry mechanism in this app, so revenue/expense
    # account balances are never zeroed out. Without a computed retained-earnings line,
    # assets would never equal liabilities + equity. This is a live-computed line, not a
    # posted account, so it always reflects cumulative earnings through period_end.
    if retained_earnings != 0:
        equity.append(
            {"account_code": "", "account_name": "Retained Earnings (computed)", "amount": retained_earnings}
        )
        total_equity += retained_earnings

    return {
        "assets": assets,
        "total_assets": total_assets,
        "liabilities": liabilities,
        "total_liabilities": total_liabilities,
        "equity": equity,
        "total_equity": total_equity,
        "balanced": total_assets == total_liabilities + total_equity,
    }


def compute_cash_flow(db: DbSession, period_start: date, period_end: date) -> dict:
    cash_accounts = reconciliation_service.get_cash_accounts(db)
    result_accounts = []

    for cash_account in cash_accounts:
        opening = ledger_service.get_account_balance(
            db, cash_account, as_of_date=period_start - timedelta(days=1)
        )
        closing = ledger_service.get_account_balance(db, cash_account, as_of_date=period_end)
        net_change = closing - opening

        sign = 1 if cash_account.normal_balance == "debit" else -1
        contra_totals: dict[int, Decimal] = {}
        for cash_line in ledger_service.get_lines_in_range(
            db, cash_account.id, date_from=period_start, date_to=period_end
        ):
            cash_movement = sign * (cash_line.debit_amount - cash_line.credit_amount)
            contra_lines = [line for line in cash_line.journal_entry.lines if line.id != cash_line.id]
            contra_total_abs = sum(
                (max(line.debit_amount, line.credit_amount) for line in contra_lines), Decimal("0")
            )
            if contra_total_abs == 0:
                continue
            # A journal entry can touch more than one contra account alongside the cash
            # line (e.g. revenue + fee in one entry); attribute the cash movement to each
            # contra account proportionally by its share of the entry's contra-side total,
            # rather than crediting the full movement to every contra account touched.
            for contra_line in contra_lines:
                contra_amount_abs = max(contra_line.debit_amount, contra_line.credit_amount)
                share = (contra_amount_abs / contra_total_abs) * cash_movement
                contra_totals[contra_line.account_id] = (
                    contra_totals.get(contra_line.account_id, Decimal("0")) + share
                )

        breakdown = []
        for account_id, amount in contra_totals.items():
            contra_account = db.get(Account, account_id)
            breakdown.append(
                {"account_code": contra_account.code, "account_name": contra_account.name, "amount": amount}
            )
        breakdown.sort(key=lambda row: row["account_code"])

        result_accounts.append(
            {
                "account_code": cash_account.code,
                "account_name": cash_account.name,
                "opening_balance": opening,
                "closing_balance": closing,
                "net_change": net_change,
                "breakdown": breakdown,
            }
        )

    total_net_change = sum((row["net_change"] for row in result_accounts), Decimal("0"))
    return {"accounts": result_accounts, "total_net_change": total_net_change}


def _serialize(value):
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    if isinstance(value, dict):
        return {key: _serialize(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def get_generated_report(
    db: DbSession, report_type: str, period_type: str, period_start: date, period_end: date
) -> GeneratedReport | None:
    return (
        db.query(GeneratedReport)
        .filter(
            GeneratedReport.report_type == report_type,
            GeneratedReport.period_type == period_type,
            GeneratedReport.period_start == period_start,
            GeneratedReport.period_end == period_end,
        )
        .first()
    )


def generate_reports(
    db: DbSession, period_type: str, period_start: date, period_end: date, user: User
) -> list[GeneratedReport]:
    status = reconciliation_service.get_period_status(db, period_type, period_start, period_end)
    if not status["is_complete"]:
        raise ReportGenerationError("Reconciliation for this period is not complete")

    computed = {
        "profit_loss": compute_profit_loss(db, period_start, period_end),
        "balance_sheet": compute_balance_sheet(db, period_end),
        "cash_flow": compute_cash_flow(db, period_start, period_end),
    }

    generated = []
    for report_type, data in computed.items():
        existing = get_generated_report(db, report_type, period_type, period_start, period_end)
        serialized = _serialize(data)
        if existing:
            existing.data = serialized
            existing.generated_at = utcnow()
            existing.generated_by_user_id = user.id
        else:
            existing = GeneratedReport(
                report_type=report_type,
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                generated_at=utcnow(),
                generated_by_user_id=user.id,
                data=serialized,
            )
            db.add(existing)
        generated.append(existing)

    db.commit()
    return generated
