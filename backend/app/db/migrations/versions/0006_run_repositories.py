"""Runs working on several repositories: the run's repositories, and each stage's commits and diffs in them.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repositories', app.db.types.JSONText(), nullable=True))
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repos', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_column('repos')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('repositories')
