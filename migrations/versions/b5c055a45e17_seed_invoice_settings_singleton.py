"""seed invoice settings singleton

Revision ID: b5c055a45e17
Revises: 1e00523cd265
Create Date: 2026-07-29 06:00:11.967696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c055a45e17'
down_revision: Union[str, None] = '1e00523cd265'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # INSERT OR IGNORE on the id=1 primary key makes re-running this migration a
    # no-op if the singleton row already exists. Account mappings are looked up
    # by code (not hardcoded id) since ids depend on insertion order.
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO invoice_settings "
            "(id, invoice_prefix, default_payment_terms_days, default_tax_rate, "
            " ar_account_id, service_revenue_account_id, tax_payable_account_id, "
            " unearned_revenue_account_id, created_at, updated_at) "
            "VALUES (1, 'INV', 30, '0', "
            " (SELECT id FROM accounts WHERE code = '1100'), "
            " (SELECT id FROM accounts WHERE code = '4000'), "
            " (SELECT id FROM accounts WHERE code = '2020'), "
            " (SELECT id FROM accounts WHERE code = '2030'), "
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM invoice_settings WHERE id = 1"))
