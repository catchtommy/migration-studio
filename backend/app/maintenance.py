"""Holding back changes while the studio's files move. Everything that starts a run, fetches a repository or commits
comes in through a request to the project routers, so refusing those at the door leaves nothing half done (a run row
with nothing started), and a change waits for the ones already in progress (a commit or a push is no run)."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request

_changed = threading.Condition()
_held: str | None = None
_working = 0  # requests in progress that may change something


class Busy(RuntimeError):
    pass


def guard(request: Request) -> Iterator[None]:
    """FastAPI dependency. Reading goes on as usual."""
    global _working
    if request.method in ("GET", "HEAD", "OPTIONS"):
        yield
        return
    with _changed:
        if _held:
            raise HTTPException(409, _held)
        _working += 1
    try:
        yield
    finally:
        with _changed:
            _working -= 1
            _changed.notify_all()


@contextmanager
def hold(reason: str, wait: float = 5.0) -> Iterator[None]:
    """Refuse changes with `reason` for the duration, once those in progress have finished (Busy if they take longer
    than `wait` seconds, or something else is holding them back already)."""
    global _held
    with _changed:
        if _held:
            raise Busy(_held)
        _held = reason
        if not _changed.wait_for(lambda: not _working, wait):
            _held = None
            raise Busy("Other changes are still in progress; try again when they have finished")
    try:
        yield
    finally:
        with _changed:
            _held = None
