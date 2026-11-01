"""Each step attempt keeps the AI tokens it used: per model call totals, and the output tokens spent writing each
document (app/agents/usage.py).

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('usage', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_column('usage')
