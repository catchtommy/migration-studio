"""Cross-domain contracts and dependencies, shared by every domain migration.

Integration design writes `integration-decisions.yaml` (and the deep dive `cross-domain.yaml`). Both are validated
before the agent may finish. When a reviewer approves the gate that reviews integration design, the decisions are
recorded here: contracts are upserted into the company-wide catalogue (so later migrations reuse them) and the run's
dependencies replace any recorded before.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sqlalchemy import select

from app import projects
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
LINKED_CONTRACTS_FILE = "linked-domain-contracts.yaml"  # written by the linked-domain contracts step
MODULE_EXTRACTION_FILE = "module-extraction.yaml"  # the Module in monolith step's declaration of what it changed
COORDINATES = re.compile(r"^[\w.\-]+:[\w.\-]+$")  # Maven groupId:artifactId
DOMAIN_DESIGN_FILE = "domain-design.yaml"  # the domain design's declaration against the guardrails
# Lists in domain-design.yaml that must stay empty unless the user approved an exception for that rule.
DOMAIN_DESIGN_LOCKED = {"schema_changes": "the schema is fixed", "ui_changes": "the UI is fixed",
                        "stack_changes": "the stack is fixed",
                        "god_module_dependencies": "the domain must not depend on a God module"}
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
    for field in ("shared_code", "god_modules"):
        if data.get(field) is not None and not isinstance(data[field], list):
            raise InvalidDocument(f"{DOMAIN_DEPENDENCIES_FILE}: {field} must be a list")
    both = set(data.get("shared_code") or []) & set(data.get("god_modules") or [])
    if both:
        raise InvalidDocument(f"{DOMAIN_DEPENDENCIES_FILE}: {', '.join(sorted(both))} is listed as both shared_code "
                              "(a utility) and god_modules; it is one or the other")
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
    return {"decision": decision, "dependencies": items, "shared_code": data.get("shared_code") or [],
            "god_modules": data.get("god_modules") or []}


def parse_linked_domain_contracts(text: str) -> list[dict]:
    """The contracts between one feature and the domains it depends on, from the linked-domain contracts file."""
    data = _load(text, LINKED_CONTRACTS_FILE)
    items = data.get("contracts")
    if not isinstance(items, list):
        raise InvalidDocument(f"{LINKED_CONTRACTS_FILE}: 'contracts' must be a list (it can be empty)")
    seen = set()
    for i, c in enumerate(items, 1):
        where = f"{LINKED_CONTRACTS_FILE} contract {i}"
        if not isinstance(c, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(c, ("key", "provider", "kind", "name"), where)
        _one_of(c["kind"], CONTRACT_KINDS, where)
        if not str(c["key"]).startswith(f"{c['provider']}."):
            raise InvalidDocument(f"{where}: key {c['key']} must start with its provider, as {c['provider']}.<name>")
        if c["key"] in seen:
            raise InvalidDocument(f"{where}: duplicate key {c['key']}")
        seen.add(c["key"])
        if c.get("interactions") is not None and not isinstance(c["interactions"], list):
            raise InvalidDocument(f"{where}: interactions must be a list")
        if c.get("reuse"):
            continue  # already in the catalogue: nothing more to define
        if not c.get("spec"):
            raise InvalidDocument(f"{where}: missing spec (or set reuse: true for a contract already in the "
                                  "catalogue)")
        binding = c.get("binding")
        if not isinstance(binding, dict) or not isinstance(binding.get("delegates_to"), list) \
                or not binding["delegates_to"]:
            raise InvalidDocument(f"{where}: describe the temporary binding: binding.delegates_to lists the existing "
                                  "Class#method the monolith delegates to")
        _require(binding, ("remove_when",), f"{where} binding")
    return items


def _linked_contracts_problems(output_dir: Path, contracts: list[dict]) -> list[str]:
    """Check the contracts against the dependency finder's file next to them: every domain the feature depends on
    has a contract, and no contract is for a domain the feature does not depend on."""
    f = output_dir / DOMAIN_DEPENDENCIES_FILE
    if not f.is_file():
        return []
    try:
        deps = load_domain_dependencies(f.read_text(encoding="utf-8"))["dependencies"]
    except InvalidDocument:
        return []  # reported for that file itself
    outbound = {d["domain"] for d in deps if d.get("direction", "outbound") == "outbound"}
    providers = {c["provider"] for c in contracts}
    problems = [f"{LINKED_CONTRACTS_FILE}: no contract for {d}, which {DOMAIN_DEPENDENCIES_FILE} lists as an "
                "outbound dependency" for d in sorted(outbound - providers)]
    problems += [f"{LINKED_CONTRACTS_FILE}: contract {c['key']} is for {c['provider']}, which is not an outbound "
                 f"dependency in {DOMAIN_DEPENDENCIES_FILE}" for c in contracts if c["provider"] not in outbound]
    return problems


def parse_domain_design(text: str) -> dict:
    """The domain design's guardrail declaration. The locked lists must be empty, unless an exception for that rule
    was recorded."""
    data = _load(text, DOMAIN_DESIGN_FILE)
    where = DOMAIN_DESIGN_FILE
    _require(data, ("domain",), where)
    lists = ("owned_tables", "read_only_tables", "utility_modules", "other_domain_dependencies", *DOMAIN_DESIGN_LOCKED)
    for field in lists:
        if data.get(field) is None:
            raise InvalidDocument(f"{where}: missing {field} (write {field}: [] when there is none)")
        if not isinstance(data[field], list):
            raise InvalidDocument(f"{where}: {field} must be a list")
    exceptions = data.get("exceptions") or []
    if not isinstance(exceptions, list):
        raise InvalidDocument(f"{where}: exceptions must be a list")
    for i, e in enumerate(exceptions, 1):
        if not isinstance(e, dict):
            raise InvalidDocument(f"{where} exception {i} must be a mapping")
        _require(e, ("rule", "reason"), f"{where} exception {i}")
        _one_of(e["rule"], tuple(DOMAIN_DESIGN_LOCKED), f"{where} exception {i}")
    excused = {e["rule"] for e in exceptions}
    for field, why in DOMAIN_DESIGN_LOCKED.items():
        if data[field] and field not in excused:
            raise InvalidDocument(
                f"{where}: {field} must be empty ({why}); found {', '.join(map(str, data[field]))}. Remove them, or "
                f"ask the user and record the approved exception for {field} under exceptions")
    for u in data["utility_modules"]:
        if not isinstance(u, str) or not COORDINATES.match(u):
            raise InvalidDocument(f"{where}: utility module {u!r} must be Maven coordinates, groupId:artifactId "
                                  "(for example com.acme:common-utils)")
    overlap = {t.upper() for t in data["owned_tables"]} & {t.upper() for t in data["read_only_tables"]}
    if overlap:
        raise InvalidDocument(f"{where}: {', '.join(sorted(overlap))} is in both owned_tables and read_only_tables")
    return data


def parse_module_extraction(text: str) -> dict:
    """What the Module in monolith step declares it created and changed. The guardrail check compares it with git."""
    data = _load(text, MODULE_EXTRACTION_FILE)
    where = MODULE_EXTRACTION_FILE
    _require(data, ("feature", "domain", "scope"), where)
    _one_of(data["scope"], MODULITH_SCOPES, f"{where} scope")
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise InvalidDocument(f"{where}: list the new modules (at least the domain module)")
    for i, m in enumerate(modules, 1):
        if not isinstance(m, dict):
            raise InvalidDocument(f"{where} module {i} must be a mapping")
        _require(m, ("path", "kind", "artifact"), f"{where} module {i}")
        _one_of(m["kind"], ("domain", "orchestrator"), f"{where} module {i}")
        if str(m["path"]).startswith("/") or ".." in Path(str(m["path"])).parts:
            raise InvalidDocument(f"{where} module {i}: path must be inside the repository")
        if not COORDINATES.match(str(m["artifact"])):
            raise InvalidDocument(f"{where} module {i}: artifact must be Maven coordinates, groupId:artifactId")
    kinds = [m["kind"] for m in modules]
    if kinds.count("domain") != 1 or kinds.count("orchestrator") > 1:
        raise InvalidDocument(f"{where}: exactly one domain module and at most one orchestrator module")
    if data["scope"] == "domain-only" and ("orchestrator" in kinds or data.get("bridge_dir")):
        raise InvalidDocument(f"{where}: the feature is domain-only, so there is no orchestrator module and no "
                              "bridge_dir; callers use the domain's public API directly")
    if data["scope"] == "cross-domain" and ("orchestrator" not in kinds or not data.get("bridge_dir")):
        raise InvalidDocument(f"{where}: the feature is cross-domain, so add an orchestrator module and the "
                              "bridge_dir (the temporary legacy bridge package in the monolith)")
    for field in ("outside_changes", "events"):
        if data.get(field) is not None and not isinstance(data[field], list):
            raise InvalidDocument(f"{where}: {field} must be a list")
    for i, c in enumerate(data.get("shared_copies") or [], 1):
        if not isinstance(c, dict):
            raise InvalidDocument(f"{where} shared copy {i} must be a mapping")
        _require(c, ("item", "remove_when"), f"{where} shared copy {i}")
    return data


def _module_extraction_problems(output_dir: Path, declaration: dict) -> list[str]:
    deps = output_dir / DOMAIN_DEPENDENCIES_FILE
    if not deps.is_file():
        return []
    try:
        scope = load_domain_dependencies(deps.read_text(encoding="utf-8"))["decision"]["scope"]
    except InvalidDocument:
        return []
    if scope != declaration["scope"]:
        return [f"{MODULE_EXTRACTION_FILE}: scope is {declaration['scope']}, but {DOMAIN_DEPENDENCIES_FILE} decides "
                f"{scope}"]
    return []


def _domain_design_problems(output_dir: Path, design: dict) -> list[str]:
    """Check the declaration against the contracts and the dependency finder's decision next to it: the domain may
    depend on other domains only through linked-domain contracts."""
    named = [str(d) for d in design["other_domain_dependencies"]]
    linked = output_dir / LINKED_CONTRACTS_FILE
    if linked.is_file():
        try:
            keys = {c["key"] for c in parse_linked_domain_contracts(linked.read_text(encoding="utf-8"))}
        except InvalidDocument:
            return []  # reported for that file itself
        return [f"{DOMAIN_DESIGN_FILE}: other_domain_dependencies names {d}, which is not a contract in "
                f"{LINKED_CONTRACTS_FILE}; a domain depends on another only through a contract" for d in named
                if d not in keys]
    deps = output_dir / DOMAIN_DEPENDENCIES_FILE
    if named and deps.is_file():
        try:
            if load_domain_dependencies(deps.read_text(encoding="utf-8"))["decision"]["scope"] == "domain-only":
                return [f"{DOMAIN_DESIGN_FILE}: other_domain_dependencies must be empty: {DOMAIN_DEPENDENCIES_FILE} "
                        "decides this feature is domain-only"]
        except InvalidDocument:
            return []
    return []


def domain_design_ownership_problems(output_dir: Path, domain: str, ownership: dict[str, dict]) -> list[str]:
    """Tables the domain design claims, owned or read, that another domain owns. Empty when the file is absent or
    invalid (that is reported by validate_outputs)."""
    f = output_dir / DOMAIN_DESIGN_FILE
    if not f.is_file():
        return []
    try:
        design = parse_domain_design(f.read_text(encoding="utf-8"))
    except InvalidDocument:
        return []
    owners = {t.upper(): other for other, o in ownership.items() if other != domain for t in o.get("tables") or []}
    return [f"{DOMAIN_DESIGN_FILE}: table {t} is owned by domain {owners[t.upper()]}; this domain may only reach it "
            "through a contract, never own or read it directly"
            for t in [*design["owned_tables"], *design["read_only_tables"]] if t.upper() in owners]


def parse_decisions(text: str, known: frozenset[str] | set[str] = frozenset()) -> dict:
    """`known`: contract keys defined elsewhere in the run (the linked-domain contracts), usable without listing."""
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
        if d.get("contract") and d["contract"] not in keys and d["contract"] not in known \
                and not _exists(d["contract"]):
            raise InvalidDocument(f"{where}: contract {d['contract']} is neither listed under contracts, nor a "
                                  "linked-domain contract, nor in the catalogue")
    return {"decisions": decisions, "contracts": contracts}


def _exists(key: str) -> bool:
    with session_scope() as s:
        return s.get(Contract, (projects.current(), key)) is not None


def validate_outputs(output_dir: Path) -> list[str]:
    """Problems with the structured cross-domain files an agent wrote (none when the files are absent)."""
    problems = []
    linked: list[dict] = []
    for name, parse in ((CROSS_DOMAIN_FILE, parse_cross_domain), (DOMAIN_DEPENDENCIES_FILE, parse_domain_dependencies),
                        (LINKED_CONTRACTS_FILE, parse_linked_domain_contracts),
                        (DOMAIN_DESIGN_FILE, parse_domain_design),
                        (MODULE_EXTRACTION_FILE, parse_module_extraction),
                        (DECISIONS_FILE, lambda text: parse_decisions(text, {c["key"] for c in linked}))):
        f = output_dir / name
        if not f.is_file():
            continue
        try:
            parsed = parse(f.read_text(encoding="utf-8"))
        except InvalidDocument as e:
            problems.append(str(e))
            continue
        if name == LINKED_CONTRACTS_FILE:
            linked = parsed
            problems += _linked_contracts_problems(output_dir, parsed)
            problems += [f"{LINKED_CONTRACTS_FILE}: spec file {c['spec']} for contract {c['key']} does not exist"
                         for c in parsed if c.get("spec") and not (output_dir / c["spec"]).is_file()]
        if name == DOMAIN_DESIGN_FILE:
            problems += _domain_design_problems(output_dir, parsed)
        if name == MODULE_EXTRACTION_FILE:
            problems += _module_extraction_problems(output_dir, parsed)
        if name == DECISIONS_FILE:
            for c in parsed["contracts"]:
                if c.get("spec") and not (output_dir / c["spec"]).is_file():
                    problems.append(f"{DECISIONS_FILE}: spec file {c['spec']} for contract {c['key']} does not exist")
    return problems


def _latest_artifact(s, run_id: str, stages: list[str], name: str) -> Artifact | None:
    """The file `name` from the latest attempt of the first of these stages that wrote it."""
    rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(stages))
                     .order_by(Artifact.attempt.desc(), Artifact.id.desc())).all()
    latest_attempt: dict[str, int] = {}
    for a in rows:
        latest_attempt.setdefault(a.stage, a.attempt)
    return next((a for a in rows if a.name == name and a.attempt == latest_attempt[a.stage]), None)


def _latest_decisions(s, run_id: str, stages: list[str]) -> Artifact | None:
    return _latest_artifact(s, run_id, stages, DECISIONS_FILE)


def _upsert_contract(s, project: str, run_id: str, c: dict, base: PurePosixPath, status: str,
                     consumers: list[str]) -> None:
    """Add or update a catalogue contract from a design file. A contract that is already provided is never moved
    back, and `status` only upgrades a contract that is merely proposed."""
    spec_path = str(base / c["spec"]) if c.get("spec") else None
    spec_doc = spec_path and s.scalar(select(Artifact).where(Artifact.run_id == run_id, Artifact.path == spec_path)
                                      .order_by(Artifact.id.desc()).limit(1))
    spec = spec_doc.content if spec_doc else None
    row = s.get(Contract, (project, c["key"]))
    existed = row is not None
    if row is None:
        row = Contract(project=project, key=c["key"], consumers=[], status=status)
        s.add(row)
    elif status == "approved" and row.status not in _LIVE:
        row.status = "approved"
    row.consumers = sorted(set(row.consumers or []) | set(consumers))
    if c.get("reuse") and existed:
        return  # only a new consumer: the catalogue's definition stands
    row.provider, row.kind, row.name = c["provider"], c["kind"], c["name"]
    row.version = str(c.get("version") or row.version or "1")
    row.description = c.get("description") or row.description or ""
    row.spec, row.spec_path, row.run_id = spec or row.spec, spec_path or row.spec_path, run_id


def record_linked_domain_contracts(run_id: str, reviewed_stages: list[str]) -> int:
    """On approval of a gate: record the linked-domain contracts in the catalogue as proposed, with this run's domain
    as a consumer. The linked domain's team has not accepted them yet; integration decisions that use them are
    recorded as approved afterwards. Returns how many contracts were recorded (0 when the gate reviews none)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        artifact = _latest_artifact(s, run_id, reviewed_stages, LINKED_CONTRACTS_FILE)
        if artifact is None:
            return 0
        contracts = parse_linked_domain_contracts(artifact.content)
        base = PurePosixPath(artifact.path).parent
        for c in contracts:
            _upsert_contract(s, run.project, run_id, c, base, "proposed", [run.domain])
        return len(contracts)


