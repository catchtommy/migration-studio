"""Committing code to git: runs' repository, base branch and delivery; workflows' git settings; users' git identity
and access tokens.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repository', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('base_branch', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('started_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('delivery', app.db.types.JSONText(), nullable=True))
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.add_column(sa.Column('git', app.db.types.JSONText(), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('git_name', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('git_email', sa.String(length=255), nullable=True))
    op.create_table('git_credentials',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=False),
    sa.Column('token', sa.Text(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'host')
    )
    with op.batch_alter_table('git_credentials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_git_credentials_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_table('git_credentials')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('git_email')
        batch_op.drop_column('git_name')
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.drop_column('git')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        for column in ('delivery', 'started_by', 'base_branch', 'repository'):
            batch_op.drop_column(column)
