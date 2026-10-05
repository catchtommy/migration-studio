"""Domains of the monolith and the per-agent guidance attached to them."""

import shutil
from pathlib import Path

from sqlalchemy import select

from app.db.models import (
    Approval, Artifact, AuditEvent, Contract, Dependency, Domain, Feature, Question, Run, StageExecution,
)
from app.db.session import session_scope

BUSY = ("running", "awaiting_input")  # a worker thread is using the run

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


class DomainBusy(RuntimeError):
    pass


def delete_with_runs(key: str) -> int:
    """Delete a domain with everything recorded for it: its runs (stages, documents, approvals, questions, logs,
    workspaces, checkpoints), features, recorded dependencies and the contracts it provides. Returns the number of
    runs deleted. Refused while one of its runs is executing."""
    from app.orchestrator.runner import get_runner

    with session_scope() as s:
        runs = s.scalars(select(Run).where(Run.domain == key)).all()
        busy = [r for r in runs if r.status in BUSY or get_runner().is_active(r.id)]
        if busy:
            raise DomainBusy(f"{len(busy)} of its runs are still working; wait for them to stop, then delete")
        ids = [r.id for r in runs]
        workspaces = [r.workspace for r in runs if r.workspace]
        for model in (StageExecution, Artifact, Approval, AuditEvent, Question):
            s.query(model).filter(model.run_id.in_(ids)).delete(synchronize_session=False)
        s.query(Dependency).filter(Dependency.run_id.in_(ids) | (Dependency.source == key) |
                                   (Dependency.target == key)).delete(synchronize_session=False)
        s.query(Contract).filter(Contract.provider == key).delete(synchronize_session=False)
        for contract in s.scalars(select(Contract)):
            if key in (contract.consumers or []):
                contract.consumers = [c for c in contract.consumers if c != key]
        s.query(Feature).filter(Feature.domain == key).delete(synchronize_session=False)
        s.query(Run).filter(Run.id.in_(ids)).delete(synchronize_session=False)
        domain = s.get(Domain, key)
        if domain is not None:
            s.delete(domain)
    for run_id in ids:
        get_runner().forget(run_id)
    for ws in workspaces:
        shutil.rmtree(Path(ws), ignore_errors=True)
    return len(ids)