def record_approved_design(run_id: str, reviewed_stages: list[str]) -> int:
    """On approval of a gate: record the run's integration decisions and contracts. Reads the database copy, which has
    reviewers' manual edits. Returns the number of decisions recorded (0 when the gate reviews none)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        project = run.project
        artifact = _latest_decisions(s, run_id, reviewed_stages)
        if artifact is None:
            return 0
        linked = _latest_artifact(s, run_id, reviewed_stages, LINKED_CONTRACTS_FILE)
        known = {c["key"] for c in parse_linked_domain_contracts(linked.content)} if linked else set()
        parsed = parse_decisions(artifact.content, known)
        base = PurePosixPath(artifact.path).parent

        for c in parsed["contracts"]:
            _upsert_contract(s, project, run_id, c, base, "approved", c.get("consumers") or [])

        s.query(Dependency).filter(Dependency.run_id == run_id).delete()
        for d in parsed["decisions"]:
            outbound = d.get("direction", "outbound") == "outbound"
            s.add(Dependency(
                project=project, run_id=run_id, interaction=str(d["interaction"]),
                source=run.domain if outbound else d["target"], target=d["target"] if outbound else run.domain,
                kind=d["kind"], pattern=d["pattern"], contract=d.get("contract"),
                status=d.get("status", "planned"), summary=d.get("summary", ""),
                code_refs=list(d.get("code_refs") or []),
            ))
        return len(parsed["decisions"])


def export_specs(workspace: Path) -> int:
    """Copy the catalogue's contract specs into the workspace so agents can read them. Returns how many changed."""
    with session_scope() as s:
        specs = [(c.key, c.spec) for c in s.scalars(select(Contract).where(Contract.project == projects.current()))
                 if c.spec]
    changed = 0
    for key, spec in specs:
        f = workspace / CATALOGUE_DIR / f"{key}.yaml"
        if not f.is_file() or f.read_text(encoding="utf-8") != spec:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(spec, encoding="utf-8")
            changed += 1
    return changed


