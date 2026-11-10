"""A catalogue contract's temporary binding and its integration entry point, so a later feature reuses the entry
point an earlier one built for a domain not migrated yet (app/legacy_modules.py).

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('binding', app.db.types.JSONText(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.drop_column('binding')
