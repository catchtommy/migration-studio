"""The database paths that only remote databases take (the suite otherwise runs on SQLite): COMMIT and ROLLBACK are sent
only when a transaction is open, writes are still committed, and a batch of log lines with a bad row still writes the
good ones.

The connection here is SQLite reporting `transaction_in_progress` as python-oracledb does: a SELECT opens no
transaction, a write does (Oracle behaves the same way)."""

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.db import session as db_session
from app.db.models import AuditEvent


class OracleLikeConnection:
    """A DB-API connection that says whether a transaction is open, and counts what it is sent."""

    def __init__(self, path: str, sent: dict):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._sent = sent

    @property
    def transaction_in_progress(self) -> bool:
        return self._conn.in_transaction

    def commit(self):
        self._sent["commit"] += 1
        self._conn.commit()

    def rollback(self):
        self._sent["rollback"] += 1
        self._conn.rollback()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def remote_like(tmp_path):
    sent = {"commit": 0, "rollback": 0}
    path = str(tmp_path / "remote.db")
    engine = create_engine("sqlite://", creator=lambda: OracleLikeConnection(path, sent), poolclass=QueuePool,
                           pool_size=2, max_overflow=0, pool_reset_on_return=None)  # as make_engine has it
    db_session._skip_idle_transaction_calls(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE t (v INTEGER)"))
    sent.update(commit=0, rollback=0)
    yield engine, sessionmaker(bind=engine, expire_on_commit=False), sent, path
    engine.dispose()


def test_a_read_only_session_sends_no_commit_and_no_rollback(remote_like):
    _engine, Session, sent, _path = remote_like
    with Session() as s:  # as session_scope: commit at the end, then the connection goes back to the pool
        assert s.execute(text("SELECT count(*) FROM t")).scalar() == 0
        s.commit()
    assert sent == {"commit": 0, "rollback": 0}


def test_a_write_is_committed_and_another_connection_sees_it(remote_like):
    _engine, Session, sent, path = remote_like
    with Session() as s:
        s.execute(text("INSERT INTO t (v) VALUES (1)"))
        s.commit()
    assert sent["commit"] == 1 and sent["rollback"] == 0
    with sqlite3.connect(path) as other:
        assert other.execute("SELECT v FROM t").fetchall() == [(1,)]


def test_a_failed_write_is_rolled_back(remote_like):
    _engine, Session, sent, path = remote_like
    with Session() as s:
        s.execute(text("INSERT INTO t (v) VALUES (2)"))
        s.rollback()
    assert sent["rollback"] == 1 and sent["commit"] == 0
    with sqlite3.connect(path) as other:
        assert other.execute("SELECT count(*) FROM t").fetchone() == (0,)


def test_a_connection_returned_with_an_open_transaction_is_rolled_back_by_the_pool(remote_like):
    engine, _Session, sent, path = remote_like
    raw = engine.raw_connection()  # left open by mistake: the pool must not hand it on with the write pending
    raw.cursor().execute("INSERT INTO t (v) VALUES (3)")
    raw.close()
    assert sent["rollback"] == 1
    with sqlite3.connect(path) as other:
        assert other.execute("SELECT count(*) FROM t").fetchone() == (0,)
    with engine.connect() as c:  # and the connection works on
        assert c.execute(select(text("1"))).scalar() == 1


def test_a_batch_of_log_lines_with_a_bad_line_still_writes_the_good_ones(client, monkeypatch):
    from tests.conftest import start_run, wait_for

    run_id = start_run(client, {"domain": "customer", "operation": "badbatch"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    good = [{"run_id": run_id, "stage": None, "kind": "log", "message": f"bad-batch good {i}",
             "created_at": db_session.utcnow()} for i in range(3)]
    bad = {**good[0], "kind": None, "message": "bad-batch bad"}  # kind is required: this row can't be written
    with db_session._log_queue_lock:
        db_session._log_queue.extend([good[0], bad, good[1], good[2]])
    db_session.flush_logs()
    with db_session.session_scope() as s:
        written = list(s.scalars(select(AuditEvent.message).where(AuditEvent.run_id == run_id,
                                                                  AuditEvent.message.like("bad-batch%"))
                                 .order_by(AuditEvent.id)))
    assert written == ["bad-batch good 0", "bad-batch good 1", "bad-batch good 2"]


# --- connections to a remote database: settings, pinging idle ones, opening them ahead ----------------------------

def test_a_remote_engine_gets_the_pool_settings_and_oracle_tuning(monkeypatch):
    import oracledb
    from sqlalchemy import make_url

    monkeypatch.setattr(oracledb.defaults, "prefetchrows", oracledb.defaults.prefetchrows)  # put back afterwards
    monkeypatch.setattr(oracledb.defaults, "stmtcachesize", oracledb.defaults.stmtcachesize)
    engine = db_session.make_engine(make_url("oracle+oracledb://u:p@db.example.com:1521/?service_name=s"))
    try:
        assert engine.pool.size() == 10 and engine.pool._max_overflow == 20 and engine.pool._recycle == 3600
        assert engine.pool._reset_on_return.name == "reset_none"  # the reset event decides: only an open transaction
        assert oracledb.defaults.prefetchrows == 100 and oracledb.defaults.stmtcachesize == 100
    finally:
        engine.dispose()


def test_a_connection_that_sat_idle_is_pinged_and_replaced_when_dead(tmp_path, monkeypatch):
    opened = []
    path = str(tmp_path / "ping.db")

    def connect():
        conn = OracleLikeConnection(path, {"commit": 0, "rollback": 0})
        opened.append(conn)
        return conn

    engine = create_engine("sqlite://", creator=connect, poolclass=QueuePool, pool_size=1, max_overflow=0,
                           pool_reset_on_return=None)
    db_session._ping_idle_connections(engine)
    alive = {"answer": True}
    monkeypatch.setattr(engine.dialect, "do_ping", lambda _conn: alive["answer"])
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    with engine.connect() as c:  # used moments ago: no ping, same connection
        c.execute(text("SELECT 1"))
    assert len(opened) == 1
    monkeypatch.setattr(db_session, "IDLE_PING_SECONDS", 0)  # everything counts as idle now
    alive["answer"] = False  # and the network dropped it
    with engine.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1  # a new connection, transparently
    assert len(opened) == 2
    engine.dispose()


def test_connections_are_opened_ahead_in_the_background(tmp_path, monkeypatch):
    import time

    opened = []
    path = str(tmp_path / "warm.db")
    engine = create_engine("sqlite://", creator=lambda: opened.append(1) or OracleLikeConnection(
        path, {"commit": 0, "rollback": 0}), poolclass=QueuePool, pool_size=4, max_overflow=0)
    # warm_pool leaves SQLite alone (a local file has nothing to gain): present this engine as a remote one
    monkeypatch.setattr(db_session, "_engine", type("E", (), {"url": SimpleNamespace(get_backend_name=lambda: "oracle"),
                                                             "connect": engine.connect})())
    db_session.warm_pool(3)
    deadline = time.time() + 5
    while (len(opened) < 3 or engine.pool.checkedin() < 3) and time.time() < deadline:
        time.sleep(0.05)
    assert len(opened) == 3 and engine.pool.checkedin() == 3  # opened, then given back to the pool ready for use
    engine.dispose()
