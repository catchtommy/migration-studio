"""Cross-domain contracts and dependencies, shared by every domain migration.

Integration design writes `integration-decisions.yaml` (and the deep dive `cross-domain.yaml`). Both are validated
before the agent may finish. When a reviewer approves the gate that reviews integration design, the decisions are
recorded here: contracts are upserted into the company-wide catalogue (so later migrations reuse them) and the run's
dependencies replace any recorded before.
"""

from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import select

from app.db.models import Artifact, Contract, Dependency, Domain, Run
from app.db.session import session_scope

KINDS = ("query", "bulk-query", "side-effect", "command", "shared-write", "reference-data")
PATTERNS = ("sync-api", "read-model", "event", "saga", "deferred", "reference-copy")
CONTRACT_KINDS = ("api", "event", "command")
CONTRACT_STATUSES = ("proposed", "approved", "provided-by-monolith", "provided-by-service", "deprecated")
DECISION_STATUSES = ("planned", "deferred", "done")
DECISIONS_FILE = "integration-decisions.yaml"
CATALOGUE_DIR = ".migration/contracts"  # catalogue specs copied into each run's workspace for its agents
CROSS_DOMAIN_FILE = "cross-domain.yaml"
DOMAIN_DEPENDENCIES_FILE = "domain-dependencies.yaml"  # written by the dependency finder
DEPENDENCY_RELATIONS = ("calls", "reads-data", "writes-data", "called-by", "shares-transaction")
DEPENDENCY_STRENGTHS = ("hard", "soft", "side-effect")
TOUCHPOINT_KINDS = ("service", "dao", "table", "view", "config", "event")
MODULITH_SCOPES = ("domain-only", "cross-domain")
# Statuses that record real progress; re-approving a design must not move a contract back from these.
_LIVE = ("provided-by-monolith", "provided-by-service", "deprecated")


class InvalidDocument(ValueError):
    pass


