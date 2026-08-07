"""rebuild reconciliation as itemized statement-anchored flow

Revision ID: 7a3144664a78
Revises: 6cca466cefc0
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


# revision identifiers, used by Alembic.
revision: str = '7a3144664a78'
down_revision: Union[str, None] = '6cca466cefc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Old reconciliations were a period-batch model (period_type/period_start/period_end,
    # one row per account per calendar period, no itemized clearing). This is early-stage
    # dev data with no continuity guarantees worth preserving against the new schema, so we
    # drop and recreate rather than backfill.
    op.drop_table('reconciliations')

    op.create_table(
        'reconciliations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('statement_date', sa.Date(), nullable=False),
        sa.Column('statement_ending_balance', app.models.base.SQLiteDecimal(), nullable=False),
        sa.Column('starting_balance', app.models.base.SQLiteDecimal(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'undone')", name='ck_reconciliations_status'
        ),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['completed_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_id', 'statement_date', name='uq_reconciliations_account_statement_date'
        ),
    )
    op.create_index(
        op.f('ix_reconciliations_account_id'), 'reconciliations', ['account_id'], unique=False
    )
    # Partial unique index: at most one in-progress reconciliation per account at a time.
    op.create_index(
        'uq_reconciliations_account_in_progress',
        'reconciliations',
        ['account_id'],
        unique=True,
        sqlite_where=sa.text("status = 'in_progress'"),
    )

    # SQLite can't ALTER a table to add a constraint directly; batch mode recreates the
    # table under the hood (same pattern as 6cca466cefc0 / 7ae2d8df3607).
    with op.batch_alter_table('journal_lines') as batch_op:
        batch_op.add_column(
            sa.Column(
                'cleared_status', sa.String(length=20), nullable=False, server_default='uncleared'
            )
        )
        batch_op.add_column(sa.Column('reconciliation_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f('ix_journal_lines_reconciliation_id'), ['reconciliation_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_journal_lines_reconciliation_id_reconciliations',
            'reconciliations',
            ['reconciliation_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_check_constraint(
            'ck_journal_lines_cleared_status', "cleared_status IN ('uncleared', 'cleared')"
        )
        batch_op.alter_column('cleared_status', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('journal_lines') as batch_op:
        batch_op.drop_constraint('ck_journal_lines_cleared_status', type_='check')
        batch_op.drop_constraint(
            'fk_journal_lines_reconciliation_id_reconciliations', type_='foreignkey'
        )
        batch_op.drop_index(op.f('ix_journal_lines_reconciliation_id'))
        batch_op.drop_column('reconciliation_id')
        batch_op.drop_column('cleared_status')

    op.drop_index('uq_reconciliations_account_in_progress', table_name='reconciliations')
    op.drop_index(op.f('ix_reconciliations_account_id'), table_name='reconciliations')
    op.drop_table('reconciliations')

    op.create_table(
        'reconciliations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period_type', sa.String(length=20), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('statement_balance', app.models.base.SQLiteDecimal(), nullable=False),
        sa.Column('book_balance', app.models.base.SQLiteDecimal(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('notes', sa.String(length=1000), nullable=True),
        sa.Column('submitted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reconciled_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reconciled_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "period_type IN ('monthly', 'quarterly', 'annual')",
            name='ck_reconciliations_period_type',
        ),
        sa.CheckConstraint("status IN ('open', 'completed')", name='ck_reconciliations_status'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['reconciled_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['submitted_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'period_type', 'period_start', 'period_end', 'account_id',
            name='uq_reconciliations_period_account',
        ),
    )
    op.create_index(
        op.f('ix_reconciliations_period_start'), 'reconciliations', ['period_start'], unique=False
    )
