"""seed invoice chart of accounts

Revision ID: 3fe23b9c6599
Revises: 7ae2d8df3607
Create Date: 2026-07-29 05:58:57.025357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3fe23b9c6599'
down_revision: Union[str, None] = '7ae2d8df3607'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# code, name, account_type, normal_balance
NEW_ACCOUNTS = [
    ("1100", "Accounts Receivable", "asset", "debit"),
    ("2020", "Tax Payable", "liability", "credit"),
    ("2030", "Unearned Revenue", "liability", "credit"),
]


def upgrade() -> None:
    # INSERT OR IGNORE relies on the unique index on accounts.code, so re-running
    # this migration (e.g. against a DB that already has these codes) is a no-op.
    conn = op.get_bind()
    for code, name, account_type, normal_balance in NEW_ACCOUNTS:
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO accounts "
                "(code, name, account_type, normal_balance, is_cash_account, is_active, description, created_at, updated_at) "
                "VALUES (:code, :name, :account_type, :normal_balance, 0, 1, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"code": code, "name": name, "account_type": account_type, "normal_balance": normal_balance},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for code, _name, _account_type, _normal_balance in NEW_ACCOUNTS:
        conn.execute(sa.text("DELETE FROM accounts WHERE code = :code"), {"code": code})
