import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine, event, exc
from sqlalchemy.orm import Session, sessionmaker

from app import cache  # registers the session events that keep cached reads up to date
from app.config import settings
from app.db import config as db_config
from app.db.models import AuditEvent, utcnow

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


def _skip_idle_transaction_calls(engine: Engine) -> None:
    """On a remote database every COMMIT and ROLLBACK is a network round trip, and most sessions only read. Two are
    saved per session_scope(): its COMMIT is sent only when a transaction is open, and the pool rolls a connection back
    on return only when one is still open (a read-only session leaves none). Drivers that can't tell (no
    `transaction_in_progress`) are always committed and rolled back, as before."""

    def in_progress(dbapi_conn) -> bool:
        try:
            return bool(dbapi_conn.transaction_in_progress)
        except AttributeError:
            return True

    commit = engine.dialect.do_commit

    def do_commit(dbapi_conn) -> None:
        if in_progress(dbapi_conn):
            commit(dbapi_conn)

    engine.dialect.do_commit = do_commit  # this engine's dialect instance only

    @event.listens_for(engine, "reset")
    def _reset(dbapi_conn, _record, reset_state):  # the engine is made with pool_reset_on_return=None
        if reset_state.terminate_only:
            return
        if in_progress(dbapi_conn):
            dbapi_conn.rollback()


def _time_statements(engine: Engine) -> None:
    """Count each statement (and its time) into the API request that ran it (app/request_timing.py)."""
    from app import request_timing

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, _cursor, _statement, _parameters, _context, _many):
        conn.info["statement_started"] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, _cursor, _statement, _parameters, _context, _many):
        started = conn.info.pop("statement_started", None)
        if started is not None:
            request_timing.record(time.perf_counter() - started)


def make_engine(url: URL) -> Engine:
    engine = _make_engine(url)
    _time_statements(engine)
    return engine


def _make_engine(url: URL) -> Engine:
    db_config.prepare_driver(url)
    if url.get_backend_name() != "sqlite":
        if url.get_backend_name() == "oracle":
            import oracledb
            # A query's first rows come back with the execute itself instead of a separate fetch round trip (the
            # driver's default prefetches 2 rows, so any list cost one more round trip).
            oracledb.defaults.prefetchrows = 100
            # The studio runs a few hundred distinct statements: cached per connection, Oracle doesn't parse them
            # again each time (the driver's default keeps 20)
            oracledb.defaults.stmtcachesize = 100
        # The pool covers the runner's worker threads (up to 16) plus the API's requests. Connections that sat idle
        # are pinged before use (_ping_idle_connections), so recycling is only a backstop: each new connection to a
        # remote Oracle costs seconds (TLS and authentication).
        engine = create_engine(url, connect_args=db_config.connect_args(url), pool_size=10, max_overflow=20,
                               pool_recycle=3600, pool_reset_on_return=None)
        _ping_idle_connections(engine)
        _skip_idle_transaction_calls(engine)
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


def warm_pool(connections: int = 6) -> None:
    """Open `connections` pooled connections at once, in the background. A new connection to a remote Oracle costs
    seconds (TLS and authentication): opened here, the first requests find them ready instead of each opening one."""
    engine = _engine
    if engine is None or engine.url.get_backend_name() == "sqlite":
        return
    held = threading.Barrier(connections + 1)

    def hold() -> None:
        try:
            with engine.connect():
                held.wait(timeout=60)  # keep it checked out until all are open, so each opens its own
        except Exception:  # noqa: BLE001 - warming is best effort; requests open connections as before
            held.abort()

    for i in range(connections):
        threading.Thread(target=hold, name=f"db-warm-{i}", daemon=True).start()

    def release() -> None:
        try:
            held.wait(timeout=60)
        except threading.BrokenBarrierError:
            pass

    threading.Thread(target=release, name="db-warm", daemon=True).start()


def configure(url: URL) -> None:
    """Use this database from now on."""
    global _engine
    if _engine is not None:
        try:
            flush_logs()  # queued log lines belong to the database they were logged in
        except Exception:  # noqa: BLE001 - the old database may be the reason for the switch
            pass
    old, _engine = _engine, make_engine(url)
    SessionLocal.configure(bind=_engine)
    cache.clear()  # what was read from the previous database
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


_emitted: dict[str, int] = {}  # run id -> how many events this process has recorded for it (see emitted())
_emitted_lock = threading.Lock()


def emitted(run_id: str) -> int:
    """A counter that changes whenever this process records an event of the run: the activity stream reads the
    database only when it moved (runs and the API share one process), instead of polling it continuously."""
    return _emitted.get(run_id, 0)


def _signal(run_ids) -> None:
    with _emitted_lock:
        for run_id in run_ids:
            _emitted[run_id] = _emitted.get(run_id, 0) + 1


# --- agents' log lines, written in batches ------------------------------------------------------------------------
# An agent logs hundreds of lines per step. Written one by one, each line is an INSERT and a COMMIT the agent waits
# for: two round trips, about 0.4 s on a remote database. So log lines are queued and written together, at most every
# LOG_FLUSH_SECONDS, by a background thread. Every other event (status, question, error, ...) first writes the queued
# lines, then itself, so the log keeps its order; readers that need everything (deleting a run) call flush_logs().
# Not on SQLite (a local file, nothing to save): there every line is written at once, as before.

LOG_FLUSH_SECONDS = 1.0
batch_logs: bool | None = None  # None: batch unless the database is SQLite (tests set it to check either way)
_log_queue: list[dict] = []
_log_queue_lock = threading.Lock()
_flush_lock = threading.Lock()  # one writer at a time, so batches keep their order
_log_writer: threading.Thread | None = None


def _batching() -> bool:
    if batch_logs is not None:
        return batch_logs
    return _engine is not None and _engine.dialect.name != "sqlite"


def _write_logs() -> None:
    while True:
        time.sleep(LOG_FLUSH_SECONDS)
        try:
            flush_logs()
        except Exception:  # noqa: BLE001 - keep writing later batches; flush_logs logs what it couldn't write
            pass


def flush_logs() -> None:
    """Write the queued log lines now."""
    with _flush_lock:
        with _log_queue_lock:
            batch = list(_log_queue)
            _log_queue.clear()
        if not batch:
            return
        table = AuditEvent.__table__
        try:
            with session_scope() as s:
                s.execute(table.insert(), batch)  # one round trip for the whole batch
        except Exception:  # noqa: BLE001 - e.g. a run deleted meanwhile: write the others one by one
            import logging
            for row in batch:
                try:
                    with session_scope() as s:
                        s.execute(table.insert(), [row])
                except Exception as e:  # noqa: BLE001 - its run is gone: the line has nowhere to go
                    logging.getLogger(__name__).warning("Dropped a log line of run %s: %s", row["run_id"], e)
        _signal({row["run_id"] for row in batch})


def emit(run_id: str, kind: str, message: str, stage: str | None = None) -> None:
    global _log_writer
    if kind == "log" and _batching():
        with _log_queue_lock:
            _log_queue.append({"run_id": run_id, "stage": stage, "kind": kind, "message": message,
                               "created_at": utcnow()})  # when it happened, not when the batch is written
            if _log_writer is None or not _log_writer.is_alive():
                _log_writer = threading.Thread(target=_write_logs, name="log-writer", daemon=True)
                _log_writer.start()
        return
    flush_logs()  # the lines logged before this event come before it
    with session_scope() as s:
        s.add(AuditEvent(run_id=run_id, stage=stage, kind=kind, message=message))
    _signal([run_id])
