"""How much database work each API request does, to see where time goes on a remote database.

Every response gets a `Server-Timing` header (visible in the browser's DevTools, Network → Timing):
`db;dur=<ms>;desc="<n> statements", app;dur=<ms>`. Requests slower than SLOW_SECONDS are logged with the same numbers.
Statements are counted by engine events (app/db/session.py) into the request's counters, also from the worker threads
FastAPI runs endpoints in (they get a copy of the request's context, which shares the counters).
"""

import logging
import time
from contextvars import ContextVar

log = logging.getLogger("studio.timing")

SLOW_SECONDS = 1.0


class Counters:
    __slots__ = ("statements", "db_seconds")

    def __init__(self) -> None:
        self.statements = 0
        self.db_seconds = 0.0


_current: ContextVar[Counters | None] = ContextVar("request_counters", default=None)


def record(seconds: float) -> None:
    """One statement of the current request (none outside requests: runs' own work isn't counted)."""
    counters = _current.get()
    if counters is not None:
        counters.statements += 1
        counters.db_seconds += seconds


class TimingMiddleware:
    """Plain ASGI (not BaseHTTPMiddleware): streams such as the activity log pass through untouched."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        counters, started = Counters(), time.monotonic()
        token = _current.set(counters)

        async def timed_send(message):
            if message["type"] == "http.response.start":
                total = (time.monotonic() - started) * 1000
                value = (f'db;dur={counters.db_seconds * 1000:.0f};desc="{counters.statements} statements", '
                         f"app;dur={total:.0f}")
                message = {**message, "headers": [*message.get("headers", []), (b"server-timing", value.encode())]}
                if total / 1000 >= SLOW_SECONDS:
                    log.info("Slow request %s %s: %.0f ms, %d statements, %.0f ms in the database", scope["method"],
                             scope["path"], total, counters.statements, counters.db_seconds * 1000)
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            _current.reset(token)
