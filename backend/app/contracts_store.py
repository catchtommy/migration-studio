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
from sqlalchemy.exc import IntegrityError

from app import projects
from app.db.models import Artifact, Contract, Dependency, Domain, Run
from app.db.session import session_scope
from app.guardrail_problems import blocker

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
MICROSERVICE_EXTRACTION_FILE = "microservice-extraction.yaml"  # the microservice step's declaration
MODULE_EXTRACTION_FILE = "module-extraction.yaml"  # the Module in monolith step's declaration of what it changed
ORCHESTRATOR_EXTRACTION_FILE = "orchestrator-extraction.yaml"  # the Orchestrator in monolith step's declaration
ORCHESTRATOR_SERVICE_FILE = "orchestrator-service-extraction.yaml"  # the Orchestrator microservice step's declaration
INTEGRATION_ROUTING_FILE = "integration-routing.yaml"  # the Monolith integration step's declaration of its toggle
ROUTING_TARGETS = {"orchestrator-service": "orchestrator-openapi.yaml", "domain-service": "openapi.yaml"}
DOMAIN_OPENAPI_FILE = "openapi.yaml"  # the domain's public API, written by the design
ORCHESTRATOR_OPENAPI_FILE = "orchestrator-openapi.yaml"  # the orchestrator's API, written by the design when it has one
# Where each functionality of the feature goes, decided by the dependency step and approved (and amended) by a
# reviewer before the design: the design builds on it and never re-decides it.
FUNCTIONAL_ALLOCATION_FILE = "functional-allocation.yaml"
PLACEMENTS = ("domain", "domain-call", "orchestrator", "event-handler", "other-domain", "ui-unchanged")
EVENT_TRIGGERS = ("domain", "orchestrator")  # where an event-handler's event is published from
CALLERS = ("orchestrator", "domain-call")  # how this feature asks another domain (other-domain's `via`)
# Placements done by this feature's own code (the domain module or the orchestrator): their owner is this domain.
OWN_PLACEMENTS = ("domain", "domain-call", "orchestrator", "ui-unchanged")
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
        for field in ("requirements", "scenarios"):  # ids of the functional specification and the test scenarios
            value = item.get(field)
            if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
                raise InvalidDocument(f"{where}: {field} must be a list of ids")
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
    internal = data.get("considered_internal")
    if internal is not None and (not isinstance(internal, list) or not all(
            isinstance(i, dict) and i.get("requirement") and i.get("reason") for i in internal)):
        raise InvalidDocument(f"{DOMAIN_DEPENDENCIES_FILE}: considered_internal must be a list of entries with a "
                              "requirement (an id of functional-spec.md) and the reason")
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


def parse_functional_allocation(text: str) -> dict:
    """Where each functionality of one feature goes: the domain itself, a direct call from the domain to another
    domain's API, the orchestrator, an event handler (with the event, where it is published from and what the handler
    does), another domain (asked through the orchestrator or a direct call), or the unchanged UI."""
    data = _load(text, FUNCTIONAL_ALLOCATION_FILE)
    where = FUNCTIONAL_ALLOCATION_FILE
    _require(data, ("feature", "domain"), where)
    items = data.get("functionalities")
    if not isinstance(items, list) or not items:
        raise InvalidDocument(f"{where}: 'functionalities' must list every functionality of the feature")
    seen: set[str] = set()
    for i, item in enumerate(items, 1):
        at = f"{where} functionality {i}"
        if not isinstance(item, dict):
            raise InvalidDocument(f"{at} must be a mapping")
        _require(item, ("id", "name", "placement", "reason"), at)
        item["id"] = str(item["id"])
        at = f"{where} functionality {item['id']}"
        if item["id"] in seen:
            raise InvalidDocument(f"{at}: duplicate id")
        seen.add(item["id"])
        placement = item["placement"]
        _one_of(placement, PLACEMENTS, at)
        for field in ("requirements", "depends_on", "code_refs", "calls", "interactions"):
            value = item.get(field)
            if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
                raise InvalidDocument(f"{at}: {field} must be a list")
        if placement in OWN_PLACEMENTS and item.get("owner") not in (None, "", data["domain"]):
            raise InvalidDocument(f"{at}: a {placement} functionality is this domain's own ({data['domain']}); owner "
                                  f"{item['owner']} belongs to an event-handler or other-domain functionality")
        if placement in ("event-handler", "other-domain"):
            _require(item, ("owner",), at)
            if item["owner"] == data["domain"] and placement == "other-domain":
                raise InvalidDocument(f"{at}: other-domain names the OTHER domain that decides it as owner")
        if placement == "domain-call" and not item.get("calls"):
            raise InvalidDocument(f"{at}: a domain-call names the domains the domain module calls directly (calls)")
        if item.get("calls") and placement not in CALLERS:
            raise InvalidDocument(f"{at}: only a domain-call or orchestrator functionality calls other domains; a "
                                  f"{placement} one involves none (or place it where the call is made)")
        if placement == "other-domain":
            _require(item, ("via",), at)
            _one_of(item["via"], CALLERS, f"{at} via")
        elif item.get("via"):
            raise InvalidDocument(f"{at}: only an other-domain functionality has via")
        if data["domain"] in (item.get("calls") or []):
            raise InvalidDocument(f"{at}: calls lists other domains only, not {data['domain']} itself")
        event = item.get("event")
        if placement == "event-handler":
            if not isinstance(event, dict):
                raise InvalidDocument(f"{at}: an event-handler needs its event: name, triggered_from (domain or "
                                      "orchestrator), publisher, when and handler (what the handler does)")
            _require(event, ("name", "triggered_from", "publisher", "handler"), f"{at} event")
            _one_of(event["triggered_from"], EVENT_TRIGGERS, f"{at} event triggered_from")
            if event.get("delegates_to") is not None and not (
                    isinstance(event["delegates_to"], list) and all(isinstance(d, str) for d in event["delegates_to"])):
                raise InvalidDocument(f"{at} event: delegates_to must be a list of Class#method")
            if event.get("no_temporary_handler") is not None:
                if not str(event["no_temporary_handler"]).strip():
                    raise InvalidDocument(f"{at} event: no_temporary_handler gives the reason (or remove it)")
                if event.get("delegates_to"):
                    raise InvalidDocument(f"{at} event: either delegates_to (a temporary handler calls them) or "
                                          "no_temporary_handler (why there is none), not both")
        elif event:
            raise InvalidDocument(f"{at}: only an event-handler has an event; remove it, or place the functionality "
                                  "on event-handler")
        review = item.get("review")
        if review is not None and not isinstance(review, dict):
            raise InvalidDocument(f"{at}: review must be a mapping")
    for item in items:
        unknown = [d for d in item.get("depends_on") or [] if d not in seen]
        if unknown:
            raise InvalidDocument(f"{where} functionality {item['id']}: depends_on names {', '.join(unknown)}, which "
                                  "is not a functionality here")
    return {"feature": data["feature"], "domain": data["domain"], "functionalities": items}


def allocation_owner(item: dict) -> str:
    """The owner the design's "Functional allocation" table gives the ids of one functionality: the side that does
    it (a direct call is made by the domain module), or for an event handler the side that triggers it (what this
    feature must keep is that it publishes)."""
    placement = item["placement"]
    if placement == "event-handler":
        return item["event"]["triggered_from"]
    return {"domain-call": "domain", "other-domain": "other domain", "ui-unchanged": "ui unchanged"}.get(placement,
                                                                                                         placement)


def load_functional_allocation(output_dir: Path) -> dict | None:
    """The run's functional allocation, or None when it has none or it is invalid (reported for that file)."""
    f = output_dir / FUNCTIONAL_ALLOCATION_FILE
    try:
        return parse_functional_allocation(f.read_text(encoding="utf-8")) if f.is_file() else None
    except InvalidDocument:
        return None


def has_orchestrator(allocation: dict) -> bool:
    """Whether the allocation needs an orchestrator: a functionality placed on it, another domain asked through it,
    or an event published from it."""
    return any(i["placement"] == "orchestrator"
               or (i["placement"] == "other-domain" and i["via"] == "orchestrator")
               or (i["placement"] == "event-handler" and i["event"]["triggered_from"] == "orchestrator")
               for i in allocation["functionalities"])


def direct_calls(domain: str) -> list[str] | None:
    """The other domains this domain may call directly (its Direct calls setting); None when the domain isn't known
    in this project (nothing to check against)."""
    try:
        with session_scope() as s:
            row = s.get(Domain, (projects.current(), domain))
            return None if row is None else list(row.direct_calls or [])
    except Exception:  # noqa: BLE001 - without the database there is no setting to check against
        return None


def _direct_targets(item: dict) -> list[str]:
    """The domains a functionality calls directly from the domain module."""
    if item["placement"] == "domain-call":
        return list(item.get("calls") or [])
    if item["placement"] == "other-domain" and item["via"] == "domain-call":
        return [item["owner"]]
    return []


