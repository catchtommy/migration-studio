"""Stage attempts that re-ran a completed stage in place (restart from a stage).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rerun', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_column('rerun')
