from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import AuditEvent, Base

settings.data_dir.mkdir(parents=True, exist_ok=True)

_is_sqlite = settings.db_url.startswith("sqlite")
engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL lets the SSE reader and the graph worker threads use the DB concurrently.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all only creates missing tables; add nullable columns introduced since a table was created."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing and column.nullable:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                                      f"{column.type.compile(engine.dialect)}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


def emit(run_id: str, kind: str, message: str, stage: str | None = None) -> None:
    with session_scope() as s:
        s.add(AuditEvent(run_id=run_id, stage=stage, kind=kind, message=message))
