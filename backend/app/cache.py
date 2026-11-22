"""An in-process cache for what nearly every request reads but rarely changes: who a session belongs to and what they may
do, settings, agents, workflows. On a remote database each of those reads is several network round trips.

Entries are invalidated by the writes themselves, wherever they come from: every ORM session records the tables it
inserted into, updated or deleted from, and when it commits, those tables' generation numbers go up. An entry remembers
the generations of the tables it was read from (taken before reading, so a write committed while it was being read
invalidates it too) and is used only while they are unchanged. Writes that bypass the ORM session (a person editing
the database by hand) are picked up after TTL_SECONDS at most.

The studio runs as one process (the runner's threads and the API share it), which is what makes this sufficient.
"""

import copy
import threading
import time
from collections.abc import Callable, Hashable, Iterable
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy.orm import Session

TTL_SECONDS = 60
MAX_ENTRIES = 5000
ALL = "*"  # a write whose table can't be told: every entry is invalidated

T = TypeVar("T")

_lock = threading.Lock()
_generations: dict[str, int] = {}
_entries: dict[tuple, tuple[float, tuple[int, ...], Any]] = {}


def _current(tables: tuple[str, ...]) -> tuple[int, ...]:
    return (_generations.get(ALL, 0), *(_generations.get(t, 0) for t in tables))


def cached(name: str, tables: Iterable[str], key: Hashable, load: Callable[[], T], copy_result: bool = True) -> T:
    """`load()`'s result, read again only once one of `tables` changed (or after TTL_SECONDS). The cached value is
    shared: callers get a deep copy unless `copy_result` is False (for immutable values)."""
    tables = tuple(tables)
    entry_key = (name, key)
    with _lock:
        generations = _current(tables)
        hit = _entries.get(entry_key)
    now = time.monotonic()
    if hit is not None and hit[1] == generations and now - hit[0] < TTL_SECONDS:
        value = hit[2]
    else:
        value = load()  # errors are not cached
        with _lock:
            if len(_entries) >= MAX_ENTRIES:
                _entries.clear()
            _entries[entry_key] = (now, generations, value)
    return copy.deepcopy(value) if copy_result else value


def changed(*tables: str) -> None:
    """Invalidate what was read from these tables (done automatically for ORM writes; see below)."""
    with _lock:
        for table in tables:
            _generations[table] = _generations.get(table, 0) + 1


def clear() -> None:
    """Forget everything (another database was opened)."""
    with _lock:
        _entries.clear()
        _generations[ALL] = _generations.get(ALL, 0) + 1


# --- invalidation by the writes ------------------------------------------------------------------------------------

_TOUCHED = "cache_touched_tables"


@event.listens_for(Session, "after_flush")
def _flushed(session: Session, _context) -> None:
    # Still the flush's own lists here: what it inserted, updated and deleted
    touched = session.info.setdefault(_TOUCHED, set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = getattr(type(obj), "__tablename__", None)
        touched.add(table or ALL)


@event.listens_for(Session, "do_orm_execute")
def _bulk_write(state) -> None:
    # insert(), update() and delete() statements run through the session, which never show up in a flush
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(getattr(state.statement, "table", None), "name", None)
    state.session.info.setdefault(_TOUCHED, set()).add(table or ALL)


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    touched = session.info.pop(_TOUCHED, None)
    if touched:
        changed(*touched)
