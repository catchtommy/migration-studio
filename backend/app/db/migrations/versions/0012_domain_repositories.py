"""Each domain's service and orchestrator service repositories, which the workflow steps refer to by kind.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.add_column(sa.Column('service_repository', app.db.types.LongText(), nullable=True))
        batch_op.add_column(sa.Column('orchestrator_repository', app.db.types.LongText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.drop_column('orchestrator_repository')
        batch_op.drop_column('service_repository')
