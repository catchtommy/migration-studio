"""Check the studio's database connection the way the studio uses it: how long a round trip takes, what a read-only
session costs, and (with --write-check) that a committed write is really stored when COMMITs are skipped for sessions
that changed nothing (app/db/session.py).

Run from the backend folder, with the studio's own settings (data folder, database.json or STUDIO_DATABASE_URL):

    .venv/bin/python scripts/check_db_roundtrips.py               # read-only
    .venv/bin/python scripts/check_db_roundtrips.py --write-check # also writes, reads back and deletes one setting row

The write check uses a global setting row named _roundtrip_check, deleted again at the end.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db import config as db_config  # noqa: E402
from app.db import session  # noqa: E402
from app.db.models import GLOBAL, Setting  # noqa: E402

CHECK_KEY = "_roundtrip_check"


def timed(fn, times: int) -> list[float]:
    out = []
    for _ in range(times):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000)
    return out


def report(label: str, samples: list[float]) -> None:
    print(f"{label:<52} median {statistics.median(samples):7.1f} ms   (min {min(samples):.1f}, max {max(samples):.1f})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-check", action="store_true", help="write, read back and delete one setting row")
    parser.add_argument("--times", type=int, default=10)
    args = parser.parse_args()

    url = db_config.current_url()
    if url is None:
        print("No database is configured for the studio (data folder / database.json / STUDIO_DATABASE_URL)")
        return 2
    print(f"Database: {url.render_as_string(hide_password=True)}")
    started = time.perf_counter()
    session.configure(url)
    engine = session.get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1 FROM DUAL" if engine.dialect.name == "oracle" else "SELECT 1"))
    print(f"{'first connection (TLS, authentication)':<52} {(time.perf_counter() - started) * 1000:7.1f} ms")

    one = "SELECT 1 FROM DUAL" if engine.dialect.name == "oracle" else "SELECT 1"
    with engine.connect() as conn:
        report("one round trip (SELECT 1 on an open connection)", timed(lambda: conn.execute(text(one)), args.times))

    def read_only_session():
        with session.session_scope() as s:
            s.execute(text(one))

    # Before this change: SELECT + COMMIT + ROLLBACK (3 round trips). Now: the SELECT only.
    report("session_scope() with one SELECT", timed(read_only_session, args.times))

    if not args.write_check:
        print("\nRead-only checks done. Add --write-check to check that writes are committed.")
        return 0

    stamp = f"check-{time.time()}"
    try:
        with session.session_scope() as s:
            s.merge(Setting(project=GLOBAL, key=CHECK_KEY, value=stamp))
        # Read back on a brand-new connection: proves the COMMIT reached the database
        other = create_engine(url, poolclass=NullPool, connect_args=db_config.connect_args(url))
        try:
            with other.connect() as conn:
                stored = conn.execute(select(Setting.value).where(Setting.project == GLOBAL,
                                                                  Setting.key == CHECK_KEY)).scalar()
        finally:
            other.dispose()
        ok = stored == stamp
        print(f"\nWrite committed and visible from another connection: {'YES' if ok else 'NO (stored: %r)' % stored}")
        return 0 if ok else 1
    finally:
        with session.session_scope() as s:
            row = s.get(Setting, (GLOBAL, CHECK_KEY))
            if row is not None:
                s.delete(row)


if __name__ == "__main__":
    sys.exit(main())
