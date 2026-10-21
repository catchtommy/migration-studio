"""Schema migrations (Alembic), run at startup and when a new database is configured.

To change the schema: edit app/db/models.py, then add a revision under versions/ with explicit `op` calls that work on
SQLite, PostgreSQL and Oracle (e.g. `alembic revision --autogenerate` against a scratch database, then review it).
"""

import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from app.db.models import Base

BASELINE = "0001"
# The tables of the baseline revision. Databases from before migrations are brought up to exactly these.
BASELINE_TABLES = {
    "agents", "approvals", "artifacts", "audit_events", "contracts", "dependencies", "domains", "features",
    "graph_checkpoints", "graph_writes", "questions", "runs", "settings", "stage_executions", "workflows",
}


def _config(conn) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent))
    cfg.attributes["connection"] = conn
    return cfg


def upgrade(engine: Engine) -> None:
    with engine.begin() as conn:
        cfg = _config(conn)
        tables = set(inspect(conn).get_table_names())
        if MigrationContext.configure(conn).get_current_revision() is None and "runs" in tables:
            # A SQLite database from before migrations: the baseline's tables it doesn't have yet (older releases
            # had fewer), its columns brought up to the baseline the old way, then tracked.
            _create_missing_baseline_tables(conn, tables)
            _add_missing_columns(conn)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def _create_missing_baseline_tables(conn, existing: set[str]) -> None:
    """Run the baseline revision's table definitions for the tables the database lacks, as the baseline has them (not
    as the models have them now: later revisions add the rest)."""
    path = Path(__file__).parent / "versions" / "0001_baseline.py"
    spec = importlib.util.spec_from_file_location("studio_baseline_revision", path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    ops = Operations(MigrationContext.configure(conn))

    class MissingIndexes:  # a table the database has: only the baseline's indexes it lacks are created
        def __init__(self, table):
            self.table = table
            self.have = {i["name"] for i in inspect(conn).get_indexes(table)}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def f(self, name):
            return name

        def create_index(self, name, columns, unique=False, **kw):
            if name not in self.have:
                ops.create_index(name, self.table, columns, unique=unique)

    class OnlyMissing:
        def create_table(self, name, *columns, **kw):
            if name not in existing:
                ops.create_table(name, *columns, **kw)

        def batch_alter_table(self, name, **kw):
            return MissingIndexes(name) if name in existing else ops.batch_alter_table(name, **kw)

        def __getattr__(self, attr):  # anything else the baseline does, as Alembic does it
            return getattr(ops, attr)

    baseline.op = OnlyMissing()
    baseline.upgrade()


def _baseline_tables():
    return [t for t in Base.metadata.sorted_tables if t.name in BASELINE_TABLES]


# Columns later revisions add; bringing an old database up to the baseline must leave them to those revisions.
LATER_COLUMNS = {("stage_executions", "rerun"), ("runs", "repository"), ("runs", "base_branch"),
                 ("runs", "started_by"), ("runs", "delivery"), ("workflows", "git"),
                 ("runs", "repositories"), ("stage_executions", "repos"),
                 ("runs", "legacy"), ("agents", "guardrails")}


def _add_missing_columns(conn) -> None:
    """What startup used to do: add nullable columns introduced since a table was created."""
    inspector = inspect(conn)
    for table in _baseline_tables():
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            later = column.name == "project" or (table.name, column.name) in LATER_COLUMNS
            if column.name not in existing and column.nullable and not later:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                                  f"{column.type.compile(conn.dialect)}"))
