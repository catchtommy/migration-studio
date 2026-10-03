"""Runtime settings editable from the UI, persisted in the `settings` table."""

from typing import Any

from app.db.models import Setting
from app.db.session import session_scope

DEFAULTS: dict[str, Any] = {
    "monolith_repo": "",  # git URL or local path; empty = blank workspace
    "max_attempts": 3,  # self-verification retries per stage
    "jdk_legacy_home": "",  # JDK 8; each agent picks legacy or modern on the Agents page
    "jdk_modern_home": "",  # JDK 25
    "oracle_test_url": "",
}


def get_all() -> dict[str, Any]:
    with session_scope() as s:
        # Keys no longer in DEFAULTS (e.g. per-agent models, now on agent configs) are ignored.
        stored = {row.key: row.value for row in s.query(Setting).all() if row.key in DEFAULTS}
    return {**DEFAULTS, **stored}


def get(key: str) -> Any:
    return get_all()[key]


def update(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown settings: {', '.join(sorted(unknown))}")
    with session_scope() as s:
        for key, value in values.items():
            s.merge(Setting(key=key, value=value))
    return get_all()
