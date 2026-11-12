import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine, event, exc
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import config as db_config
from app.db.models import AuditEvent

settings.data_dir.mkdir(parents=True, exist_ok=True)


class NotConfigured(RuntimeError):
    """No database has been chosen yet: the studio is in setup mode."""


_engine: Engine | None = None
SessionLocal = sessionmaker(expire_on_commit=False)


# A pooled connection idle for longer than this is pinged before use. Pinging every checkout (pool_pre_ping) costs a
# round trip each time, and requests check out several connections: on a remote database that adds up to seconds.
IDLE_PING_SECONDS = 30


def _ping_idle_connections(engine: Engine) -> None:
    """Replace connections the server or a firewall dropped, as pool_pre_ping would, but only check those that sat
    idle in the pool: a connection used moments ago is still there."""

    @event.listens_for(engine, "checkin")
    def _checkin(_dbapi_conn, record):
        record.info["idle_since"] = time.monotonic()

    @event.listens_for(engine, "checkout")
    def _checkout(dbapi_conn, record, _proxy):
        idle_since = record.info.pop("idle_since", None)
        if idle_since is None or time.monotonic() - idle_since < IDLE_PING_SECONDS:
            return
        try:
            alive = engine.dialect.do_ping(dbapi_conn)
        except Exception:  # noqa: BLE001 - any driver error means the connection is gone
            alive = False
        if not alive:
            raise exc.DisconnectionError()  # the pool discards it and checks out (or opens) another


def make_engine(url: URL) -> Engine:
    db_config.prepare_driver(url)
    if url.get_backend_name() != "sqlite":
        # The pool covers the runner's worker threads (up to 16) plus the API's requests; pool_recycle stays under
        # the idle timeouts firewalls commonly apply.
        engine = create_engine(url, connect_args=db_config.connect_args(url), pool_size=10, max_overflow=20,
                               pool_recycle=1800)
        _ping_idle_connections(engine)
        return engine
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