def _functional_allocation_problems(output_dir: Path, allocation: dict) -> list[str]:
    """The allocation against the documents next to it and the domain's setting: domains it names are this one or
    ones the feature is related to (domain-dependencies.yaml), and every outbound dependency is allocated; a direct
    call only to a domain the Direct calls setting allows; no orchestrator for a domain-only feature; interactions
    that exist; and, when the run has a functional specification, every requirement and rule allocated."""
    from app import functional_spec as fs

    where, problems = FUNCTIONAL_ALLOCATION_FILE, []
    items, me = allocation["functionalities"], allocation["domain"]
    deps_file = output_dir / DOMAIN_DEPENDENCIES_FILE
    deps = None
    if deps_file.is_file():
        try:
            deps = load_domain_dependencies(deps_file.read_text(encoding="utf-8"))
        except InvalidDocument:
            deps = None  # reported for that file itself
    if deps is not None:
        known = {me, *(d["domain"] for d in deps["dependencies"])}
        for i in items:
            named = [("owner", i.get("owner")), *(("calls", c) for c in i.get("calls") or [])]
            if i["placement"] == "event-handler":
                named.append(("event publisher", i["event"]["publisher"]))
            for label, d in named:
                if d and d not in known:
                    problems.append(f"{where} functionality {i['id']}: {label} {d} is neither this domain nor a "
                                    f"domain {DOMAIN_DEPENDENCIES_FILE} lists; add the dependency or correct it")
        covered = {d for i in items for d in [*(i.get("calls") or []), i.get("owner"),
                                              (i.get("event") or {}).get("publisher")] if d}
        outbound = sorted({d["domain"] for d in deps["dependencies"] if d.get("direction", "outbound") == "outbound"})
        missing = [d for d in outbound if d not in covered]
        if missing:
            problems.append(f"{where}: no functionality covers the outbound dependency on {', '.join(missing)}: name "
                            "it in the calls of the functionality that asks it, or as the owner of the event handler "
                            "or other-domain functionality it does")
        if deps["decision"]["scope"] == "domain-only" and has_orchestrator(allocation):
            problems.append(f"{where}: the feature is domain-only (no orchestrator is built), but functionalities "
                            "are placed on the orchestrator or use it; give them to the domain")
        calling = [i["id"] for i in items if i.get("calls") or i["placement"] == "other-domain"]
        if deps["decision"]["scope"] == "domain-only" and calling:
            problems.append(f"{where}: the feature is domain-only, but {', '.join(calling)} ask other domains; "
                            f"correct them, or the scope decision in {DOMAIN_DEPENDENCIES_FILE}")
    allowed = direct_calls(me)
    if allowed is not None:
        for i in items:
            barred = [d for d in _direct_targets(i) if d not in allowed]
            if barred:
                problems.append(f"{where} functionality {i['id']}: {me} may not call {', '.join(barred)} directly "
                                f"(the domain's Direct calls setting allows {', '.join(allowed) or 'none'}): go "
                                "through the orchestrator, or ask the user to allow the direct call on the domain")
    # At `monolith` the event is published in-process only (Service Bus comes with the Monolith integration step), so
    # whatever domain handles it, only a temporary handler does the work there: it calls the handling domain's
    # existing service through its bean, which that domain's own router sends on once it is migrated.
    unhandled = [i["id"] for i in items if i["placement"] == "event-handler" and i.get("owner") != me
                 and not i["event"].get("delegates_to") and not i["event"].get("no_temporary_handler")]
    if unhandled:
        problems.append(f"{where}: {', '.join(unhandled)} name no delegates_to: at monolith the event is published "
                        "in-process only, so without a temporary handler nothing does the work (even when the handling "
                        "domain is migrated, its service listens on Service Bus). Name the handling domain's existing "
                        "service method(s) that do it today, or, only when the user agrees, no_temporary_handler with "
                        "the reason")
    published = [i["id"] for i in items if i["placement"] == "event-handler"
                 and i["event"]["triggered_from"] == "orchestrator"]
    if published and not any(i["placement"] == "orchestrator" for i in items):
        problems.append(f"{where}: {', '.join(published)} publish their event from the orchestrator, but no "
                        "functionality is placed on it; publish from the domain, or place the operation whose "
                        "completion triggers it on the orchestrator")
    cross = output_dir / CROSS_DOMAIN_FILE
    if cross.is_file():
        try:
            interactions = parse_cross_domain(cross.read_text(encoding="utf-8"))
        except InvalidDocument:
            interactions = None
        if interactions is not None and (deps is None or deps["decision"]["scope"] == "cross-domain"):
            # (a domain-only feature has no outbound interaction to link: one listed is the dependency step's own
            # inconsistency, reported by the scope decision)
            ids = {str(x["id"]) for x in interactions}
            for i in items:
                unknown = [x for x in i.get("interactions") or [] if x not in ids]
                if unknown:
                    problems.append(f"{where} functionality {i['id']}: interactions {', '.join(unknown)} are not in "
                                    f"{CROSS_DOMAIN_FILE}")
                # The design decides each linked interaction's pattern from the placement: a placement that involves
                # another domain names the interactions it covers.
                if i["placement"] not in ("domain", "ui-unchanged") and not i.get("interactions"):
                    problems.append(f"{where} functionality {i['id']} ({i['placement']}): name the interactions of "
                                    f"{CROSS_DOMAIN_FILE} it covers (interactions)")
                elif i["placement"] in ("domain", "ui-unchanged"):
                    outbound = [x for x in i.get("interactions") or [] if any(
                        str(c["id"]) == x and c.get("direction", "outbound") == "outbound" for c in interactions)]
                    if outbound:
                        problems.append(f"{where} functionality {i['id']}: a {i['placement']} functionality involves "
                                        f"no other domain, but links the outbound interaction(s) {', '.join(outbound)}")
            linked = {x for i in items for x in i.get("interactions") or []}
            unlinked = sorted(str(x["id"]) for x in interactions
                              if x.get("direction", "outbound") == "outbound" and str(x["id"]) not in linked)
            if unlinked:
                problems.append(f"{where}: the outbound interaction(s) {', '.join(unlinked)} of {CROSS_DOMAIN_FILE} "
                                "are linked by no functionality: add each to the interactions of the functionality "
                                "that covers it")
    spec = output_dir / fs.SPEC_FILE
    if spec.is_file():
        defined = fs.spec_ids(spec.read_text(encoding="utf-8"))
        wanted = [i for k in ("FR", "BR") for i in defined[k]]
        given = {r for i in items for r in i.get("requirements") or []}
        missing = [i for i in wanted if i not in given]
        if missing:
            problems.append(f"{where}: {', '.join(missing[:30])} of {fs.SPEC_FILE} not allocated: add each to the "
                            "requirements of the functionality that implements it")
        everything = {i for ids in defined.values() for i in ids}
        unknown = sorted(r for r in given if r not in everything)
        if unknown:
            problems.append(f"{where}: requirements {', '.join(unknown[:20])} are not defined in {fs.SPEC_FILE}")
    return problems


# The integration patterns each placement allows for the interactions its functionality links (Lean Modulith design).
SYNC_PATTERNS = ("sync-api", "read-model", "reference-copy", "deferred")
PATTERNS_BY_PLACEMENT = {"event-handler": ("event",), "orchestrator": (*SYNC_PATTERNS, "saga"),
                         "domain-call": SYNC_PATTERNS}


def _allowed_patterns(item: dict) -> tuple[str, ...]:
    placement = item["placement"]
    if placement == "other-domain":
        placement = item["via"]
    return PATTERNS_BY_PLACEMENT.get(placement, ())


def allocation_decision_problems(output_dir: Path) -> list[str]:
    """The Lean design's integration decisions against the approved functional allocation: every interaction a
    functionality links has a decision whose pattern its placement allows and whose target it calls or is owned by; a
    domain or UI functionality links no outbound interaction; an outbound event decision comes from an event-handler
    functionality. Empty when either file is absent or invalid (reported for that file)."""
    allocation = load_functional_allocation(output_dir)
    f = output_dir / DECISIONS_FILE
    if allocation is None or not f.is_file():
        return []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    decisions = [d for d in (data or {}).get("decisions") or [] if isinstance(d, dict)] if isinstance(data, dict) else []
    directions: dict[str, str] = {}
    cross = output_dir / CROSS_DOMAIN_FILE
    if cross.is_file():
        try:
            directions = {str(x["id"]): x.get("direction", "outbound")
                          for x in parse_cross_domain(cross.read_text(encoding="utf-8"))}
        except InvalidDocument:
            pass
    by_interaction: dict[str, list[dict]] = {}
    for d in decisions:
        by_interaction.setdefault(str(d.get("interaction")), []).append(d)
    by_key = _linked_by_key(output_dir)
    where, problems = f"{DECISIONS_FILE} against {FUNCTIONAL_ALLOCATION_FILE}", []
    from_handlers: set[str] = set()
    for item in allocation["functionalities"]:
        linked = [str(x) for x in item.get("interactions") or []]
        fid, placement = item["id"], item["placement"]
        if placement in ("domain", "ui-unchanged"):
            outbound = [x for x in linked if directions.get(x, "outbound") == "outbound"]
            if outbound:
                problems.append(f"{where}: {fid} is placed on {placement}, which involves no other domain, but links "
                                f"the outbound interaction(s) {', '.join(outbound)}: the allocation and the "
                                "interactions disagree; ask the user")
            continue
        if placement == "event-handler":
            from_handlers.update(linked)
        allowed = _allowed_patterns(item)
        targets = {*(item.get("calls") or []), *([item["owner"]] if item.get("owner") else [])}
        for x in linked:
            found = by_interaction.get(x)
            if not found:
                problems.append(f"{where}: {fid} ({placement}) links {x}, which has no decision")
                continue
            for d in found:
                if d.get("pattern") not in allowed:
                    problems.append(f"{where}: {x} is decided as {d.get('pattern')}, but {fid} is placed on "
                                    f"{placement}{' via ' + item['via'] if placement == 'other-domain' else ''}, which "
                                    f"allows {', '.join(allowed)}; follow the approved allocation")
                if targets and d.get("target") not in targets:
                    problems.append(f"{where}: {x} targets {d.get('target')}, but {fid} calls or is owned by "
                                    f"{', '.join(sorted(targets))}")
                if placement == "event-handler" and d.get("pattern") == "event":
                    # The handling domain is still in the monolith exactly when the allocation names the methods
                    # that do the work today: then the event-handler module gets a handler, which reaches them through
                    # integration entry points (app/legacy_modules.py).
                    want = sorted(item["event"].get("delegates_to") or [])
                    handler = d.get("event_handler") if isinstance(d.get("event_handler"), dict) else None
                    if d.get("temporary_handler") is not None:
                        problems.append(f"{where}: {x} has a temporary_handler: the handler of a domain not migrated "
                                        "yet is now an event_handler in the event-handler module, calling integration "
                                        "entry points (calls); replace it")
                    elif want and handler is None:
                        problems.append(f"{where}: {x} needs an event_handler: {fid}'s event names delegates_to "
                                        f"({', '.join(want)}), so its handling domain is still in the monolith")
                    elif handler is not None and not want:
                        problems.append(f"{where}: {x} has an event_handler, but {fid}'s event names no delegates_to "
                                        "(its handling domain handles the event itself)")
                    elif handler is not None:
                        problems += _event_handler_problems(f"{where}: {x}'s event_handler", handler, item, want,
                                                            by_key)
    stray = sorted({str(d.get("interaction")) for d in decisions if d.get("pattern") == "event"
                    and d.get("direction", "outbound") == "outbound" and str(d.get("interaction")) not in from_handlers})
    if stray:
        problems.append(f"{where}: {', '.join(stray)} decided as events, but no event-handler functionality links them; "
                        "a side effect is an event only when the allocation places it on an event handler")
    return problems


