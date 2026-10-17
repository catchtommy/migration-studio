"""Runs that only read the monolith use the project's shared read-only copy: which one a run reads.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('legacy', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('legacy')
