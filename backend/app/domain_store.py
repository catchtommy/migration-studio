"""Domains of the monolith and the per-agent guidance attached to them."""

from sqlalchemy import case, select

from app import projects
from app.db.models import Contract, Dependency, Domain, Feature, Project, Run
from app.db.session import session_scope

BUSY = ("running", "awaiting_input")  # a worker thread is using the run

# Seeded in the default project on first start; more can be added from the Domains page, or found by discovery.
INITIAL_DOMAINS = ["customer", "broker", "loan", "payments", "notification", "auditing", "reports"]


def seed() -> None:
    with session_scope() as s:
        if s.query(Domain).count() == 0 and s.get(Project, projects.DEFAULT) is not None:
            for key in INITIAL_DOMAINS:
                s.add(Domain(project=projects.DEFAULT, key=key, name=key.capitalize(), description="", context="",
                             agent_inputs={}))


def in_migration_order(query):
    """`query` (selecting domains) ordered as they are migrated: by their place in the order set on the Domains page,
    then those not placed yet, oldest first."""
    return query.order_by(case((Domain.migration_order.is_(None), 1), else_=0), Domain.migration_order,
                          Domain.created_at, Domain.key)


def set_migration_order(db, project: str, keys: list[str]) -> list[str]:
    """Number the project's domains 1, 2, ... in the order of `keys`; domains not in it follow in their current order.
    Raises ValueError for an unknown or repeated key. Returns the keys in their new order."""
    domains = {d.key: d for d in db.scalars(in_migration_order(select(Domain).where(Domain.project == project)))}
    if len(set(keys)) != len(keys):
        raise ValueError("A domain appears more than once in the order")
    unknown = [k for k in keys if k not in domains]
    if unknown:
        raise ValueError(f"Unknown domain(s): {', '.join(unknown)}")
    order = [*keys, *(k for k in domains if k not in keys)]
    for i, key in enumerate(order, 1):
        domains[key].migration_order = i
    return order


def normalize_inputs(raw: dict | None) -> dict:
    """{"paths": [...], "notes": "...", "repository"?, "base_branch"?} with blank paths dropped; the repository and
    base branch (an agent whose step changes code) only when set."""
    raw = raw or {}
    out = {
        "paths": [p.strip() for p in raw.get("paths") or [] if p and p.strip()],
        "notes": (raw.get("notes") or "").strip(),
    }
    for field in ("repository", "base_branch"):
        value = str(raw.get(field) or "").strip()
        if value:
            out[field] = value
    return out


def new_domain_problem(s, project: str, key: str) -> str | None:
    """Why a domain can't be created with this key. A domain's package is <prefix>.<its key without dashes>."""
    from app.domain_modules import domain_segment

    if s.get(Domain, (project, key)) is not None:
        return f"Domain {key} already exists"
    other = next((d for d in s.scalars(select(Domain.key).where(Domain.project == project))
                  if domain_segment(d) == domain_segment(key)), None)
    if other is not None:
        return (f"The domains {key} and {other} would share the package {domain_segment(key)}: give it a key that "
                "differs in more than dashes")
    return None


class DomainBusy(RuntimeError):
    pass


def delete_with_runs(key: str) -> int:
    """Delete a domain with everything recorded for it: its runs (stages, documents, approvals, questions, logs,
    workspaces, checkpoints: app/run_cleanup.py), features, recorded dependencies and the contracts it provides.
    Returns the number of runs deleted. Refused while one of its runs is executing."""
    from app import run_cleanup
    from app.orchestrator.runner import get_runner

    project = projects.current()
    with session_scope() as s:
        runs = s.scalars(select(Run).where(Run.project == project, Run.domain == key)).all()
        busy = [r for r in runs if r.status in BUSY or get_runner().is_active(r.id)]
        if busy:
            raise DomainBusy(f"{len(busy)} of its runs are still working; wait for them to stop, then delete")
        ids = [r.id for r in runs]
        s.query(Dependency).filter(Dependency.project == project, (Dependency.source == key) |
                                   (Dependency.target == key)).delete(synchronize_session=False)
        s.query(Contract).filter(Contract.project == project, Contract.provider == key).delete(
            synchronize_session=False)
        for contract in s.scalars(select(Contract).where(Contract.project == project)):
            if key in (contract.consumers or []):
                contract.consumers = [c for c in contract.consumers if c != key]
        s.query(Feature).filter(Feature.project == project, Feature.domain == key).delete(synchronize_session=False)
    deleted = run_cleanup.delete(project, ids)
    with session_scope() as s:
        domain = s.get(Domain, (project, key))
        if domain is not None:
            s.delete(domain)
    return deleted
