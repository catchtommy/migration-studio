"""A feature's entry points outside its domain: other domains' flows and external systems that use it, recorded by
the feature listing (used_by) as a start for the Analysis step.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.add_column(sa.Column('used_by', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.drop_column('used_by')
