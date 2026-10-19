"""Texts agents share (the preamble and blocks their instructions include), stored and edited instead of fixed in code.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'shared_texts',
        sa.Column('project', sa.String(length=64), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=False),
        sa.Column('description', app.db.types.NonNullText(), nullable=True),
        sa.Column('text', app.db.types.NonNullText(), nullable=True),
        sa.Column('updated_by', sa.String(length=128), nullable=True),
        sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
        sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('project', 'key'),
    )


def downgrade() -> None:
    op.drop_table('shared_texts')
