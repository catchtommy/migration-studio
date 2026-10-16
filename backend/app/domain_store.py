"""Domains of the monolith and the per-agent guidance attached to them."""

from pathlib import Path

from sqlalchemy import select

from app import projects
from app.db.models import (
    Approval, Artifact, AuditEvent, Contract, Dependency, Domain, Feature, Project, Question, Run, StageExecution,
)
from app.db.session import session_scope
from app.tools import git_ops

BUSY = ("running", "awaiting_input")  # a worker thread is using the run

# Seeded in the default project on first start; more can be added from the Domains page, or found by discovery.
INITIAL_DOMAINS = ["customer", "broker", "loan", "payments", "notification", "auditing", "reports"]


def seed() -> None:
    with session_scope() as s:
        if s.query(Domain).count() == 0 and s.get(Project, projects.DEFAULT) is not None:
            for key in INITIAL_DOMAINS:
                s.add(Domain(project=projects.DEFAULT, key=key, name=key.capitalize(), description="", context="",
                             agent_inputs={}))


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

    project = projects.current()
    with session_scope() as s:
        runs = s.scalars(select(Run).where(Run.project == project, Run.domain == key)).all()
        busy = [r for r in runs if r.status in BUSY or get_runner().is_active(r.id)]
        if busy:
            raise DomainBusy(f"{len(busy)} of its runs are still working; wait for them to stop, then delete")
        ids = [r.id for r in runs]
        workspaces = [r.workspace for r in runs if r.workspace]
        for model in (StageExecution, Artifact, Approval, AuditEvent, Question):
            s.query(model).filter(model.run_id.in_(ids)).delete(synchronize_session=False)
        s.query(Dependency).filter(Dependency.project == project, Dependency.run_id.in_(ids) |
                                   (Dependency.source == key) | (Dependency.target == key)).delete(
            synchronize_session=False)
        s.query(Contract).filter(Contract.project == project, Contract.provider == key).delete(
            synchronize_session=False)
        for contract in s.scalars(select(Contract).where(Contract.project == project)):
            if key in (contract.consumers or []):
                contract.consumers = [c for c in contract.consumers if c != key]
        s.query(Feature).filter(Feature.project == project, Feature.domain == key).delete(synchronize_session=False)
        s.query(Run).filter(Run.id.in_(ids)).delete(synchronize_session=False)
        domain = s.get(Domain, (project, key))
        if domain is not None:
            s.delete(domain)
    for run_id in ids:
        get_runner().forget(run_id)
    for ws in workspaces:
        git_ops.remove_tree(Path(ws))
    return len(ids)
