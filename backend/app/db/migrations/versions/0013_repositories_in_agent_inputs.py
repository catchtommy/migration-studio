"""Repositories moved to each domain's Agent inputs (per agent, with its base branch): the two fixed columns go.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.drop_column('orchestrator_repository')
        batch_op.drop_column('service_repository')


def downgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_repository', app.db.types.LongText(), nullable=True))
        batch_op.add_column(sa.Column('orchestrator_repository', app.db.types.LongText(), nullable=True))
