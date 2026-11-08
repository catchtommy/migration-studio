"""Which domains a feature was moved out of, so a later feature listing of those domains doesn't bring it back.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.add_column(sa.Column('moved_from', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.drop_column('moved_from')
