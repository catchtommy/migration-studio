"""The other domains a domain may call directly through their API, without an orchestrator.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.add_column(sa.Column('direct_calls', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.drop_column('direct_calls')
