"""Each agent's own guardrails in plain words, edited on the Agents page and given to it as hard rules.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('guardrails', app.db.types.NonNullText(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'guardrails')
