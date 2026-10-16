from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import config as db_config
from app.db.models import AuditEvent

settings.data_dir.mkdir(parents=True, exist_ok=True)


class NotConfigured(RuntimeError):
    """No database has been chosen yet: the studio is in setup mode."""


_engine: Engine | None = None
SessionLocal = sessionmaker(expire_on_commit=False)


def make_engine(url: URL) -> Engine:
    db_config.prepare_driver(url)
    if url.get_backend_name() != "sqlite":
        # pre_ping replaces connections the server or a firewall dropped; the pool covers the runner's worker threads
        # (up to 16) plus the API's requests.
        return create_engine(url, connect_args=db_config.connect_args(url), pool_pre_ping=True, pool_size=10,
                             max_overflow=20)
    engine = create_engine(url, connect_args=db_config.connect_args(url))

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL lets the SSE reader and the graph worker threads use the DB concurrently.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def configure(url: URL) -> None:
    """Use this database from now on."""
    global _engine
    old, _engine = _engine, make_engine(url)
    SessionLocal.configure(bind=_engine)
    if old is not None:
        old.dispose()


def is_configured() -> bool:
    return _engine is not None


def get_engine() -> Engine:
    if _engine is None:
        raise NotConfigured("Choose a database on the setup screen first")
    return _engine


def init_db() -> None:
    from app.db.migrations import upgrade

    upgrade(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
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
