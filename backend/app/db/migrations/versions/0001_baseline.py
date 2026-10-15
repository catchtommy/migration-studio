"""Baseline: the schema when database choice (SQLite, PostgreSQL, Oracle) was introduced.

Revision ID: 0001
Revises: (none)
"""

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('agents',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('effort', sa.String(length=16), nullable=False),
    sa.Column('jdk', sa.String(length=16), nullable=False),
    sa.Column('instructions', app.db.types.NonNullText(), nullable=True),
    sa.Column('tools', app.db.types.JSONText(), nullable=False),
    sa.Column('outputs', app.db.types.JSONText(), nullable=False),
    sa.Column('max_turns', sa.Integer(), nullable=False),
    sa.Column('checks', app.db.types.JSONText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('contracts',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('spec', sa.Text(), nullable=True),
    sa.Column('spec_path', sa.Text(), nullable=True),
    sa.Column('consumers', app.db.types.JSONText(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contracts_provider'), ['provider'], unique=False)

    op.create_table('domains',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('context', app.db.types.NonNullText(), nullable=True),
    sa.Column('agent_inputs', app.db.types.JSONText(), nullable=False),
    sa.Column('default_workflow', sa.String(length=64), nullable=True),
    sa.Column('owned_tables', app.db.types.JSONText(), nullable=True),
    sa.Column('owned_packages', app.db.types.JSONText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('features',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('domain', sa.String(length=64), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('entry_points', app.db.types.JSONText(), nullable=False),
    sa.Column('tables', app.db.types.JSONText(), nullable=False),
    sa.Column('calls', app.db.types.JSONText(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('missing_from_latest', sa.Boolean(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('domain', 'key')
    )
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_features_domain'), ['domain'], unique=False)

    op.create_table('graph_checkpoints',
    sa.Column('thread_id', sa.String(length=64), nullable=False),
    sa.Column('checkpoint_ns', sa.String(length=255), nullable=False),
    sa.Column('checkpoint_id', sa.String(length=64), nullable=False),
    sa.Column('parent_checkpoint_id', sa.String(length=64), nullable=True),
    sa.Column('type', sa.String(length=32), nullable=True),
    sa.Column('checkpoint', sa.LargeBinary(), nullable=True),
    sa.Column('meta', sa.LargeBinary(), nullable=True),
    sa.PrimaryKeyConstraint('thread_id', 'checkpoint_ns', 'checkpoint_id')
    )
    op.create_table('graph_writes',
    sa.Column('thread_id', sa.String(length=64), nullable=False),
    sa.Column('checkpoint_ns', sa.String(length=255), nullable=False),
    sa.Column('checkpoint_id', sa.String(length=64), nullable=False),
    sa.Column('task_id', sa.String(length=64), nullable=False),
    sa.Column('idx', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('channel', sa.String(length=255), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=True),
    sa.Column('value', sa.LargeBinary(), nullable=True),
    sa.PrimaryKeyConstraint('thread_id', 'checkpoint_ns', 'checkpoint_id', 'task_id', 'idx')
    )
    op.create_table('runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('domain', sa.String(length=64), nullable=False),
    sa.Column('operation', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('current_stage', sa.String(length=64), nullable=True),
    sa.Column('workspace', sa.Text(), nullable=True),
    sa.Column('branch', sa.String(length=255), nullable=True),
    sa.Column('base_sha', sa.String(length=64), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('inputs', app.db.types.JSONText(), nullable=True),
    sa.Column('workflow', sa.String(length=64), nullable=True),
    sa.Column('workflow_stages', app.db.types.JSONText(), nullable=True),
    sa.Column('scope', sa.String(length=16), nullable=True),
    sa.Column('pending_rerun', app.db.types.JSONText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_runs_domain'), ['domain'], unique=False)

    op.create_table('settings',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('value', app.db.types.JSONText(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('workflows',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('steps', app.db.types.JSONText(), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('approvals',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('gate', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('target_stage', sa.String(length=64), nullable=True),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('reviewer', sa.String(length=128), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approvals_run_id'), ['run_id'], unique=False)

    op.create_table('artifacts',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('path', sa.String(length=1024), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('content', app.db.types.NonNullText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_artifacts_run_id'), ['run_id'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('message', app.db.types.NonNullText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_run_id'), ['run_id'], unique=False)

    op.create_table('dependencies',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('interaction', sa.String(length=128), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('target', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('pattern', sa.String(length=32), nullable=False),
    sa.Column('contract', sa.String(length=128), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('summary', app.db.types.NonNullText(), nullable=True),
    sa.Column('code_refs', app.db.types.JSONText(), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('dependencies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dependencies_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_dependencies_source'), ['source'], unique=False)
        batch_op.create_index(batch_op.f('ix_dependencies_target'), ['target'], unique=False)

    op.create_table('questions',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('question', app.db.types.NonNullText(), nullable=True),
    sa.Column('options', app.db.types.JSONText(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('answered_by', sa.String(length=128), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('answered_at', app.db.types.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_questions_run_id'), ['run_id'], unique=False)

    op.create_table('stage_executions',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('start_sha', sa.String(length=64), nullable=True),
    sa.Column('end_sha', sa.String(length=64), nullable=True),
    sa.Column('feedback', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('diff', sa.Text(), nullable=True),
    sa.Column('started_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('finished_at', app.db.types.UTCDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stage_executions_run_id'), ['run_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('stage_executions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stage_executions_run_id'))

    op.drop_table('stage_executions')
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_questions_run_id'))

    op.drop_table('questions')
    with op.batch_alter_table('dependencies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dependencies_target'))
        batch_op.drop_index(batch_op.f('ix_dependencies_source'))
        batch_op.drop_index(batch_op.f('ix_dependencies_run_id'))

    op.drop_table('dependencies')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_events_run_id'))

    op.drop_table('audit_events')
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_artifacts_run_id'))

    op.drop_table('artifacts')
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approvals_run_id'))

    op.drop_table('approvals')
    op.drop_table('workflows')
    op.drop_table('settings')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runs_domain'))

    op.drop_table('runs')
    op.drop_table('graph_writes')
    op.drop_table('graph_checkpoints')
    with op.batch_alter_table('features', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_features_domain'))

    op.drop_table('features')
    op.drop_table('domains')
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contracts_provider'))

    op.drop_table('contracts')
    op.drop_table('agents')
