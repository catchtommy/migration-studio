"""Discovery, before any migration: the monolith's bounded contexts, then each domain's features.

Both are ordinary workflow runs with a reserved name, so they reuse the runner, gates, questions and documents:
- bounded contexts: domain MONOLITH, operation CONTEXTS_OP, scope "monolith" (one for the whole monolith)
- a domain's features: the domain, operation FEATURES_OP, scope "domain"
Neither name can clash with a real domain or operation, whose keys must start with a letter.

Their agents write YAML files that are validated before the agent may finish. Approving the gate records them:
bounded contexts update the domains (new ones are added, nothing is deleted) and features replace the domain's
feature list, which migrations then pick from.
"""

import re
from pathlib import Path

from sqlalchemy import func, select

from app import projects
from app.contracts_store import InvalidDocument, _load, _require
from app.db.models import Artifact, Domain, Feature, Run, Setting
from app.db.session import session_scope

SCOPES = ("operation", "domain", "monolith")
MONOLITH = "_monolith"
CONTEXTS_OP = "bounded-contexts"
FEATURES_OP = "_features"
CONTEXTS_FILE = "bounded-contexts.yaml"
FEATURES_FILE = "features.yaml"
CONTEXTS_SETTING = "discovery_contexts"  # {"run_id", "keys"} of the latest approved bounded contexts
FEATURES_AGENT = "domain_features"  # a new context's entry points become this agent's inputs on the domain
DOMAIN_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
FEATURE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")

# Migration runs (of one feature), as opposed to discovery runs. Runs from before discovery have no scope.
MIGRATION_RUN = func.coalesce(Run.scope, "operation") == "operation"


class DiscoveryError(ValueError):
    pass


def subject(domain: str, operation: str) -> str:
    """What a run covers, for headings and messages."""
    if domain == MONOLITH:
        return "whole monolith"
    if operation == FEATURES_OP:
        return f"{domain} (all features)"
    return f"{domain} / {operation}"


# --- documents -----------------------------------------------------------------------------------------------------

def _strings(item: dict, field: str, where: str) -> list[str]:
    value = item.get(field) or []
    if not isinstance(value, list):
        raise InvalidDocument(f"{where}: {field} must be a list")
    return list(dict.fromkeys(str(v).strip() for v in value if v is not None and str(v).strip()))


def _known_domains() -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(Domain.key).where(Domain.project == projects.current())))


