"""A run remembers when someone chose to keep what its steps produced although their agents changed since.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agents_kept_at', app.db.types.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('agents_kept_at')