def _load(text: str, name: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise InvalidDocument(f"{name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise InvalidDocument(f"{name} must be a YAML mapping")
    return data


def _require(item: dict, fields: tuple[str, ...], where: str) -> None:
    missing = [f for f in fields if not item.get(f)]
    if missing:
        raise InvalidDocument(f"{where}: missing {', '.join(missing)}")


def _one_of(value: Any, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise InvalidDocument(f"{where}: {value!r} must be one of {', '.join(allowed)}")


def parse_cross_domain(text: str) -> list[dict]:
    data = _load(text, CROSS_DOMAIN_FILE)
    items = data.get("interactions")
    if not isinstance(items, list):
        raise InvalidDocument(f"{CROSS_DOMAIN_FILE}: 'interactions' must be a list (it can be empty)")
    seen = set()
    for i, item in enumerate(items, 1):
        where = f"{CROSS_DOMAIN_FILE} interaction {i}"
        if not isinstance(item, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(item, ("id", "target", "kind", "summary"), where)
        _one_of(item["kind"], KINDS, where)
        _one_of(item.get("direction", "outbound"), ("outbound", "inbound"), where)
        if item["id"] in seen:
            raise InvalidDocument(f"{where}: duplicate id {item['id']}")
        seen.add(item["id"])
    return items


def parse_domain_dependencies(text: str) -> list[dict]:
    """The other domains one feature is related to, from the dependency finder's file."""
    return load_domain_dependencies(text)["dependencies"]


def load_domain_dependencies(text: str) -> dict:
    """The dependency finder's whole file, validated: its Modulith scope decision and the dependencies."""
    data = _load(text, DOMAIN_DEPENDENCIES_FILE)
    items = data.get("dependencies")
    if not isinstance(items, list):
        raise InvalidDocument(f"{DOMAIN_DEPENDENCIES_FILE}: 'dependencies' must be a list (it can be empty)")
    seen = set()
    for i, item in enumerate(items, 1):
        where = f"{DOMAIN_DEPENDENCIES_FILE} dependency {i}"
        if not isinstance(item, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(item, ("domain", "relation"), where)
        _one_of(item["relation"], DEPENDENCY_RELATIONS, where)
        _one_of(item.get("direction", "outbound"), ("outbound", "inbound"), where)
        if item.get("strength") is not None:
            _one_of(item["strength"], DEPENDENCY_STRENGTHS, where)
        if not isinstance(item.get("touchpoints"), list) or not item["touchpoints"]:
            raise InvalidDocument(f"{where}: list the touchpoints (the services, DAOs or tables where "
                                  f"{item['domain']} and this feature meet)")
        for j, t in enumerate(item["touchpoints"], 1):
            t_where = f"{where} touchpoint {j}"
            if not isinstance(t, dict):
                raise InvalidDocument(f"{t_where} must be a mapping")
            _require(t, ("kind", "ref"), t_where)
            _one_of(t["kind"], TOUCHPOINT_KINDS, t_where)
        key = (item["domain"], item["relation"], item.get("direction", "outbound"))
        if key in seen:
            raise InvalidDocument(f"{where}: {item['domain']} is listed twice with the same relation and direction; "
                                  "merge the touchpoints")
        seen.add(key)
    decision = data.get("decision")
    where = f"{DOMAIN_DEPENDENCIES_FILE} decision"
    if not isinstance(decision, dict):
        raise InvalidDocument(f"{where}: add a decision with a scope (domain-only or cross-domain) and a reason")
    _require(decision, ("scope", "reason"), where)
    _one_of(decision["scope"], MODULITH_SCOPES, where)
    outbound = [i["domain"] for i in items if i.get("direction", "outbound") == "outbound"]
    if decision["scope"] == "domain-only" and outbound:
        raise InvalidDocument(f"{where}: scope domain-only, but the feature has outbound dependencies on "
                              f"{', '.join(dict.fromkeys(outbound))}; choose cross-domain or correct the list")
    if decision["scope"] == "cross-domain" and not outbound:
        raise InvalidDocument(f"{where}: scope cross-domain, but no outbound dependency is listed; "
                              "choose domain-only or list the dependency")
    return {"decision": decision, "dependencies": items}


def parse_decisions(text: str) -> dict:
    data = _load(text, DECISIONS_FILE)
    decisions, contracts = data.get("decisions"), data.get("contracts") or []
    if not isinstance(decisions, list) or not isinstance(contracts, list):
        raise InvalidDocument(f"{DECISIONS_FILE}: 'decisions' and 'contracts' must be lists")
    keys = set()
    for i, c in enumerate(contracts, 1):
        where = f"{DECISIONS_FILE} contract {i}"
        if not isinstance(c, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(c, ("key", "provider", "kind", "name"), where)
        _one_of(c["kind"], CONTRACT_KINDS, where)
        if c.get("consumers") is not None and not isinstance(c["consumers"], list):
            raise InvalidDocument(f"{where}: consumers must be a list")
        keys.add(c["key"])
    for i, d in enumerate(decisions, 1):
        where = f"{DECISIONS_FILE} decision {i}"
        if not isinstance(d, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(d, ("interaction", "target", "kind", "pattern"), where)
        _one_of(d["kind"], KINDS, where)
        _one_of(d["pattern"], PATTERNS, where)
        _one_of(d.get("direction", "outbound"), ("outbound", "inbound"), where)
        _one_of(d.get("status", "planned"), DECISION_STATUSES, where)
        if d["pattern"] not in ("deferred", "reference-copy") and not d.get("contract"):
            raise InvalidDocument(f"{where}: pattern {d['pattern']} needs a contract")
        if d.get("contract") and d["contract"] not in keys and not _exists(d["contract"]):
            raise InvalidDocument(f"{where}: contract {d['contract']} is neither listed under contracts nor in "
                                  "the catalogue")
    return {"decisions": decisions, "contracts": contracts}


def _exists(key: str) -> bool:
    with session_scope() as s:
        return s.get(Contract, key) is not None


def validate_outputs(output_dir: Path) -> list[str]:
    """Problems with the structured cross-domain files an agent wrote (none when the files are absent)."""
    problems = []
    for name, parse in ((CROSS_DOMAIN_FILE, parse_cross_domain), (DOMAIN_DEPENDENCIES_FILE, parse_domain_dependencies),
                        (DECISIONS_FILE, parse_decisions)):
        f = output_dir / name
        if not f.is_file():
            continue
        try:
            parsed = parse(f.read_text())
        except InvalidDocument as e:
            problems.append(str(e))
            continue
        if name == DECISIONS_FILE:
            for c in parsed["contracts"]:
                if c.get("spec") and not (output_dir / c["spec"]).is_file():
                    problems.append(f"{DECISIONS_FILE}: spec file {c['spec']} for contract {c['key']} does not exist")
    return problems


def _latest_decisions(s, run_id: str, stages: list[str]) -> Artifact | None:
    rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(stages))
                     .order_by(Artifact.attempt.desc(), Artifact.id.desc())).all()
    latest_attempt: dict[str, int] = {}
    for a in rows:
        latest_attempt.setdefault(a.stage, a.attempt)
    return next((a for a in rows if a.name == DECISIONS_FILE and a.attempt == latest_attempt[a.stage]), None)


def record_approved_design(run_id: str, reviewed_stages: list[str]) -> int:
    """On approval of a gate: record the run's integration decisions and contracts. Reads the database copy, which has
    reviewers' manual edits. Returns the number of decisions recorded (0 when the gate reviews none)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        artifact = _latest_decisions(s, run_id, reviewed_stages)
        if artifact is None:
            return 0
        parsed = parse_decisions(artifact.content)
        base = PurePosixPath(artifact.path).parent

        for c in parsed["contracts"]:
            spec_path = str(base / c["spec"]) if c.get("spec") else None
            spec_doc = spec_path and s.scalar(select(Artifact).where(Artifact.run_id == run_id, Artifact.path == spec_path)
                                              .order_by(Artifact.id.desc()).limit(1))
            spec = spec_doc.content if spec_doc else None
            row = s.get(Contract, c["key"])
            if row is None:
                row = Contract(key=c["key"], consumers=[], status="approved")
                s.add(row)
            elif row.status not in _LIVE:
                row.status = "approved"
            row.provider, row.kind, row.name = c["provider"], c["kind"], c["name"]
            row.version = str(c.get("version") or row.version or "1")
            row.description = c.get("description") or row.description or ""
            row.consumers = sorted(set(row.consumers or []) | set(c.get("consumers") or []))
            row.spec, row.spec_path, row.run_id = spec or row.spec, spec_path or row.spec_path, run_id

        s.query(Dependency).filter(Dependency.run_id == run_id).delete()
        for d in parsed["decisions"]:
            outbound = d.get("direction", "outbound") == "outbound"
            s.add(Dependency(
                run_id=run_id, interaction=str(d["interaction"]),
                source=run.domain if outbound else d["target"], target=d["target"] if outbound else run.domain,
                kind=d["kind"], pattern=d["pattern"], contract=d.get("contract"),
                status=d.get("status", "planned"), summary=d.get("summary", ""),
                code_refs=list(d.get("code_refs") or []),
            ))
        return len(parsed["decisions"])


def export_specs(workspace: Path) -> int:
    """Copy the catalogue's contract specs into the workspace so agents can read them. Returns how many changed."""
    with session_scope() as s:
        specs = [(c.key, c.spec) for c in s.scalars(select(Contract)) if c.spec]
    changed = 0
    for key, spec in specs:
        f = workspace / CATALOGUE_DIR / f"{key}.yaml"
        if not f.is_file() or f.read_text() != spec:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(spec)
            changed += 1
    return changed


def as_dict(c: Contract) -> dict[str, Any]:
    return {"key": c.key, "provider": c.provider, "kind": c.kind, "name": c.name, "version": c.version,
            "description": c.description, "spec": c.spec, "spec_path": c.spec_path, "consumers": c.consumers or [],
            "status": c.status, "run_id": c.run_id, "created_at": c.created_at, "updated_at": c.updated_at}


def list_contracts(domain: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(Contract).order_by(Contract.provider, Contract.key)).all()
        return [as_dict(c) for c in rows if domain is None or c.provider == domain or domain in (c.consumers or [])]


def catalogue_brief(domain: str, settings: dict) -> str:
    """What every agent should know about cross-domain integration: ownership, conventions and contracts."""
    lines = ["## Cross-domain integration"]
    with session_scope() as s:
        domains = s.scalars(select(Domain).order_by(Domain.key)).all()
        owned = [(d.key, d.owned_tables or [], d.owned_packages or []) for d in domains
                 if d.owned_tables or d.owned_packages]
        contracts = s.scalars(select(Contract).order_by(Contract.provider, Contract.key)).all()
        if owned:
            lines.append("Ownership (tables, Java packages):")
            lines += [f"- {key}{' (this domain)' if key == domain else ''}: tables {', '.join(t) or '–'}; "
                      f"packages {', '.join(p) or '–'}" for key, t, p in owned]
        else:
            lines.append("No table or package ownership has been recorded for the domains yet; infer it from the "
                         "code and ask the user when unsure.")
        topic = settings.get("event_topic_pattern") or "{domain}-events"
        queue = settings.get("command_queue_pattern") or "{domain}-commands"
        ns = settings.get("event_bus_namespace") or "(namespace not configured yet)"
        lines.append(f"Messaging: Azure Service Bus {ns}; this domain publishes events on topic "
                     f"`{topic.format(domain=domain)}` and receives saga commands on queue "
                     f"`{queue.format(domain=domain)}`.")
        service_dir = (settings.get("service_dir_pattern") or "services/{domain}-service").format(domain=domain)
        lines.append(f"This domain's microservice lives in `{service_dir}/`.")
        if contracts:
            lines.append("Contracts catalogue (reuse these keys; provider → consumers, status):")
            lines += [f"- `{c.key}` {c.kind} \"{c.name}\" v{c.version}: {c.provider} → "
                      f"{', '.join(c.consumers or []) or 'no consumers yet'} ({c.status})"
                      + (f", spec `{CATALOGUE_DIR}/{c.key}.yaml`" if c.spec else "") for c in contracts]
        else:
            lines.append("The contracts catalogue is empty: this is the first domain to define cross-domain contracts.")
    return "\n".join(lines)


# Patterns where one domain needs the other to answer or act right away: they shape the migration order.
BLOCKING = ("sync-api", "saga", "deferred")


def dependency_map() -> dict[str, Any]:
    """Domain-to-domain edges (caller/consumer → provider) from approved designs, and a suggested migration order:
    domains that others depend on synchronously the least go first; domains not analysed yet go last."""
    with session_scope() as s:
        domains = [d.key for d in s.scalars(select(Domain).order_by(Domain.created_at, Domain.key))]
        deps = s.scalars(select(Dependency)).all()
        analysed = {r.domain for r in s.scalars(select(Run).where(Run.id.in_({d.run_id for d in deps})))}
        edges: dict[tuple[str, str], dict[str, int]] = {}
        for d in deps:
            patterns = edges.setdefault((d.source, d.target), {})
            patterns[d.pattern] = patterns.get(d.pattern, 0) + 1

    def dependants(domain: str, blocking: bool) -> list[str]:
        return sorted({src for (src, tgt), pats in edges.items() if tgt == domain and src != domain
                       and (not blocking or any(p in BLOCKING for p in pats))})

    known = [d for d in domains if d in analysed or any(d in edge for edge in edges)]
    order = []
    for d in sorted(known, key=lambda d: (len(dependants(d, True)), len(dependants(d, False)), domains.index(d))):
        sync = dependants(d, True)
        reason = (f"{len(sync)} domain(s) depend on it synchronously ({', '.join(sync)})" if sync
                  else "No other domain depends on it synchronously")
        others = [x for x in dependants(d, False) if x not in sync]
        if others:
            reason += f"; {', '.join(others)} {'uses' if len(others) == 1 else 'use'} its events or read models"
        order.append({"domain": d, "reason": reason})
    order += [{"domain": d, "reason": "No approved cross-domain analysis yet"} for d in domains if d not in known]
    return {
        "domains": domains,
        "edges": [{"source": src, "target": tgt, "count": sum(p.values()), "patterns": p}
                  for (src, tgt), p in sorted(edges.items())],
        "order": order,
    }