def as_dict(c: Contract) -> dict[str, Any]:
    return {"key": c.key, "provider": c.provider, "kind": c.kind, "name": c.name, "version": c.version,
            "description": c.description, "spec": c.spec, "spec_path": c.spec_path, "consumers": c.consumers or [],
            "status": c.status, "run_id": c.run_id, "created_at": c.created_at, "updated_at": c.updated_at}


def list_contracts(domain: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(Contract).where(Contract.project == projects.current())
                         .order_by(Contract.provider, Contract.key)).all()
        return [as_dict(c) for c in rows if domain is None or c.provider == domain or domain in (c.consumers or [])]


def catalogue_brief(domain: str, settings: dict) -> str:
    """What every agent should know about cross-domain integration: ownership, conventions and contracts."""
    lines = ["## Cross-domain integration"]
    project = projects.current()
    with session_scope() as s:
        domains = s.scalars(select(Domain).where(Domain.project == project).order_by(Domain.key)).all()
        owned = [(d.key, d.owned_tables or [], d.owned_packages or []) for d in domains
                 if d.owned_tables or d.owned_packages]
        contracts = s.scalars(select(Contract).where(Contract.project == project)
                              .order_by(Contract.provider, Contract.key)).all()
        if owned:
            lines.append("Ownership (tables; code packages, namespaces or modules):")
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
    project = projects.current()
    with session_scope() as s:
        domains = [d.key for d in s.scalars(select(Domain).where(Domain.project == project)
                                            .order_by(Domain.created_at, Domain.key))]
        deps = s.scalars(select(Dependency).where(Dependency.project == project)).all()
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
