"""What each step attempt was given: the earlier documents it used, with the attempt that produced each, and the full
prompt its agent got (shown to users with runs.inspect).

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inputs', app.db.types.JSONText(), nullable=True))
        batch_op.add_column(sa.Column('prompt', app.db.types.LongText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_column('prompt')
        batch_op.drop_column('inputs')
