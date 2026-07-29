"""add invoice columns to journal entries

Revision ID: 6cca466cefc0
Revises: b5c055a45e17
Create Date: 2026-07-29 06:00:12.274014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6cca466cefc0'
down_revision: Union[str, None] = 'b5c055a45e17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER a table to add a constraint directly; batch mode recreates
    # the table under the hood (see 7ae2d8df3607 for the same pattern).
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.add_column(sa.Column("invoice_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reverses_entry_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_journal_entries_invoice_id"), ["invoice_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_journal_entries_invoice_id_invoices", "invoices", ["invoice_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_journal_entries_reverses_entry_id_journal_entries",
            "journal_entries",
            ["reverses_entry_id"],
            ["id"],
        )
        batch_op.drop_constraint("ck_journal_entries_source", type_="check")
        batch_op.create_check_constraint(
            "ck_journal_entries_source", "source IN ('manual', 'webhook', 'invoice')"
        )


def downgrade() -> None:
    with op.batch_alter_table("journal_entries") as batch_op:
        batch_op.drop_constraint("ck_journal_entries_source", type_="check")
        batch_op.create_check_constraint(
            "ck_journal_entries_source", "source IN ('manual', 'webhook')"
        )
        batch_op.drop_constraint(
            "fk_journal_entries_reverses_entry_id_journal_entries", type_="foreignkey"
        )
        batch_op.drop_constraint("fk_journal_entries_invoice_id_invoices", type_="foreignkey")
        batch_op.drop_index(op.f("ix_journal_entries_invoice_id"))
        batch_op.drop_column("reverses_entry_id")
        batch_op.drop_column("invoice_id")
