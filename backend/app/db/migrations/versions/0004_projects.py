"""Projects: a project column on project data, per-project settings, project members.

Existing data becomes the "default" project (created at startup if missing). Agents and workflows become the shared
library; settings split into global ones and the default project's.

Revision ID: 0004
Revises: 0003
"""

import warnings

import sqlalchemy as sa
from alembic import op

import app.db.types

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

DEFAULT = "default"
SHARED = "_shared"
GLOBAL = "_global"
# Settings that belong to a project (the rest stay global), including the latest approved bounded contexts.
PROJECT_SETTINGS = ("monolith_repo", "max_attempts", "jdk_legacy_home", "jdk_modern_home", "oracle_test_url",
                    "event_bus_namespace", "event_topic_pattern", "command_queue_pattern", "service_dir_pattern",
                    "discovery_contexts")


def _add_project(table: str, value: str | None = None, where: str = "") -> None:
    op.add_column(table, sa.Column('project', sa.String(length=64), nullable=True))
    if value is not None:
        op.execute(sa.text(f"UPDATE {table} SET project = :v{(' WHERE ' + where) if where else ''}").bindparams(v=value))


def _set_pk(table: str, columns: list[str]) -> None:
    """Make `project` NOT NULL and the primary key (project, key)."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":  # SQLite can't alter constraints: the table is recreated
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", sa.exc.SAWarning)
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column('project', existing_type=sa.String(length=64), nullable=False)
                batch.create_primary_key(f"pk_{table}", columns)
        return
    old = sa.inspect(bind).get_pk_constraint(table)["name"]
    op.alter_column(table, 'project', existing_type=sa.String(length=64), nullable=False)
    op.drop_constraint(old, table, type_="primary")
    op.create_primary_key(f"pk_{table}", table, columns)


def _not_null(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.alter_column('project', existing_type=sa.String(length=64), nullable=False)
        batch.create_index(batch.f(f'ix_{table}_project'), ['project'], unique=False)


def upgrade() -> None:
    op.create_table('projects',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', app.db.types.NonNullText(), nullable=True),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.Column('updated_at', app.db.types.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('project_members',
    sa.Column('project', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=64), nullable=False),
    sa.Column('created_at', app.db.types.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('project', 'user_id')
    )

    keys = ", ".join(f"'{k}'" for k in PROJECT_SETTINGS)
    _add_project('settings', GLOBAL)
    op.execute(sa.text(f"UPDATE settings SET project = :p WHERE key IN ({keys})").bindparams(p=DEFAULT))
    _set_pk('settings', ['project', 'key'])
    for table in ('agents', 'workflows'):
        _add_project(table, SHARED)
        _set_pk(table, ['project', 'key'])
    for table in ('domains', 'contracts'):
        _add_project(table, DEFAULT)
        _set_pk(table, ['project', 'key'])
    for table in ('runs', 'dependencies'):
        _add_project(table, DEFAULT)
        _not_null(table)

    # features: unique (domain, key) becomes unique (project, domain, key)
    _add_project('features', DEFAULT)
    bind = op.get_bind()
    old = next((u["name"] for u in sa.inspect(bind).get_unique_constraints('features')
                if u["column_names"] == ["domain", "key"]), None)
    convention = {"uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", sa.exc.SAWarning)
        with op.batch_alter_table('features', naming_convention=convention,
                                  recreate="always" if bind.dialect.name == "sqlite" else "auto") as batch:
            batch.alter_column('project', existing_type=sa.String(length=64), nullable=False)
            batch.drop_constraint(old or "uq_features_domain_key", type_="unique")
            batch.create_unique_constraint('uq_features_project_domain_key', ['project', 'domain', 'key'])
            batch.create_index(batch.f('ix_features_project'), ['project'], unique=False)


def downgrade() -> None:
    raise NotImplementedError("Projects can't be removed automatically; restore a backup taken before upgrading")
