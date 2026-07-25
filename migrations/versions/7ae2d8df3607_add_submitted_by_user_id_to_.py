"""add submitted_by_user_id to reconciliations

Revision ID: 7ae2d8df3607
Revises: cc370c74bf83
Create Date: 2026-07-25 07:38:43.388927

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ae2d8df3607'
down_revision: Union[str, None] = 'cc370c74bf83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ALTER a table to add a constraint directly; batch mode recreates
    # the table under the hood (Alembic's documented approach for SQLite).
    with op.batch_alter_table('reconciliations') as batch_op:
        batch_op.add_column(sa.Column('submitted_by_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_reconciliations_submitted_by_user_id_users',
            'users',
            ['submitted_by_user_id'],
            ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('reconciliations') as batch_op:
        batch_op.drop_constraint('fk_reconciliations_submitted_by_user_id_users', type_='foreignkey')
        batch_op.drop_column('submitted_by_user_id')