def _linked_by_key(output_dir: Path) -> dict[str, dict] | None:
    """The run's linked-domain contracts by key; None when the file is absent or invalid (reported for that file)."""
    f = output_dir / LINKED_CONTRACTS_FILE
    if not f.is_file():
        return None
    try:
        return {c["key"]: c for c in parse_linked_domain_contracts(f.read_text(encoding="utf-8"))}
    except InvalidDocument:
        return None


def _event_handler_problems(where: str, handler: dict, item: dict, want: list[str],
                            linked: dict[str, dict] | None) -> list[str]:
    """An event handler of a domain still in the monolith: in that domain's package, calling only integration entry
    points of linked-domain contracts, which together reach every method the allocation says does the work today."""
    problems = []
    if str(handler.get("domain")) != str(item.get("owner")):
        problems.append(f"{where} is in {handler.get('domain')}'s package, but {item['id']} is done by "
                        f"{item.get('owner')}")
    if linked is None:
        return problems
    reached: set[str] = set()
    for key in handler.get("calls") or []:
        c = linked.get(key)
        if c is None:
            problems.append(f"{where} calls {key}, which is not a contract in {LINKED_CONTRACTS_FILE}: define it there "
                            "with its binding and entry point (or list it with reuse: true when the catalogue has it)")
            continue
        binding = _catalogue_binding(key) if c.get("reuse") else c.get("binding")
        if not isinstance(binding, dict) or binding.get("entry_point") is None:
            problems.append(f"{where} calls {key}, which has no entry point"
                            + (" in the catalogue: define the contract here with its binding and entry_point"
                               if c.get("reuse") else ": give its binding an entry_point")
                            + ", so the handler reaches it through the integration module")
            continue
        reached |= set(binding.get("delegates_to") or [])
    missing = [m for m in want if m not in reached]
    if missing:
        problems.append(f"{where}: no entry point it calls reaches {', '.join(missing)}, which {item['id']}'s event "
                        "says does the work: add the contract (with its entry point) of each domain it works in")
    return problems


def approved_owners(output_dir: Path) -> dict[str, set[str]]:
    """Requirement id -> the owners the approved functional allocation allows for it in the design's table."""
    allocation = load_functional_allocation(output_dir)
    out: dict[str, set[str]] = {}
    for item in (allocation or {}).get("functionalities") or []:
        for r in item.get("requirements") or []:
            owner = allocation_owner(item)
            if item["placement"] == "other-domain" and r.startswith("MSG-"):
                # Another domain decides the rule; the message this feature shows for its answer is mapped by the
                # side that asks it (via), and tested there.
                owner = "orchestrator" if item["via"] == "orchestrator" else "domain"
            out.setdefault(r, set()).add(owner)
    return out


def orchestrator_api_problems(output_dir: Path) -> list[str]:
    """The design's two APIs: when the design has an orchestrator, its own `orchestrator-openapi.yaml`, under
    /internal/api/v1/<domain>/orchestrator/; the domain's `openapi.yaml` never holds orchestrator paths."""
    f = output_dir / DOMAIN_DESIGN_FILE
    try:
        design = parse_domain_design(f.read_text(encoding="utf-8")) if f.is_file() else None
    except InvalidDocument:
        return []  # reported for that file itself
    if design is None:
        return []
    prefix = f"/internal/api/v1/{design['domain']}/orchestrator"
    problems = []
    domain_api = _openapi_paths(output_dir / DOMAIN_OPENAPI_FILE)
    mixed = [p for p in domain_api or [] if p.startswith(prefix)]
    if mixed:
        problems.append(f"{DOMAIN_OPENAPI_FILE}: {', '.join(mixed[:5])} are orchestrator operations; they belong in "
                        f"{ORCHESTRATOR_OPENAPI_FILE}, the domain's API holds only the domain's own")
    if not isinstance(design.get("orchestrator_module"), dict):
        return problems
    orch = output_dir / ORCHESTRATOR_OPENAPI_FILE
    paths = _openapi_paths(orch)
    if paths is None:
        problems.append(f"{ORCHESTRATOR_OPENAPI_FILE}: the design has an orchestrator, so write its API as an OpenAPI "
                        f"3.1 document next to {DOMAIN_OPENAPI_FILE}: one operation per orchestrator operation, under "
                        f"{prefix}/")
    elif not paths:
        problems.append(f"{ORCHESTRATOR_OPENAPI_FILE}: no paths; give one operation per orchestrator operation")
    else:
        outside = [p for p in paths if not p.startswith(prefix + "/")]
        if outside:
            problems.append(f"{ORCHESTRATOR_OPENAPI_FILE}: {', '.join(outside[:5])} must be under {prefix}/")
    return problems


def _openapi_paths(f: Path) -> list[str] | None:
    """An OpenAPI document's paths; None when the file is absent or isn't an OpenAPI document."""
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or "openapi" not in data:
        return None
    return [str(p) for p in (data.get("paths") or {})] if isinstance(data.get("paths") or {}, dict) else []


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
        if not all(isinstance(m, str) and DELEGATE.match(m) for m in binding["delegates_to"]):
            raise InvalidDocument(f"{where} binding: delegates_to lists the existing package.Class#method(s)")
        if binding.get("entry_point") is not None:
            _entry_point(binding, c, f"{where} binding entry_point")
    return items


DELEGATE = re.compile(r"^[\w.$]+#\w+$")


def _entry_point(binding: dict, contract: dict, where: str) -> None:
    """The integration module's entry point of a contract (app/legacy_modules.py): the operations of the contract's
    interface, each with the delegates_to it reaches through the gateway (a subset of the binding's)."""
    from app.legacy_modules import entry_point_names

    ep = binding["entry_point"]
    if contract["kind"] == "event":
        raise InvalidDocument(f"{where}: only an api or command contract has an entry point; an event reaches its "
                              "handlers through the event-handler module (event_handler in integration-decisions.yaml)")
    if not isinstance(ep, dict) or not isinstance(ep.get("operations"), list) or not ep["operations"]:
        raise InvalidDocument(f"{where}: list the operations of the entry point's interface (operations)")
    names = entry_point_names(contract["key"])
    if ep.get("api") is not None and str(ep["api"]) != names["api"]:
        raise InvalidDocument(f"{where}: the interface of {contract['key']} is {names['api']} (named from the key, so "
                              f"every feature builds the same one), not {ep['api']}")
    allowed = set(binding["delegates_to"])
    seen = set()
    for i, op in enumerate(ep["operations"], 1):
        at = f"{where} operation {i}"
        if not isinstance(op, dict):
            raise InvalidDocument(f"{at} must be a mapping")
        _require(op, ("method", "delegates_to"), at)
        if not re.match(r"^[a-z_]\w*$", str(op["method"])):
            raise InvalidDocument(f"{at}: method is the Java method name of the operation (e.g. getKycStatus)")
        if op["method"] in seen:
            raise InvalidDocument(f"{at}: duplicate method {op['method']}")
        seen.add(op["method"])
        if not isinstance(op["delegates_to"], list) or not op["delegates_to"]:
            raise InvalidDocument(f"{at}: delegates_to lists the binding's Class#method(s) this operation reaches")
        extra = [m for m in op["delegates_to"] if m not in allowed]
        if extra:
            raise InvalidDocument(f"{at}: {', '.join(map(str, extra))} is not in the binding's delegates_to")
    unused = sorted(allowed - {m for op in ep["operations"] for m in op["delegates_to"]})
    if unused:
        raise InvalidDocument(f"{where}: no operation reaches {', '.join(unused)} of the binding's delegates_to")


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
    lists = ("owned_tables", "read_only_tables", "utility_modules", "other_domain_dependencies", "context_ports",
             *DOMAIN_DESIGN_LOCKED)
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
        _one_of(m["kind"], ("domain", "orchestrator"), f"{where} module {i}")  # orchestrator: refused below, clearly
        if str(m["path"]).startswith("/") or ".." in Path(str(m["path"])).parts:
            raise InvalidDocument(f"{where} module {i}: path must be inside the repository")
        if not COORDINATES.match(str(m["artifact"])):
            raise InvalidDocument(f"{where} module {i}: artifact must be Maven coordinates, groupId:artifactId")
    kinds = [m["kind"] for m in modules]
    if "orchestrator" in kinds:
        raise InvalidDocument(f"{where}: list the domain module only. The orchestrator module, when the feature needs "
                              "one, is built by the Orchestrator in monolith step and declared in "
                              f"{ORCHESTRATOR_EXTRACTION_FILE}")
    if kinds.count("domain") != 1:
        raise InvalidDocument(f"{where}: exactly one domain module")
    if not data.get("bridge_dir"):
        raise InvalidDocument(f"{where}: add the bridge_dir: the new package in the monolith with the router (and the "
                              "adapters: context ports, and the local adapters of a cross-domain feature)")
    for field in ("outside_changes", "events", "added", "reused"):  # added/reused: classes this feature added to the
        if data.get(field) is not None and not isinstance(data[field], list):  # domain's module, or reused from it
            raise InvalidDocument(f"{where}: {field} must be a list")
    _feature_packages(data, where)
    data["temporary_handlers"] = _temporary_handlers(data.get("temporary_handlers"), where)
    data["legacy_modules"] = _legacy_modules(data.get("legacy_modules"), where, "domain")
    data["toggle_branches"] = _toggle_branches(data.get("toggle_branches"), where)
    data["routing"] = _routing_section(data.get("routing"), where, bool(data["toggle_branches"]))
    bridge = str(data["bridge_dir"]).strip("/")
    if not str(data["routing"]["router"]).strip("/").startswith(bridge + "/"):
        raise InvalidDocument(f"{where} routing: the router {data['routing']['router']} belongs in the bridge package "
                              f"{bridge}")
    for i, c in enumerate(data.get("shared_copies") or [], 1):
        if not isinstance(c, dict):
            raise InvalidDocument(f"{where} shared copy {i} must be a mapping")
        _require(c, ("item", "remove_when"), f"{where} shared copy {i}")
    return data


