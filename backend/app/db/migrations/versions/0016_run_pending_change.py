"""A change asked for to an approved step while the run was working: applied when the working step stops
(app/orchestrator/runner.py amend).

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pending_change', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('pending_change')
