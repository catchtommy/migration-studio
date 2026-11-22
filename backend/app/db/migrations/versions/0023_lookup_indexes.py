"""Indexes for the lookups pages make on every load (and the activity stream every few seconds), so a remote database
answers them from the index instead of reading every row of the run, project or table.

- audit_events (run_id, id): a run's events after the last one sent, in order (the activity stream)
- runs (project, domain, operation): a feature's runs; the dashboard's list (ordered by domain, operation)
- runs (project, operation): the feature-listing runs of a project (operation "_features")
- project_members (user_id): the projects a user belongs to (the key starts with the project)

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

INDEXES = [
    ('ix_audit_events_run_id_id', 'audit_events', ['run_id', 'id']),
    ('ix_runs_project_domain_op', 'runs', ['project', 'domain', 'operation']),
    ('ix_runs_project_operation', 'runs', ['project', 'operation']),
    ('ix_project_members_user_id', 'project_members', ['user_id']),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
