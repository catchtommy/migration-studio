"""Schema migrations: a new database, and databases from before migrations, end up with the schema the models
describe."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db.migrations import _config, upgrade
from app.db.models import Base

HEAD = "0013"


def _state(engine):
    with engine.connect() as c:
        ctx = MigrationContext.configure(c, opts={"compare_type": False})
        diff = [d for d in compare_metadata(ctx, Base.metadata)
                if not (isinstance(d, list) and d and d[0][0] == "modify_nullable")]
        return ctx.get_current_revision(), set(inspect(c).get_table_names()), diff


def test_a_new_database_gets_the_models_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/new.db")
    upgrade(engine)
    revision, tables, diff = _state(engine)
    assert revision == HEAD and set(Base.metadata.tables) <= tables and diff == []


def test_a_database_from_before_migrations_missing_later_tables_is_brought_up(tmp_path):
    """An early release created its tables itself, before contracts, dependencies and features existed, and without
    the graph tables or Alembic's version table: it is adopted, the missing tables are created, and it is upgraded."""
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as c:
        command.upgrade(_config(c), "0001")  # the schema as it was then
    with engine.begin() as c:  # what such a database looks like
        for table in ("contracts", "dependencies", "features", "graph_checkpoints", "graph_writes", "alembic_version"):
            c.execute(text(f"DROP TABLE {table}"))
        c.execute(text("INSERT INTO runs (id, domain, operation, status, created_at, updated_at) "
                       "VALUES ('r1', 'loan', 'approve', 'completed', '2025-01-01', '2025-01-01')"))
    upgrade(engine)
    revision, tables, diff = _state(engine)
    assert revision == HEAD and {"contracts", "dependencies", "features", "graph_checkpoints"} <= tables
    with engine.connect() as c:
        assert c.execute(text("SELECT operation FROM runs WHERE id = 'r1'")).scalar() == "approve"  # data kept
    upgrade(engine)  # starting again changes nothing
    assert _state(engine)[0] == HEAD
