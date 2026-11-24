"""The order the project's domains are migrated in, set on the Domains page (1 first). Domains without one (added
since the order was last saved) come after, oldest first.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.add_column(sa.Column('migration_order', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('domains', schema=None) as batch_op:
        batch_op.drop_column('migration_order')
