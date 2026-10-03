"""Domains of the monolith and the per-agent guidance attached to them."""

from app.db.models import Domain
from app.db.session import session_scope

# Seeded on first start; more can be added from the Domains page.
INITIAL_DOMAINS = ["customer", "broker", "loan", "payments", "notification", "auditing", "reports"]


def seed() -> None:
    with session_scope() as s:
        if s.query(Domain).count() == 0:
            for key in INITIAL_DOMAINS:
                s.add(Domain(key=key, name=key.capitalize(), description="", context="", agent_inputs={}))


def normalize_inputs(raw: dict | None) -> dict:
    """{"paths": [...], "notes": "..."} with blank paths dropped."""
    raw = raw or {}
    return {
        "paths": [p.strip() for p in raw.get("paths") or [] if p and p.strip()],
        "notes": (raw.get("notes") or "").strip(),
    }