def parse_microservice_extraction(text: str) -> dict:
    """What the microservice step declares: the monolith commit it copied from, the operations it exposes, what it
    replaced instead of copying, the events it publishes and the open decisions it found."""
    data = _load(text, MICROSERVICE_EXTRACTION_FILE)
    where = MICROSERVICE_EXTRACTION_FILE
    _require(data, ("feature", "domain", "module_commit"), where)
    _feature_packages(data, where)
    ops = data.get("operations")
    if not isinstance(ops, list) or not ops:
        raise InvalidDocument(f"{where}: list the operations the service exposes (method and operation_id)")
    seen = set()
    for i, o in enumerate(ops, 1):
        if not isinstance(o, dict):
            raise InvalidDocument(f"{where} operation {i} must be a mapping")
        _require(o, ("method", "operation_id"), f"{where} operation {i}")
        if not re.match(r"^\w+#\w+$", str(o["method"])):
            raise InvalidDocument(f"{where} operation {i}: method must look like BrokerService#create")
        if o["operation_id"] in seen:
            raise InvalidDocument(f"{where} operation {i}: duplicate operation_id {o['operation_id']}")
        seen.add(o["operation_id"])
    for field, needs in (("replaced", ("file", "reason")), ("open_decisions", ("topic", "detail"))):
        value = data.get(field)
        if value is None:
            raise InvalidDocument(f"{where}: missing {field} (write {field}: [] when there is none)")
        if not isinstance(value, list):
            raise InvalidDocument(f"{where}: {field} must be a list")
        for i, item in enumerate(value, 1):
            if not isinstance(item, dict):
                raise InvalidDocument(f"{where} {field} {i} must be a mapping")
            _require(item, needs, f"{where} {field} {i}")
    if data.get("events") is not None and not isinstance(data["events"], list):
        raise InvalidDocument(f"{where}: events must be a list")
    return data


def _toggle_branches(branches, where: str) -> list[dict]:
    """The legacy callers (controllers, jobs, services) that get a toggle branch asking the router, because no bean
    seam fits: each with the reason. `controller:` is the older name of `file:`."""
    if branches is None:
        return []
    if not isinstance(branches, list):
        raise InvalidDocument(f"{where}: toggle_branches must be a list")
    for i, b in enumerate(branches, 1):
        if not isinstance(b, dict):
            raise InvalidDocument(f"{where} toggle branch {i} must be a mapping")
        if "file" not in b and "controller" in b:
            b["file"] = b.pop("controller")
        _require(b, ("file", "reason"), f"{where} toggle branch {i}")
        path = str(b["file"])
        if path.startswith("/") or ".." in Path(path).parts or not path.endswith(".java"):
            raise InvalidDocument(f"{where} toggle branch {i}: file must be a Java file inside the repository")
        if not str(b["reason"]).strip():
            raise InvalidDocument(f"{where} toggle branch {i}: give the reason no bean seam fits")
    return branches


def _routing_section(routing, where: str, branches: bool = False) -> dict:
    """The parallel run of module-extraction.yaml: the toggle (default legacy), the router, the beans it takes over
    (none when every caller is a controller with a toggle branch)."""
    if not isinstance(routing, dict):
        raise InvalidDocument(f"{where}: add routing: the toggle property, its default (legacy), the router, and the "
                              "legacy beans it takes over (the as-is code stays the old flow)")
    _require(routing, ("property", "default", "router", "routes"), f"{where} routing")
    if str(routing["default"]) != "legacy":
        raise InvalidDocument(f"{where} routing: the toggle defaults to legacy (the as-is code), so deploying changes "
                              "nothing until someone switches")
    if not isinstance(routing["routes"], list) or not (routing["routes"] or branches):
        raise InvalidDocument(f"{where} routing: list each legacy bean the router takes over (or, where no bean seam "
                              "fits, the callers in toggle_branches)")
    for i, r in enumerate(routing["routes"], 1):
        if not isinstance(r, dict):
            raise InvalidDocument(f"{where} routing route {i} must be a mapping")
        _require(r, ("bean", "legacy_bean", "interface", "module"), f"{where} routing route {i}")
        if r["bean"] == r["legacy_bean"]:
            raise InvalidDocument(f"{where} routing route {i}: the legacy bean gets a new id; the router takes over "
                                  f"{r['bean']}")
    return routing


JAVA_PACKAGE = re.compile(r"^[a-z_][\w]*(?:\.[a-z_][\w]*)*$")


def _feature_packages(data: dict, where: str) -> None:
    """base_package (the module's package; each feature's is <base_package>.<feature>) and uses_features (the domain's
    other features whose packages this feature uses): app/domain_modules.py."""
    if data.get("base_package") is not None and not JAVA_PACKAGE.match(str(data["base_package"])):
        raise InvalidDocument(f"{where}: base_package must be a Java package, e.g. com.acme.broker.domain")
    if data.get("uses_features") is not None and not isinstance(data["uses_features"], list):
        raise InvalidDocument(f"{where}: uses_features must be a list of the domain's feature keys")


def parse_orchestrator_extraction(text: str) -> dict:
    """What the Orchestrator in monolith step declares it created and changed (or `orchestrator: none` and why)."""
    data = _load(text, ORCHESTRATOR_EXTRACTION_FILE)
    where = ORCHESTRATOR_EXTRACTION_FILE
    _require(data, ("feature", "domain", "orchestrator"), where)
    _one_of(data["orchestrator"], ("module", "none"), f"{where} orchestrator")
    if data["orchestrator"] == "none":
        if not str(data.get("reason") or "").strip():
            raise InvalidDocument(f"{where}: give the reason there is no orchestrator")
        if data.get("module") or data.get("operations") or data.get("ports") or data.get("toggle_branches"):
            raise InvalidDocument(f"{where}: orchestrator is none, so list no module, operations, ports or toggle "
                                  "branches")
        return data
    module = data.get("module")
    if not isinstance(module, dict):
        raise InvalidDocument(f"{where}: module must be a mapping with path and artifact")
    _feature_packages(data, where)
    _require(module, ("path", "artifact"), f"{where} module")
    if str(module["path"]).startswith("/") or ".." in Path(str(module["path"])).parts:
        raise InvalidDocument(f"{where} module: path must be inside the repository")
    if not COORDINATES.match(str(module["artifact"])):
        raise InvalidDocument(f"{where} module: artifact must be Maven coordinates, groupId:artifactId")
    for field, needs in (("operations", ("method", "replaces")), ("ports", ("port", "contract", "property"))):
        value = data.get(field)
        if field == "operations" and not value:
            raise InvalidDocument(f"{where}: list the operations the controllers now call")
        if value is not None and not isinstance(value, list):
            raise InvalidDocument(f"{where}: {field} must be a list")
        for i, item in enumerate(value or [], 1):
            if not isinstance(item, dict):
                raise InvalidDocument(f"{where} {field} {i} must be a mapping")
            _require(item, needs, f"{where} {field} {i}")
    for i, port in enumerate(data.get("ports") or [], 1):
        if str(port.get("default", "local")) != "local":
            raise InvalidDocument(f"{where} port {i}: the mode defaults to local, which keeps today's behaviour")
    if data.get("outside_changes") is not None and not isinstance(data["outside_changes"], list):
        raise InvalidDocument(f"{where}: outside_changes must be a list")
    data["temporary_handlers"] = _temporary_handlers(data.get("temporary_handlers"), where)
    data["legacy_modules"] = _legacy_modules(data.get("legacy_modules"), where, "orchestrator")
    data["toggle_branches"] = _toggle_branches(data.get("toggle_branches"), where)
    return data


