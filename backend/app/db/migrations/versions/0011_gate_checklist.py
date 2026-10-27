"""Approval checklists: the items an approver ticked at a gate attempt.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('checklist', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_column('checklist')
