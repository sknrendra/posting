from sqlalchemy.orm import Session as DbSession

from app.models.account import Account

TYPE_NORMAL_BALANCE = {
    "asset": "debit",
    "liability": "credit",
    "equity": "credit",
    "revenue": "credit",
    "expense": "debit",
}

DEFAULT_CHART_OF_ACCOUNTS = [
    # code, name, type, is_cash_account
    ("1000", "Cash on Hand", "asset", True),
    ("1010", "Payment Gateway", "asset", True),
    ("1020", "Bank Account", "asset", True),
    ("2000", "Client Funds / Escrow Liability", "liability", False),
    ("2010", "Provider Payable", "liability", False),
    ("3000", "Owner's Equity", "equity", False),
    ("4000", "Service Revenue", "revenue", False),
    ("5100", "Payment Processing Fees", "expense", False),
    ("5200", "Server / Hosting Expense", "expense", False),
    ("5300", "Software Subscriptions Expense", "expense", False),
    ("5900", "Refunds / Chargebacks", "expense", False),
]


def list_accounts(db: DbSession, include_inactive: bool = True) -> list[Account]:
    query = db.query(Account)
    if not include_inactive:
        query = query.filter(Account.is_active.is_(True))
    return query.order_by(Account.code).all()


def get_account(db: DbSession, account_id: int) -> Account | None:
    return db.get(Account, account_id)


def create_account(
    db: DbSession,
    code: str,
    name: str,
    account_type: str,
    is_cash_account: bool = False,
    description: str | None = None,
) -> Account:
    account = Account(
        code=code,
        name=name,
        account_type=account_type,
        normal_balance=TYPE_NORMAL_BALANCE[account_type],
        is_cash_account=is_cash_account,
        is_active=True,
        description=description,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def update_account(
    db: DbSession,
    account: Account,
    name: str,
    description: str | None,
    is_cash_account: bool,
) -> Account:
    account.name = name
    account.description = description
    account.is_cash_account = is_cash_account
    db.commit()
    return account


def set_active(db: DbSession, account: Account, is_active: bool) -> Account:
    account.is_active = is_active
    db.commit()
    return account


def seed_default_chart_of_accounts(db: DbSession) -> None:
    # Idempotent per-code (not "table is empty") since migrations may seed a
    # handful of accounts (e.g. invoice-related ones) before this ever runs —
    # a table-count guard would otherwise skip the whole default chart.
    existing_codes = {code for (code,) in db.query(Account.code).all()}
    for code, name, account_type, is_cash in DEFAULT_CHART_OF_ACCOUNTS:
        if code in existing_codes:
            continue
        create_account(db, code, name, account_type, is_cash_account=is_cash)