def _temporary_handlers(value, where: str) -> list[dict]:
    """The temporary event handlers a build step declares it built in the bridge package: [{listener, delegates_to}]
    (the guardrails compare them with integration-decisions.yaml and with the code)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidDocument(f"{where}: temporary_handlers must be a list")
    seen = set()
    for i, h in enumerate(value, 1):
        at = f"{where} temporary handler {i}"
        if not isinstance(h, dict):
            raise InvalidDocument(f"{at} must be a mapping")
        _require(h, ("listener", "delegates_to"), at)
        if not re.match(r"^[A-Za-z_]\w*$", str(h["listener"])):
            raise InvalidDocument(f"{at}: listener is the helper's class name (no package)")
        if not isinstance(h["delegates_to"], list) or not h["delegates_to"] or not all(
                isinstance(m, str) and re.match(r"^[\w.]+#\w+$", m) for m in h["delegates_to"]):
            raise InvalidDocument(f"{at}: delegates_to lists the handling domain's existing package.Class#method(s)")
        if h["listener"] in seen:
            raise InvalidDocument(f"{at}: duplicate listener {h['listener']}")
        seen.add(h["listener"])
    return value


def expected_temporary_handlers(output_dir: Path, side: str) -> list[dict] | None:
    """The temporary handlers of integration-decisions.yaml that the step building `side` (domain: Module in monolith;
    orchestrator: Orchestrator in monolith) builds: those on events the functional allocation publishes from that side
    (`triggered_from`). Without an allocation (the standard workflow) every handler is the domain module's. None when
    the decisions are absent or unreadable (nothing to compare with)."""
    f = output_dir / DECISIONS_FILE
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    sides: dict[str, str] = {}
    for item in (load_functional_allocation(output_dir) or {}).get("functionalities") or []:
        if item["placement"] == "event-handler":
            for x in item.get("interactions") or []:
                sides[str(x)] = item["event"]["triggered_from"]
    out = []
    for d in data.get("decisions") or []:
        h = d.get("temporary_handler") if isinstance(d, dict) else None
        if d.get("pattern") != "event" or not isinstance(h, dict) or not h.get("listener"):
            continue
        if sides.get(str(d.get("interaction")), "domain") == side:
            out.append({"listener": str(h["listener"]), "delegates_to": [str(m) for m in h.get("delegates_to") or []],
                        "interaction": str(d.get("interaction"))})
    return out


def _legacy_modules(value, where: str, side: str) -> dict | None:
    """What a build step declares it built in the temporary modules (app/legacy_modules.py): the contract keys of the
    integration entry points (the Module in monolith step), the handlers of the events its side publishes, and where
    the legacy adapters are (adapter_dir, a new package of the monolith's web application)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidDocument(f"{where}: legacy_modules must be a mapping (adapter_dir, entry_points, handlers)")
    for field in ("entry_points", "handlers"):
        if value.get(field) is not None and not isinstance(value[field], list):
            raise InvalidDocument(f"{where}: legacy_modules.{field} must be a list")
    if side == "orchestrator" and value.get("entry_points"):
        raise InvalidDocument(f"{where}: the entry points are built by the Module in monolith step; list only the "
                              "handlers of the events the orchestrator publishes")
    if not all(isinstance(k, str) for k in value.get("entry_points") or []):
        raise InvalidDocument(f"{where}: legacy_modules.entry_points lists contract keys")
    for i, h in enumerate(value.get("handlers") or [], 1):
        if not isinstance(h, dict):
            raise InvalidDocument(f"{where} legacy_modules handler {i} must be a mapping")
        _require(h, ("event", "domain"), f"{where} legacy_modules handler {i}")
    adapter = value.get("adapter_dir")
    if adapter is not None and (str(adapter).startswith("/") or ".." in Path(str(adapter)).parts):
        raise InvalidDocument(f"{where}: legacy_modules.adapter_dir must be inside the repository")
    return value


def expected_entry_points(output_dir: Path) -> list[dict] | None:
    """The integration entry points of the run's design: one per linked-domain contract whose binding has an
    entry_point, [{key, provider, operations, delegates_to}]. All are built by the Module in monolith step (the
    orchestrator's ports and the handlers use them too). None when the design has neither entry points nor event
    handlers (an older design: the bridge's local adapters and temporary handlers instead)."""
    linked = _linked_by_key(output_dir)
    handlers = _decision_handlers(output_dir)
    out = []
    for c in (linked or {}).values():
        binding = c.get("binding")
        reused = bool(c.get("reuse"))
        if reused:  # an entry point an earlier feature designed: its binding is in the catalogue
            binding = _catalogue_binding(c["key"])
        if isinstance(binding, dict) and binding.get("entry_point"):
            out.append({"key": c["key"], "provider": c["provider"], "operations": binding["entry_point"]["operations"],
                        "delegates_to": list(binding.get("delegates_to") or []), "reused": reused})
    if not any(not e["reused"] for e in out) and not handlers:
        return None
    return out


def _catalogue_binding(key: str) -> dict | None:
    """The temporary binding the catalogue keeps for a contract (with its entry point), if any."""
    try:
        with session_scope() as s:
            row = s.get(Contract, (projects.current(), key))
            return dict(row.binding) if row is not None and isinstance(row.binding, dict) else None
    except Exception:  # noqa: BLE001 - without the database there is no catalogue to read
        return None


def _decision_handlers(output_dir: Path) -> list[dict]:
    f = output_dir / DECISIONS_FILE
    if not f.is_file():
        return []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    decisions = (data.get("decisions") or []) if isinstance(data, dict) else []
    return [d for d in decisions if isinstance(d, dict) and d.get("pattern") == "event"
            and isinstance(d.get("event_handler"), dict)]


def expected_event_handlers(output_dir: Path, side: str) -> list[dict] | None:
    """The event handlers of integration-decisions.yaml that the step building `side` builds (domain: Module in
    monolith; orchestrator: Orchestrator in monolith): those on events the functional allocation publishes from that
    side. [{event, domain, calls, delegates_to, interaction}]; None when the design has neither handlers nor entry
    points."""
    handlers = _decision_handlers(output_dir)
    linked = _linked_by_key(output_dir) or {}
    if not handlers and not any(isinstance(c.get("binding"), dict) and c["binding"].get("entry_point")
                                for c in linked.values()):
        return None
    sides: dict[str, str] = {}
    for item in (load_functional_allocation(output_dir) or {}).get("functionalities") or []:
        if item["placement"] == "event-handler":
            for x in item.get("interactions") or []:
                sides[str(x)] = item["event"]["triggered_from"]
    out = []
    for d in handlers:
        if sides.get(str(d.get("interaction")), "domain") != side:
            continue
        h = d["event_handler"]
        calls = [str(k) for k in h.get("calls") or []]
        out.append({"event": str(d.get("contract")), "domain": str(h.get("domain")), "calls": calls,
                    "delegates_to": [m for k in calls for m in ((linked.get(k) or {}).get("binding") or {})
                                     .get("delegates_to") or []],
                    "interaction": str(d.get("interaction"))})
    return out


def entry_point_problems(output_dir: Path) -> list[str]:
    """The Lean design gives every linked-domain contract it defines with a temporary binding (an api or command of a
    domain not migrated yet) an integration entry point; its operations, when the contract's spec is OpenAPI, are
    operations of that spec."""
    from app.service_guardrails import openapi_operation_ids

    linked = _linked_by_key(output_dir)
    problems = []
    for c in (linked or {}).values():
        binding = c.get("binding") if isinstance(c.get("binding"), dict) else None
        if c.get("reuse") or binding is None or c["kind"] == "event":
            continue
        ep = binding.get("entry_point")
        if ep is None:
            problems.append(f"{LINKED_CONTRACTS_FILE}: {c['key']} has a temporary binding but no entry_point: the "
                            "domain and the orchestrator reach a domain not migrated yet through its entry point in "
                            "the integration module")
            continue
        spec = output_dir / str(c.get("spec") or "")
        if c.get("spec") and spec.is_file() and "openapi" in spec.read_text(encoding="utf-8")[:400]:
            ids, _missing = openapi_operation_ids(spec.read_text(encoding="utf-8"))
            problems += [f"{LINKED_CONTRACTS_FILE}: {c['key']} entry point operation_id {op['operation_id']} is not "
                         f"an operation of {c['spec']}" for op in ep["operations"]
                         if op.get("operation_id") and str(op["operation_id"]) not in ids]
    return problems


def _orchestrator_extraction_problems(output_dir: Path, declaration: dict) -> list[str]:
    """Check the orchestrator against the documents next to it: only a cross-domain feature has one, it is a module
    of its own beside the domain module, and its ports call linked-domain contracts."""
    problems = []
    scope = None
    deps = output_dir / DOMAIN_DEPENDENCIES_FILE
    if deps.is_file():
        try:
            scope = load_domain_dependencies(deps.read_text(encoding="utf-8"))["decision"]["scope"]
        except InvalidDocument:
            pass
    if declaration["orchestrator"] == "none":
        return problems
    if scope == "domain-only":
        problems.append(f"{ORCHESTRATOR_EXTRACTION_FILE}: {DOMAIN_DEPENDENCIES_FILE} decides this feature is "
                        "domain-only, so it has no orchestrator: write orchestrator: none")
    module_file = output_dir / MODULE_EXTRACTION_FILE
    if not module_file.is_file():
        problems.append(blocker(f"{ORCHESTRATOR_EXTRACTION_FILE}: {MODULE_EXTRACTION_FILE} of the Module in monolith "
                                "step is missing; the orchestrator is built on the domain module it declares"))
    else:
        try:
            modules = parse_module_extraction(module_file.read_text(encoding="utf-8"))["modules"]
        except InvalidDocument:
            modules = []
        path = str(declaration["module"]["path"]).strip("/")
        for m in modules:
            other = str(m["path"]).strip("/")
            if path == other or path.startswith(other + "/") or other.startswith(path + "/"):
                problems.append(f"{ORCHESTRATOR_EXTRACTION_FILE}: the orchestrator module {path} overlaps the "
                                f"{m['kind']} module {other}; it is a module of its own")
    linked = output_dir / LINKED_CONTRACTS_FILE
    if linked.is_file():
        try:
            keys = {c["key"] for c in parse_linked_domain_contracts(linked.read_text(encoding="utf-8"))}
        except InvalidDocument:
            keys = None
        if keys is not None:
            problems += [f"{ORCHESTRATOR_EXTRACTION_FILE}: port {p['port']} calls {p['contract']}, which is not a "
                         f"contract in {LINKED_CONTRACTS_FILE}" for p in declaration.get("ports") or []
                         if p["contract"] not in keys]
    return problems


def parse_orchestrator_service(text: str) -> dict:
    """What the Orchestrator microservice step declares the service exposes and calls (or `orchestrator: none`)."""
    data = _load(text, ORCHESTRATOR_SERVICE_FILE)
    where = ORCHESTRATOR_SERVICE_FILE
    _require(data, ("feature", "domain", "orchestrator"), where)
    _one_of(data["orchestrator"], ("service", "none"), f"{where} orchestrator")
    if data["orchestrator"] == "none":
        if not str(data.get("reason") or "").strip():
            raise InvalidDocument(f"{where}: give the reason there is no orchestrator service")
        if data.get("operations") or data.get("domain_api"):
            raise InvalidDocument(f"{where}: orchestrator is none, so list no operations or domain_api")
        return data
    _require(data, ("module_commit", "openapi"), where)
    _feature_packages(data, where)
    for field, needs in (("operations", ("method", "operation_id")), ("domain_api", ("method", "operation_id")),
                         ("replaced", ("file", "reason"))):
        value = data.get(field)
        if field == "operations" and not value:
            raise InvalidDocument(f"{where}: list the operations the service exposes")
        if value is not None and not isinstance(value, list):
            raise InvalidDocument(f"{where}: {field} must be a list")
        for i, item in enumerate(value or [], 1):
            if not isinstance(item, dict):
                raise InvalidDocument(f"{where} {field} {i} must be a mapping")
            _require(item, needs, f"{where} {field} {i}")
    if data.get("contracts") is not None and not isinstance(data["contracts"], list):
        raise InvalidDocument(f"{where}: contracts must be a list of contract keys")
    return data


def _orchestrator_service_problems(output_dir: Path, declaration: dict) -> list[str]:
    """Check the service against the documents next to it: there is an orchestrator to lift, the service exposes
    every operation of the orchestrator module, and it calls only domain operations and linked-domain contracts."""
    if declaration["orchestrator"] == "none":
        return []
    from app.service_guardrails import openapi_operation_ids

    problems = []
    built = output_dir / ORCHESTRATOR_EXTRACTION_FILE
    module = None
    if not built.is_file():
        problems.append(blocker(f"{ORCHESTRATOR_SERVICE_FILE}: {ORCHESTRATOR_EXTRACTION_FILE} of the Orchestrator in "
                                "monolith step is missing; the service lifts the orchestrator module it declares"))
    else:
        try:
            module = parse_orchestrator_extraction(built.read_text(encoding="utf-8"))
        except InvalidDocument:
            module = None
        if module and module["orchestrator"] == "none":
            problems.append(f"{ORCHESTRATOR_SERVICE_FILE}: the Orchestrator in monolith step built no orchestrator "
                            f"({module['reason']}), so there is no orchestrator service: write orchestrator: none")
    if module and module["orchestrator"] == "module":
        exposed = {str(o["method"]) for o in declaration["operations"]}
        problems += [f"{ORCHESTRATOR_SERVICE_FILE}: {o['method']} of the orchestrator module is not an operation of the "
                     "service; the monolith calls every one through it" for o in module.get("operations") or []
                     if str(o["method"]) not in exposed]
    api = output_dir / str(declaration["openapi"])
    if not api.is_file():
        problems.append(f"{ORCHESTRATOR_SERVICE_FILE}: the service's API {declaration['openapi']} does not exist")
    domain_api = output_dir / "openapi.yaml"
    if domain_api.is_file() and declaration.get("domain_api"):
        ids, _missing = openapi_operation_ids(domain_api.read_text(encoding="utf-8"))
        problems += [f"{ORCHESTRATOR_SERVICE_FILE}: domain operation {o['operation_id']} ({o['method']}) is not in the "
                     "domain's openapi.yaml" for o in declaration["domain_api"] if str(o["operation_id"]) not in ids]
    linked = output_dir / LINKED_CONTRACTS_FILE
    if linked.is_file() and declaration.get("contracts"):
        try:
            keys = {c["key"] for c in parse_linked_domain_contracts(linked.read_text(encoding="utf-8"))}
        except InvalidDocument:
            keys = None
        if keys is not None:
            problems += [f"{ORCHESTRATOR_SERVICE_FILE}: contract {c} is not in {LINKED_CONTRACTS_FILE}"
                         for c in declaration["contracts"] if c not in keys]
    return problems


def parse_integration_routing(text: str) -> dict:
    """How the Monolith integration step routes the feature: the target service, its API, the toggle, the operations."""
    data = _load(text, INTEGRATION_ROUTING_FILE)
    where = INTEGRATION_ROUTING_FILE
    _require(data, ("feature", "domain", "target", "api", "toggle", "router", "operations"), where)
    _one_of(data["target"], tuple(ROUTING_TARGETS), f"{where} target")
    toggle = data["toggle"]
    if not isinstance(toggle, dict):
        raise InvalidDocument(f"{where}: toggle must be a mapping with property and default")
    _require(toggle, ("property", "default"), f"{where} toggle")
    if str(toggle["default"]) != "legacy":
        raise InvalidDocument(f"{where}: the toggle defaults to legacy (the as-is code), so deploying changes nothing "
                              "until someone switches")
    if not isinstance(data["operations"], list) or not data["operations"]:
        raise InvalidDocument(f"{where}: list every operation of the target's API")
    for i, op in enumerate(data["operations"], 1):
        if not isinstance(op, dict):
            raise InvalidDocument(f"{where} operation {i} must be a mapping")
        _require(op, ("operation_id", "monolith", "client"), f"{where} operation {i}")
    subs = data.get("temporary_subscribers")
    if subs is not None:
        if not isinstance(subs, list):
            raise InvalidDocument(f"{where}: temporary_subscribers must be a list")
        for i, sub in enumerate(subs, 1):
            if not isinstance(sub, dict):
                raise InvalidDocument(f"{where} temporary subscriber {i} must be a mapping")
            if sub.get("listener") is None and sub.get("handler") is not None:
                sub["listener"] = sub["handler"]  # the event-handler module's handler interface it calls
            _require(sub, ("listener", "event", "enabled_property"), f"{where} temporary subscriber {i}")
    return data


def routing_target(output_dir: Path) -> tuple[str | None, str | None]:
    """(the service the monolith's new flow must call, why not when it can't be decided) from the feature's documents:
    the orchestrator service when the feature has an orchestrator, else the domain service."""
    module = output_dir / ORCHESTRATOR_EXTRACTION_FILE
    service = output_dir / ORCHESTRATOR_SERVICE_FILE
    try:
        built = parse_orchestrator_extraction(module.read_text(encoding="utf-8")) if module.is_file() else None
        lifted = parse_orchestrator_service(service.read_text(encoding="utf-8")) if service.is_file() else None
    except InvalidDocument as e:
        return None, str(e)
    if built and built["orchestrator"] == "module":
        if not lifted or lifted["orchestrator"] != "service":
            return None, ("the feature has an orchestrator in the monolith but no orchestrator service: calling the "
                          "domain service directly would drop the orchestration, so the Orchestrator microservice "
                          "step comes first")
        return "orchestrator-service", None
    return "domain-service", None


def _integration_routing_problems(output_dir: Path, declaration: dict) -> list[str]:
    """The new flow calls the orchestrator service exactly when the feature has an orchestrator (else the domain
    service), with every operation of that service's API and no other."""
    from app.service_guardrails import openapi_operation_ids

    where = INTEGRATION_ROUTING_FILE
    target, why = routing_target(output_dir)
    if target is None:  # the earlier steps decide this, not the integration agent
        return [blocker(f"{where}: {why}")]
    problems = []
    module = output_dir / MODULE_EXTRACTION_FILE
    if module.is_file():  # one toggle per feature: the one the Module in monolith step built
        try:
            routing = parse_module_extraction(module.read_text(encoding="utf-8"))["routing"]
        except InvalidDocument:
            routing = None
        if routing and (declaration["toggle"]["property"] != routing["property"] or
                        str(declaration["router"]).strip("/") != str(routing["router"]).strip("/")):
            problems.append(f"{where}: use the feature's toggle {routing['property']} and its router "
                            f"{routing['router']} (routing in {MODULE_EXTRACTION_FILE}); add the service value there")
    if declaration["target"] != target:
        problems.append(f"{where}: target is {declaration['target']}, but the monolith must call the {target} "
                        + ("(the feature has an orchestrator: the monolith reaches the domain only through it)"
                           if target == "orchestrator-service" else "(the feature has no orchestrator)"))
        return problems
    api = ROUTING_TARGETS[target]
    if declaration["api"] != api:
        problems.append(f"{where}: api is {declaration['api']}, but the {target}'s API is {api}")
    spec = output_dir / api
    if not spec.is_file():
        return [*problems, f"{where}: {api}, the {target}'s API, does not exist"]
    ids, _missing = openapi_operation_ids(spec.read_text(encoding="utf-8"))
    ids = {i for i in ids if not i.lower().startswith("health")}
    declared = {str(op["operation_id"]) for op in declaration["operations"]}
    problems += [f"{where}: operation {i} of {api} is not routed" for i in sorted(ids - declared)]
    problems += [f"{where}: operation {i} is not in {api}" for i in sorted(declared - ids)]
    return problems + _temporary_subscriber_problems(output_dir, declaration)


def expected_temporary_subscribers(output_dir: Path) -> list[dict] | None:
    """The temporary handlers of integration-decisions.yaml: once the feature runs in its service, the monolith takes
    each one's event from Service Bus and calls it (the Monolith integration step). None when the decisions are
    missing or unreadable."""
    f = output_dir / DECISIONS_FILE
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    decisions = (data.get("decisions") or []) if isinstance(data, dict) else []
    out = []
    for d in decisions:
        h = d.get("temporary_handler") if isinstance(d, dict) else None
        if isinstance(h, dict) and d.get("pattern") == "event" and h.get("listener"):
            out.append({"listener": str(h["listener"]), "event": str(d.get("contract") or ""),
                        "owner": str(d.get("target"))})
        h = d.get("event_handler") if isinstance(d, dict) else None
        if isinstance(h, dict) and d.get("pattern") == "event" and d.get("contract"):
            from app.legacy_modules import handler_names

            out.append({"listener": handler_names(str(d["contract"]), str(h.get("domain")))["interface"],
                        "event": str(d["contract"]),
                        "owner": str(h.get("domain"))})
    return out


def _temporary_subscriber_problems(output_dir: Path, declaration: dict) -> list[str]:
    """Every temporary handler gets a monolith subscriber (with an enabled property, switched off when the handling
    domain's own subscription goes live: the handling domain's agents are told, catalogue_brief), and no other."""
    expected = expected_temporary_subscribers(output_dir)
    if expected is None:
        return []
    where = INTEGRATION_ROUTING_FILE
    declared = {str(x["listener"]) for x in declaration.get("temporary_subscribers") or []}
    problems = [f"{where}: add {e['listener']} to temporary_subscribers: once the feature runs in its service, the "
                f"event {e['event']} no longer reaches it in-process, so only a monolith subscriber does {e['owner']}'s "
                "work until its own subscription is live" for e in expected if e["listener"] not in declared]
    known = {e["listener"] for e in expected}
    problems += [f"{where}: temporary subscriber {n} is no event handler (or temporary_handler) of {DECISIONS_FILE}"
                 for n in sorted(declared - known)]
    return problems


def _module_extraction_problems(output_dir: Path, declaration: dict) -> list[str]:
    problems = []
    deps = output_dir / DOMAIN_DEPENDENCIES_FILE
    if deps.is_file():
        try:
            scope = load_domain_dependencies(deps.read_text(encoding="utf-8"))["decision"]["scope"]
        except InvalidDocument:
            scope = declaration["scope"]
        if scope != declaration["scope"]:
            problems.append(f"{MODULE_EXTRACTION_FILE}: scope is {declaration['scope']}, but {DOMAIN_DEPENDENCIES_FILE} "
                            f"decides {scope}")
    return problems


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
        handler = d.get("temporary_handler")
        if handler is not None:
            if d["pattern"] != "event":
                raise InvalidDocument(f"{where}: only an event decision has a temporary_handler")
            if not isinstance(handler, dict):
                raise InvalidDocument(f"{where}: temporary_handler must be a mapping")
            _require(handler, ("listener", "delegates_to", "remove_when"), f"{where} temporary_handler")
            if not isinstance(handler["delegates_to"], list) or not all(
                    isinstance(m, str) and "#" in m for m in handler["delegates_to"]):
                raise InvalidDocument(f"{where} temporary_handler: delegates_to lists the handling domain's existing "
                                      "Class#method(s)")
        handler = d.get("event_handler")
        if handler is not None:
            if d["pattern"] != "event":
                raise InvalidDocument(f"{where}: only an event decision has an event_handler")
            if d.get("temporary_handler") is not None:
                raise InvalidDocument(f"{where}: an event_handler replaces the temporary_handler; keep one")
            if not isinstance(handler, dict):
                raise InvalidDocument(f"{where}: event_handler must be a mapping")
            _require(handler, ("domain", "calls", "remove_when"), f"{where} event_handler")
            if not isinstance(handler["calls"], list) or not handler["calls"] or not all(
                    isinstance(k, str) for k in handler["calls"]):
                raise InvalidDocument(f"{where} event_handler: calls lists the contract keys of the integration entry "
                                      "points the handler calls (one per domain it does work in)")
            if not d.get("contract"):
                raise InvalidDocument(f"{where} event_handler: the decision names the event's contract, which names "
                                      "the handler")
    return {"decisions": decisions, "contracts": contracts}


def _exists(key: str) -> bool:
    with session_scope() as s:
        return s.get(Contract, (projects.current(), key)) is not None


def validate_outputs(output_dir: Path, only: set[str] | None = None) -> list[str]:
    """Problems with the structured cross-domain files in the documents folder (none when the files are absent).
    With `only` (file names), just those files' problems: an agent is judged on the documents it writes, not on
    earlier steps' (which are read all the same, as the files depend on each other)."""
    problems = []
    linked: list[dict] = []
    for name, found in _validated(output_dir, linked):
        if only is None or name in only:
            problems += found
    return problems


def _validated(output_dir: Path, linked: list[dict]):
    """(file name, its problems) for each structured file present, in the order they depend on each other."""
    for name, parse in ((CROSS_DOMAIN_FILE, parse_cross_domain), (DOMAIN_DEPENDENCIES_FILE, parse_domain_dependencies),
                        (FUNCTIONAL_ALLOCATION_FILE, parse_functional_allocation),
                        (LINKED_CONTRACTS_FILE, parse_linked_domain_contracts),
                        (DOMAIN_DESIGN_FILE, parse_domain_design),
                        (MODULE_EXTRACTION_FILE, parse_module_extraction),
                        (ORCHESTRATOR_EXTRACTION_FILE, parse_orchestrator_extraction),
                        (ORCHESTRATOR_SERVICE_FILE, parse_orchestrator_service),
                        (INTEGRATION_ROUTING_FILE, parse_integration_routing),
                        (MICROSERVICE_EXTRACTION_FILE, parse_microservice_extraction),
                        (DECISIONS_FILE, lambda text: parse_decisions(text, {c["key"] for c in linked}))):
        f = output_dir / name
        if not f.is_file():
            continue
        problems = []
        try:
            parsed = parse(f.read_text(encoding="utf-8"))
        except InvalidDocument as e:
            yield name, [str(e)]
            continue
        if name == LINKED_CONTRACTS_FILE:
            linked[:] = parsed
            problems += _linked_contracts_problems(output_dir, parsed)
            problems += [f"{LINKED_CONTRACTS_FILE}: spec file {c['spec']} for contract {c['key']} does not exist"
                         for c in parsed if c.get("spec") and not (output_dir / c["spec"]).is_file()]
        if name == FUNCTIONAL_ALLOCATION_FILE:
            problems += _functional_allocation_problems(output_dir, parsed)
        if name == DOMAIN_DESIGN_FILE:
            problems += _domain_design_problems(output_dir, parsed)
        if name == MODULE_EXTRACTION_FILE:
            problems += _module_extraction_problems(output_dir, parsed)
        if name == ORCHESTRATOR_EXTRACTION_FILE:
            problems += _orchestrator_extraction_problems(output_dir, parsed)
        if name == ORCHESTRATOR_SERVICE_FILE:
            problems += _orchestrator_service_problems(output_dir, parsed)
        if name == INTEGRATION_ROUTING_FILE:
            problems += _integration_routing_problems(output_dir, parsed)
        if name == DECISIONS_FILE:
            for c in parsed["contracts"]:
                if c.get("spec") and not (output_dir / c["spec"]).is_file():
                    problems.append(f"{DECISIONS_FILE}: spec file {c['spec']} for contract {c['key']} does not exist")
        yield name, problems


def _latest_artifact(s, run_id: str, stages: list[str], name: str) -> Artifact | None:
    """The file `name` from the latest attempt of the first of these stages that wrote it."""
    rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(stages))
                     .order_by(Artifact.attempt.desc(), Artifact.id.desc())).all()
    latest_attempt: dict[str, int] = {}
    for a in rows:
        latest_attempt.setdefault(a.stage, a.attempt)
    return next((a for a in rows if a.name == name and a.attempt == latest_attempt[a.stage]), None)