def parse_bounded_contexts(text: str) -> list[dict]:
    data = _load(text, CONTEXTS_FILE)
    items = data.get("contexts")
    if not isinstance(items, list) or not items:
        raise InvalidDocument(f"{CONTEXTS_FILE}: 'contexts' must be a list with at least one context")
    out: list[dict] = []
    for i, item in enumerate(items, 1):
        where = f"{CONTEXTS_FILE} context {i}"
        if not isinstance(item, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(item, ("key", "name"), where)
        key = str(item["key"]).strip()
        if not DOMAIN_KEY.match(key):
            raise InvalidDocument(f"{where}: key {key!r} must be lower-case letters, digits, - or _, starting with a "
                                  "letter")
        if any(c["key"] == key for c in out):
            raise InvalidDocument(f"{where}: duplicate key {key}")
        out.append({"key": key, "name": str(item["name"]).strip(),
                    "description": str(item.get("description") or "").strip(),
                    **{f: _strings(item, f, where) for f in ("packages", "tables", "entry_points", "depends_on")}})
    known = {c["key"] for c in out} | _known_domains()
    owners: dict[str, str] = {}
    for c in out:
        unknown = [d for d in c["depends_on"] if d not in known]
        if unknown:
            raise InvalidDocument(f"{CONTEXTS_FILE} context {c['key']}: depends_on {', '.join(unknown)} is neither a "
                                  "context in this file nor a known domain")
        for table in c["tables"]:
            owner = owners.setdefault(table.upper(), c["key"])
            if owner != c["key"]:
                raise InvalidDocument(f"{CONTEXTS_FILE}: table {table} is owned by both {owner} and {c['key']}; give "
                                      "every table one owner and note the conflict in bounded-contexts.md")
    return out


def parse_features(text: str) -> list[dict]:
    data = _load(text, FEATURES_FILE)
    items = data.get("features")
    if not isinstance(items, list) or not items:
        raise InvalidDocument(f"{FEATURES_FILE}: 'features' must be a list with at least one feature")
    known = _known_domains()
    out: list[dict] = []
    for i, item in enumerate(items, 1):
        where = f"{FEATURES_FILE} feature {i}"
        if not isinstance(item, dict):
            raise InvalidDocument(f"{where} must be a mapping")
        _require(item, ("key", "name"), where)
        key = str(item["key"]).strip()
        if not FEATURE_KEY.match(key):
            raise InvalidDocument(f"{where}: key {key!r} must be letters, digits, - or _, starting with a letter")
        if any(f["key"] == key for f in out):
            raise InvalidDocument(f"{where}: duplicate key {key}")
        feature = {"key": key, "name": str(item["name"]).strip(),
                   "description": str(item.get("description") or "").strip(),
                   **{f: _strings(item, f, where) for f in ("entry_points", "tables", "calls")}}
        unknown = [d for d in feature["calls"] if d not in known]
        if unknown:
            raise InvalidDocument(f"{where}: calls {', '.join(unknown)}, which is not a known domain")
        out.append(feature)
    return out


PARSERS = {CONTEXTS_FILE: parse_bounded_contexts, FEATURES_FILE: parse_features}


def validate_outputs(output_dir: Path) -> list[str]:
    """Problems with the discovery files an agent wrote (none when the files are absent)."""
    problems = []
    for name, parse in PARSERS.items():
        f = output_dir / name
        if f.is_file():
            try:
                parse(f.read_text())
            except InvalidDocument as e:
                problems.append(str(e))
    return problems


# --- recording approved results ------------------------------------------------------------------------------------

def _merge(current: list | None, new: list[str]) -> list[str]:
    return list(dict.fromkeys([*(current or []), *new]))


def record_approved(run_id: str, reviews: list[str]) -> str | None:
    """Record a discovery run's results when the gate reviewing them is approved. Returns a summary, or None when
    the gate has nothing to record. Raises InvalidDocument when the (possibly hand-edited) file is invalid."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        scope, domain = run.scope or "operation", run.domain
        if scope == "operation":
            return None
        name = CONTEXTS_FILE if scope == "monolith" else FEATURES_FILE
        rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(reviews),
                                                Artifact.name == name)
                         .order_by(Artifact.attempt.desc(), Artifact.id.desc())).all()
        latest = {}
        for a in s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(reviews))):
            latest[a.stage] = max(latest.get(a.stage, 0), a.attempt)
        artifact = next((a for a in rows if a.attempt == latest[a.stage]), None)
        if artifact is None:
            return None
        text = artifact.content
    if scope == "monolith":
        return _record_contexts(run_id, parse_bounded_contexts(text))
    return _record_features(run_id, domain, parse_features(text))


def _record_contexts(run_id: str, contexts: list[dict]) -> str:
    added, updated = [], []
    project = projects.current()
    with session_scope() as s:
        for c in contexts:
            domain = s.get(Domain, (project, c["key"]))
            if domain is None:
                domain = Domain(project=project, key=c["key"], name=c["name"], description=c["description"], context="",
                                agent_inputs={}, owned_tables=c["tables"], owned_packages=c["packages"])
                s.add(domain)
                added.append(c["key"])
            else:
                if not domain.description:
                    domain.description = c["description"]
                domain.owned_tables = _merge(domain.owned_tables, c["tables"])
                domain.owned_packages = _merge(domain.owned_packages, c["packages"])
                updated.append(c["key"])
            inputs = dict(domain.agent_inputs or {})
            current = inputs.get(FEATURES_AGENT) or {}
            if c["entry_points"] and not current.get("paths"):
                inputs[FEATURES_AGENT] = {"paths": c["entry_points"], "notes": current.get("notes", "")}
                domain.agent_inputs = inputs
        setting = s.get(Setting, (project, CONTEXTS_SETTING)) or Setting(project=project, key=CONTEXTS_SETTING)
        setting.value = {"run_id": run_id, "keys": [c["key"] for c in contexts]}
        s.merge(setting)
    return (f"Bounded contexts recorded: {len(added)} new domain(s){' (' + ', '.join(added) + ')' if added else ''}, "
            f"{len(updated)} updated")


def _record_features(run_id: str, domain: str, features: list[dict]) -> str:
    kept = []
    project = projects.current()
    with session_scope() as s:
        migrated = set(s.scalars(select(Run.operation).where(Run.project == project, Run.domain == domain,
                                                             MIGRATION_RUN)))
        existing = {f.key: f for f in s.scalars(select(Feature).where(Feature.project == project,
                                                                      Feature.domain == domain))}
        for f in features:
            row = existing.pop(f["key"], None)
            if row is None:
                row = Feature(project=project, domain=domain, key=f["key"])
                s.add(row)
            row.name, row.description = f["name"], f["description"]
            row.entry_points, row.tables, row.calls = f["entry_points"], f["tables"], f["calls"]
            row.run_id, row.missing_from_latest = run_id, False
        for key, row in existing.items():
            if key in migrated:
                row.missing_from_latest = True
                kept.append(key)
            else:
                s.delete(row)
    note = f"; kept {', '.join(kept)} (not in the new list, but already migrating)" if kept else ""
    return f"Feature list recorded for {domain}: {len(features)} feature(s){note}"


# --- reading -------------------------------------------------------------------------------------------------------

def _run_brief(run: Run | None) -> dict | None:
    if run is None:
        return None
    return {"id": run.id, "status": run.status, "current_stage": run.current_stage, "created_at": run.created_at}


def _feature_dict(f: Feature, run: Run | None) -> dict:
    return {"key": f.key, "domain": f.domain, "name": f.name, "description": f.description,
            "entry_points": f.entry_points or [], "tables": f.tables or [], "calls": f.calls or [],
            "run_id": f.run_id, "missing_from_latest": bool(f.missing_from_latest),
            "migration": _run_brief(run), "created_at": f.created_at, "updated_at": f.updated_at}


def features_of(domain: str) -> list[dict]:
    """The domain's features in the order they were listed, each with its latest migration run."""
    project = projects.current()
    with session_scope() as s:
        runs = {r.operation: r for r in s.scalars(select(Run).where(Run.project == project, Run.domain == domain,
                                                                    MIGRATION_RUN).order_by(Run.created_at))}
        return [_feature_dict(f, runs.get(f.key))
                for f in s.scalars(select(Feature).where(Feature.project == project, Feature.domain == domain)
                                   .order_by(Feature.id))]


def feature(domain: str, key: str) -> dict | None:
    return next((f for f in features_of(domain) if f["key"] == key), None)


def save_feature(domain: str, key: str, values: dict, create: bool) -> dict:
    project = projects.current()
    with session_scope() as s:
        row = s.scalar(select(Feature).where(Feature.project == project, Feature.domain == domain, Feature.key == key))
        if create and row is not None:
            raise DiscoveryError(f"{domain} already has a feature {key}")
        if not create and row is None:
            raise KeyError(key)
        if row is None:
            row = Feature(project=project, domain=domain, key=key, run_id=None)
            s.add(row)
        for field in ("name", "description", "entry_points", "tables", "calls"):
            if values.get(field) is not None:
                setattr(row, field, values[field])
    return feature(domain, key)


def delete_feature(domain: str, key: str) -> None:
    project = projects.current()
    with session_scope() as s:
        row = s.scalar(select(Feature).where(Feature.project == project, Feature.domain == domain, Feature.key == key))
        if row is None:
            raise KeyError(key)
        if s.scalar(select(func.count()).where(Run.project == project, Run.domain == domain, Run.operation == key,
                                               MIGRATION_RUN)):
            raise DiscoveryError(f"{key} has migration runs, so it can't be deleted")
        s.delete(row)


def overview() -> dict:
    """Where discovery stands: the latest bounded-contexts run, each domain's feature list and its latest feature
    run, and the domains the latest approved bounded contexts didn't include."""
    project = projects.current()
    with session_scope() as s:
        contexts_run = s.scalar(select(Run).where(Run.project == project, Run.domain == MONOLITH)
                                .order_by(Run.created_at.desc()).limit(1))
        setting = s.get(Setting, (project, CONTEXTS_SETTING))
        found = set(setting.value["keys"]) if setting and setting.value else None
        counts = dict(s.execute(select(Feature.domain, func.count()).where(Feature.project == project)
                                .group_by(Feature.domain)).all())
        feature_runs = {r.domain: r for r in s.scalars(select(Run).where(Run.project == project,
                                                                         Run.operation == FEATURES_OP)
                                                        .order_by(Run.created_at))}
        domains = s.scalars(select(Domain).where(Domain.project == project)
                            .order_by(Domain.created_at, Domain.key)).all()
        return {
            "bounded_contexts": _run_brief(contexts_run),
            "contexts_inputs": contexts_run.inputs if contexts_run else None,
            "contexts_approved": found is not None,
            "domains": [{"key": d.key, "name": d.name, "features": counts.get(d.key, 0),
                         "run": _run_brief(feature_runs.get(d.key))} for d in domains],
            "not_found": [d.key for d in domains if found is not None and d.key not in found],
        }


def domains_brief(domain: str) -> str:
    """The domains known so far, for discovery agents (instead of the migration catalogue)."""
    with session_scope() as s:
        domains = s.scalars(select(Domain).where(Domain.project == projects.current()).order_by(Domain.key)).all()
        lines = ["## Known domains"]
        if not domains:
            return "## Known domains\nNone yet."
        for d in domains:
            lines.append(f"- `{d.key}` {d.name}{' (this domain)' if d.key == domain else ''}"
                         + (f": {d.description}" if d.description else "")
                         + f". Tables {', '.join(d.owned_tables or []) or '–'}; packages "
                         + f"{', '.join(d.owned_packages or []) or '–'}")
        return "\n".join(lines)
