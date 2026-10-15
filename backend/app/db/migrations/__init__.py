"""Schema migrations (Alembic), run at startup and when a new database is configured.

To change the schema: edit app/db/models.py, then add a revision under versions/ with explicit `op` calls that work on
SQLite, PostgreSQL and Oracle (e.g. `alembic revision --autogenerate` against a scratch database, then review it).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
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
            # A SQLite database from before migrations: bring it up to the baseline the old way, then track it.
            Base.metadata.create_all(conn, tables=_baseline_tables())
            _add_missing_columns(conn)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")


def _baseline_tables():
    return [t for t in Base.metadata.sorted_tables if t.name in BASELINE_TABLES]


def _add_missing_columns(conn) -> None:
    """What startup used to do: add nullable columns introduced since a table was created."""
    inspector = inspect(conn)
    for table in _baseline_tables():
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing and column.nullable:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                                  f"{column.type.compile(conn.dialect)}"))