def check_functional_allocation(run_id: str, reviewed_stages: list[str]) -> None:
    """Before a gate is approved: the functional allocation of the steps it reviews, as a reviewer may have edited it,
    is valid (InvalidDocument otherwise). Nothing to check when those steps wrote none."""
    with session_scope() as s:
        artifact = _latest_artifact(s, run_id, reviewed_stages, FUNCTIONAL_ALLOCATION_FILE)
        content = None if artifact is None else artifact.content or ""
        folder = None if artifact is None else str(PurePosixPath(artifact.path).parent)
    if content is None:
        return
    allocation = parse_functional_allocation(content)
    # The same checks the dependency step had to pass, against the run's documents as they are now (a reviewer may
    # have changed a placement): the design must be able to follow what is approved.
    import tempfile

    from app import workspace

    with tempfile.TemporaryDirectory() as tmp:
        here = Path(tmp)
        for path, text in workspace.effective_documents(run_id).items():
            p = PurePosixPath(path)
            if str(p.parent) == folder and p.name in (CROSS_DOMAIN_FILE, DOMAIN_DEPENDENCIES_FILE, "functional-spec.md"):
                (here / p.name).write_text(text or "", encoding="utf-8")
        (here / FUNCTIONAL_ALLOCATION_FILE).write_text(content, encoding="utf-8")
        problems = _functional_allocation_problems(here, allocation)
    if problems:  # includes a direct call the domain's setting doesn't allow
        raise InvalidDocument("; ".join(problems))


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
    # Locked while it is updated: another run's approval waits, so neither's consumers are lost.
    row = s.get(Contract, (project, c["key"]), with_for_update=True)
    existed = row is not None
    if row is None:
        row = Contract(project=project, key=c["key"], consumers=[], status=status, provider=c["provider"],
                       kind=c["kind"], name=c["name"])
        s.add(row)
        s.flush()  # a contract another run added at the same moment fails here; recorded() retries
    elif status == "approved" and row.status not in _LIVE:
        row.status = "approved"
    row.consumers = sorted(set(row.consumers or []) | set(consumers))
    if c.get("reuse") and existed:
        return  # only a new consumer: the catalogue's definition stands
    row.provider, row.kind, row.name = c["provider"], c["kind"], c["name"]
    row.version = str(c.get("version") or row.version or "1")
    row.description = c.get("description") or row.description or ""
    row.spec, row.spec_path, row.run_id = spec or row.spec, spec_path or row.spec_path, run_id
    if isinstance(c.get("binding"), dict):  # a later feature reuses its entry point (app/legacy_modules.py)
        row.binding = c["binding"]


def _retrying(record):
    """Record into the catalogue again when another run's approval added the same new contract at the same moment
    (the second insert fails): the retry finds it and updates it."""
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return record(*args, **kwargs)
            except IntegrityError:
                if attempt == 2:
                    raise
    wrapper.__doc__, wrapper.__name__ = record.__doc__, record.__name__
    return wrapper


@_retrying
def withdraw_run_contracts(run_id: str, keep: set[str] | None = None) -> list[str]:
    """This run's contracts in the catalogue that its design no longer has (all of them when `keep` is None: its design
    is being redone). Consumers are domains, so the run's domain stays a consumer while another run of the domain still
    uses the contract; a contract no other run uses and no service provides yet is removed. They are recorded again
    when the design is approved. Returns the keys withdrawn."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            return []
        used = _contracts_used_by_other_runs(s, run)  # key -> domains of the other runs using it
        withdrawn = []
        for row in s.scalars(select(Contract).where(Contract.project == run.project)):
            if keep is not None and row.key in keep:
                continue
            ours, consumed = row.run_id == run_id, run.domain in (row.consumers or [])
            if not ours and not consumed:
                continue
            others = used.get(row.key, set())
            if ours and not others and row.status not in _LIVE:
                s.delete(row)  # only this run's design had it
            elif consumed and run.domain not in others:
                row.consumers = [c for c in row.consumers or [] if c != run.domain]
            else:
                continue  # another run of the domain still uses it
            withdrawn.append(row.key)
        return sorted(withdrawn)


def _contracts_used_by_other_runs(s, run: Run) -> dict[str, set[str]]:
    """The contract keys the other runs of the project use in their latest design (linked-domain contracts and
    integration decisions), with the domains of those runs."""
    return contracts_used_by_runs(s, run.project, {run.id})


def contracts_used_by_runs(s, project: str, excluding: set[str]) -> dict[str, set[str]]:
    """The contract keys the project's runs, other than `excluding`, use in their latest design (linked-domain contracts
    and integration decisions), with the domains of those runs."""
    used: dict[str, set[str]] = {}
    rows = s.scalars(select(Artifact).join(Run, Run.id == Artifact.run_id).where(
        Run.project == project, Artifact.run_id.not_in(excluding),
        Artifact.name.in_((LINKED_CONTRACTS_FILE, DECISIONS_FILE)))).all()
    latest: dict[tuple[str, str], Artifact] = {}
    for a in rows:
        k = (a.run_id, a.name)
        if k not in latest or a.attempt > latest[k].attempt:
            latest[k] = a
    domains = {r.id: r.domain for r in s.scalars(select(Run).where(Run.project == project))}
    import yaml

    for (other, _name), a in latest.items():
        try:  # only the keys: each file's own checks ran when it was written
            items = (yaml.safe_load(a.content or "") or {}).get("contracts") or []
        except (yaml.YAMLError, AttributeError):
            continue
        for c in items:
            if isinstance(c, dict) and c.get("key"):
                used.setdefault(str(c["key"]), set()).add(domains.get(other, ""))
    return used


def _design_contract_keys(s, run_id: str, reviewed_stages: list[str]) -> set[str]:
    """The contract keys the run's design has now: its linked-domain contracts and its integration decisions'."""
    keys: set[str] = set()
    linked = _latest_artifact(s, run_id, reviewed_stages, LINKED_CONTRACTS_FILE)
    if linked is not None:
        keys |= {c["key"] for c in parse_linked_domain_contracts(linked.content)}
    decisions = _latest_decisions(s, run_id, reviewed_stages)
    if decisions is not None:
        try:
            keys |= {c["key"] for c in parse_decisions(decisions.content, keys)["contracts"]}
        except InvalidDocument:
            pass
    return keys


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
        keep = _design_contract_keys(s, run_id, reviewed_stages)
        base = PurePosixPath(artifact.path).parent
        for c in contracts:
            _upsert_contract(s, run.project, run_id, c, base, "proposed", [run.domain])
    # the design approved now no longer has these (an earlier approval recorded them): out of the catalogue
    withdraw_run_contracts(run_id, keep)
    return len(contracts)


@_retrying
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
            "binding": c.binding, "status": c.status, "run_id": c.run_id, "created_at": c.created_at,
            "updated_at": c.updated_at}


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
        me = next((d for d in domains if d.key == domain), None)
        direct = (me.direct_calls or []) if me is not None else []
        lines.append(f"Direct calls (the domain's setting): {domain} may call "
                     + (f"{', '.join(direct)} directly through their API, without an orchestrator (`domain-call`); "
                        "every other domain only through the orchestrator." if direct else
                        "no other domain directly: every call to another domain goes through the orchestrator."))
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
                      + (f", spec `{CATALOGUE_DIR}/{c.key}.yaml`" if c.spec else "")
                      + _entry_point_note(c) for c in contracts]
            mine = [c for c in contracts if c.provider == domain and _entry_point_note(c)]
            if mine:
                lines.append(f"Entry points other features built for {domain} in the integration module while it is "
                             "not migrated: the APIs other domains rely on. A design of this domain provides each "
                             "under the same contract, and its package goes once its service does: "
                             + ", ".join(f"`{c.key}`" for c in mine) + ".")
        else:
            lines.append("The contracts catalogue is empty: this is the first domain to define cross-domain contracts.")
        handlers = temporary_handlers_calling(s, project, domain)
    if handlers:
        lines.append(f"Temporary handlers of other features that call {domain}'s existing services (keep each "
                     "delegates_to method working through its bean):")
        lines += [f"- {h['domain']}/{h['feature']}: {h['listener']} for `{h['event']}` → {', '.join(h['delegates_to'])}"
                  + ((" (also subscribed to Service Bus in the monolith"
                      + (f", switched by `{h['enabled_property']}`" if h["enabled_property"] else "") + ")")
                     if h["subscribed"] else "") for h in handlers]
        lines.append(f"When {domain}'s design subscribes to one of these events itself (an inbound `event` decision), "
                     "the monolith's Service Bus subscriber of each one marked above is switched off (its enabled "
                     "property, in that feature's integration-routing.yaml) when that subscription goes live, or the "
                     "work is done twice; the in-process handler stays until the publishing feature runs only in its "
                     "own service.")
        if any(h.get("event_handler") for h in handlers):
            lines.append(f"Those built as event handlers sit in the event-handler module's {domain} package: they are "
                         f"the events {domain} must handle itself once migrated (its service subscribes to each), "
                         "after which that package is deleted.")
    return "\n".join(lines)


def _entry_point_note(c: Contract) -> str:
    """The catalogue line's note on a contract's integration entry point (app/legacy_modules.py), when it has one."""
    binding = c.binding if isinstance(c.binding, dict) else None
    ep = (binding or {}).get("entry_point")
    if not isinstance(ep, dict) or c.status == "provided-by-service":
        return ""
    from app.legacy_modules import entry_point_names

    names = entry_point_names(c.key)
    methods = ", ".join(str(op.get("method")) for op in ep.get("operations") or [] if isinstance(op, dict))
    return (f"; integration entry point {names['api']} ({methods}) while {c.provider} is not migrated: reuse: true "
            "when it covers the need, else define the contract again with every operation above plus the new ones")


def temporary_handlers_calling(s, project: str, domain: str) -> list[dict]:
    """The temporary handlers in other domains' latest designs that call `domain`'s existing services (their decision's
    target), and whether their feature's Monolith integration subscribed them to Service Bus in the monolith (and the
    property that switches that subscriber off)."""
    rows = s.scalars(select(Artifact).join(Run, Run.id == Artifact.run_id).where(
        Run.project == project, Run.domain != domain,
        Artifact.name.in_((DECISIONS_FILE, INTEGRATION_ROUTING_FILE)))).all()
    latest: dict[tuple[str, str], Artifact] = {}
    for a in rows:
        k = (a.run_id, a.name)
        if k not in latest or (a.attempt, a.id) > (latest[k].attempt, latest[k].id):
            latest[k] = a
    runs = {r.id: r for r in s.scalars(select(Run).where(Run.id.in_({k[0] for k in latest})))} if latest else {}
    out = []
    for (run_id, name), a in sorted(latest.items()):
        if name != DECISIONS_FILE or run_id not in runs:
            continue
        try:
            decisions = (yaml.safe_load(a.content or "") or {}).get("decisions") or []
        except (yaml.YAMLError, AttributeError):
            continue
        routing = latest.get((run_id, INTEGRATION_ROUTING_FILE))
        try:
            subscribed = {str(x.get("listener")): str(x.get("enabled_property") or "")
                          for x in (yaml.safe_load(routing.content or "") or {}).get("temporary_subscribers") or []
                          if isinstance(x, dict)} if routing else {}
        except (yaml.YAMLError, AttributeError):
            subscribed = {}
        for d in decisions:
            h = d.get("temporary_handler") if isinstance(d, dict) else None
            if isinstance(h, dict) and h.get("listener") and d.get("target") == domain:
                out.append({"domain": runs[run_id].domain, "feature": runs[run_id].operation,
                            "listener": str(h["listener"]), "event": str(d.get("contract") or ""),
                            "delegates_to": [str(m) for m in h.get("delegates_to") or []],
                            "subscribed": str(h["listener"]) in subscribed,
                            "enabled_property": subscribed.get(str(h["listener"])) or None})
            h = d.get("event_handler") if isinstance(d, dict) else None
            if isinstance(h, dict) and str(h.get("domain")) == domain and d.get("contract"):
                from app.legacy_modules import handler_names

                name = handler_names(str(d["contract"]), domain)["interface"]
                out.append({"domain": runs[run_id].domain, "feature": runs[run_id].operation, "listener": name,
                            "event": str(d["contract"]),
                            "delegates_to": [f"entry point of {k}" for k in h.get("calls") or []],
                            "subscribed": name in subscribed, "enabled_property": subscribed.get(name) or None,
                            "event_handler": True})
    return out


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
