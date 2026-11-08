"""Moving features to another domain, new or existing: splitting a domain that has grown too large.

Only features no migration has started can move: a run's branch, service folder, contracts and dependencies are tied
to its domain. `preview` says everything a move would change, for a person to review and adjust; `move` applies what
they confirmed, in one transaction, and is refused if the data changed since that review (the `fingerprint`).

Calls between features of one domain aren't recorded anywhere, so a split can turn some into cross-domain calls that
nothing here can see. The review shows what evidence there is (shared tables), and the user acknowledges this.
"""

import hashlib
import json
import logging

from sqlalchemy import or_, select

from app import contracts_store, discovery_store, domain_store, projects
from app.db.models import Artifact, Contract, Dependency, Domain, Feature, Run, Setting
from app.db.session import session_scope

log = logging.getLogger(__name__)

# A discovery run in these states is working: what it records could contradict the move. One waiting at its gate
# doesn't block: approving it later can't undo the move (discovery_store._record_features, _record_contexts).
WORKING = ("pending", "running", "awaiting_input")
STARTED = ("Its migration has started, so it can't move. An admin can reset it first: that deletes its runs, documents, "
           "dependencies and proposed contracts, while approved contracts and pushed code stay.")


class Refused(RuntimeError):
    """The move can't be applied as asked (blocked, or the review is out of date)."""


def _unique(values) -> list:
    return list(dict.fromkeys(values))


def _design_tables(s, project: str, source: str) -> set[str]:
    """The tables the latest designs of the source's runs own or read: they rely on the domain keeping them."""
    rows = s.scalars(select(Artifact).join(Run, Run.id == Artifact.run_id).where(
        Run.project == project, Run.domain == source, Artifact.name == contracts_store.DOMAIN_DESIGN_FILE)
        .order_by(Artifact.attempt, Artifact.id)).all()
    latest = {a.run_id: a for a in rows}
    tables: set[str] = set()
    for a in latest.values():
        try:
            design = contracts_store.parse_domain_design(a.content or "")
        except contracts_store.InvalidDocument:
            continue
        tables.update(t.upper() for t in [*design["owned_tables"], *design["read_only_tables"]])
    return tables


def _table_rows(moving, remaining, others, domains, source, target, locked_by_runs, design) -> list[dict]:
    """One row per table the moving features use or the source owns, with what the review offers for it. `selected` is
    the default, which the user's choice replaces (see _review)."""
    owners: dict[str, list[str]] = {}
    spelling: dict[str, str] = {}
    for d in domains:
        for t in d.owned_tables or []:
            owners.setdefault(t.upper(), []).append(d.key)
            if d.key == source:  # the domain being reviewed spells it, when domains differ
                spelling[t.upper()] = t
            else:
                spelling.setdefault(t.upper(), t)

    def using(rows):
        used: dict[str, list[str]] = {}
        for f in rows:
            for t in f.tables or []:
                used.setdefault(t.upper(), []).append(f.key)
                spelling.setdefault(t.upper(), t)
        return used

    moved, stays = using(moving), using(remaining)
    elsewhere: dict[str, list[str]] = {}
    for f in others:
        for t in f.tables or []:
            elsewhere.setdefault(t.upper(), []).append(f"{f.domain}/{f.key}")
    universe = {t for t, who in owners.items() if source in who} | set(moved)
    rows = []
    for upper in sorted(universe):
        who = owners.get(upper, [])
        in_source, in_target = source in who, target in who
        third = [d for d in who if d not in (source, target)]
        running = [k for k in stays.get(upper, []) if k in locked_by_runs]
        selected, locked, note, group = False, False, "", "used" if upper in moved else "other"
        if in_source and (running or upper in design):
            locked = True
            note = (f"Stays: {', '.join(running)} already migrating uses it" if running else
                    f"Stays: the design of one of {source}'s runs relies on it")
        elif in_target and not in_source:
            locked, note = True, f"Already owned by {target}: no change needed"
        elif in_target:
            selected, note = True, f"Also owned by {target}: ticked removes it from {source}"
        elif in_source and third:
            note = f"Also owned by {', '.join(third)}: that conflict stays either way"
            if upper in moved and upper in stays:
                note += f". Also used by {', '.join(stays[upper])}, which stay: one side will need a contract to reach it"
        elif in_source and upper in moved and upper in stays:
            note = f"Also used by {', '.join(stays[upper])}, which stay: one side will need a contract to reach it"
        elif in_source and upper in moved:
            selected = True
        elif third:
            locked, note = True, f"Owned by {', '.join(third)}"
        elif not in_source:
            note = "No domain owns it yet"
        rows.append({"table": spelling[upper], "owners": who, "moving_features": moved.get(upper, []),
                     "remaining_features": stays.get(upper, []), "other_features": elsewhere.get(upper, []),
                     "selected": selected, "locked": locked, "note": note, "group": group})
    return rows


def _reference_updates(moving, remaining, others, source, target, moved_tables, owners_after) -> list[dict]:
    """The edits to `calls` and `used_by` the move makes true. Each has a stable id, so it can be skipped by the user."""
    updates: dict[str, dict] = {}
    moving_keys = {f.key for f in moving}

    def add_call(f, domain, why):
        home = target if f.domain == source and f.key in moving_keys else f.domain  # where it will be
        if domain not in (f.calls or []) and domain != home:
            uid = f"calls:{f.domain}/{f.key}:add:{domain}"
            updates[uid] = {"id": uid, "domain": f.domain, "feature": f.key, "kind": "calls", "op": "add",
                            "value": domain, "description": (
                                f"{f.key}: will call {domain} ({why})" if f.key in moving_keys and f.domain == source
                                else f"{f.key} in {f.domain}: also calls {domain} ({why})")}

    moved_points = {p for f in moving for p in f.entry_points or []}
    for f in moving:
        if target in (f.calls or []):
            uid = f"calls:{f.domain}/{f.key}:remove:{target}"
            updates[uid] = {"id": uid, "domain": f.domain, "feature": f.key, "kind": "calls", "op": "remove",
                            "value": target, "description": f"{f.key}: no longer calls {target}, its new domain"}
        for t in f.tables or []:
            after = owners_after.get(t.upper(), set())
            if source in after and target not in after:
                add_call(f, source, f"{t} stays there")
        for u in f.used_by or []:
            if u.get("domain") == target:
                uid = f"used_by:{f.domain}/{f.key}:remove:{target}:{u.get('entry_point')}"
                updates[uid] = {"id": uid, "domain": f.domain, "feature": f.key, "kind": "used_by", "op": "remove",
                                "value": u, "description": f"{f.key}: {u.get('entry_point')} is no longer outside its "
                                                           "domain, so it leaves used_by"}
            elif u.get("domain") not in (source, "external"):
                for other in others:
                    if other.domain == u.get("domain") and u.get("entry_point") in (other.entry_points or []):
                        add_call(other, target, f"it uses {f.key}")
    for f in [*remaining, *others]:
        if f.domain != target and any(t.upper() in moved_tables for t in f.tables or []):
            add_call(f, target, "a table it uses moves there")
    for f in others:
        for u in f.used_by or []:
            if u.get("domain") == source and u.get("entry_point") in moved_points:
                op = "remove" if f.domain == target else "repoint"
                uid = f"used_by:{f.domain}/{f.key}:{op}:{source}:{u.get('entry_point')}"
                updates[uid] = {"id": uid, "domain": f.domain, "feature": f.key, "kind": "used_by", "op": op,
                                "value": u, "description": (
                                    f"{f.key} in {f.domain}: {u.get('entry_point')} is now inside its domain, so it "
                                    "leaves used_by" if op == "remove" else
                                    f"{f.key} in {f.domain}: used_by {u.get('entry_point')} now names {target}")}
    return sorted(updates.values(), key=lambda u: u["id"])


def _check_by_hand(s, project: str, src: Domain, moving, others) -> dict:
    """What may need a person: the data doesn't say which feature each of these is about, so none is edited."""
    source, keys = src.key, {f.key.lower() for f in moving}
    contracts = s.scalars(select(Contract).where(Contract.project == project, Contract.provider == source)
                          .order_by(Contract.key)).all()
    dependencies = s.scalars(select(Dependency).where(Dependency.project == project, Dependency.target == source)
                             .order_by(Dependency.id)).all()
    return {
        "calling_features": [{"domain": f.domain, "feature": f.key} for f in others
                             if f.domain != source and source in (f.calls or [])],
        "dependencies": [{"source": d.source, "interaction": d.interaction, "contract": d.contract,
                          "status": d.status} for d in dependencies],
        "contracts": [{"key": c.key, "status": c.status, "consumers": c.consumers or [],
                       "matches_moved_feature": c.key.rsplit(".", 1)[-1].lower() in keys} for c in contracts],
        "listing_paths": ((src.agent_inputs or {}).get(discovery_store.FEATURES_AGENT) or {}).get("paths") or [],
    }


def _review(s, source: str, features: list[str], unticked: list[str], target: str | None, new_domain: dict | None,
            tables: list[str] | None, packages: list[str] | None, skipped_references: list[str]) -> dict:
    project = projects.current()
    src = s.get(Domain, (project, source))
    if src is None:
        raise KeyError(source)
    target_key = new_domain["key"] if new_domain else target
    tgt = None if new_domain else s.get(Domain, (project, target_key))
    blockers = []
    if new_domain:
        if problem := domain_store.new_domain_problem(s, project, target_key):
            blockers.append(problem)
    elif tgt is None:
        blockers.append(f"Domain {target_key} doesn't exist")
    if target_key == source:
        blockers.append("The features are already in this domain: choose another")
    working = s.scalars(select(Run).where(
        Run.project == project, Run.status.in_(WORKING),
        or_(Run.domain == discovery_store.MONOLITH,
            Run.domain.in_([source, target_key]) & (Run.operation == discovery_store.FEATURES_OP)))).all()
    blockers += [f"{discovery_store.subject(r.domain, r.operation)} is working: wait for it to reach its gate"
                 for r in working]

    everything = s.scalars(select(Feature).where(Feature.project == project).order_by(Feature.id)).all()
    of_source = {f.key: f for f in everything if f.domain == source}
    in_target = [f.key for f in everything if f.domain == target_key]
    runs = s.scalars(select(Run).where(Run.project == project, Run.domain.in_([source, target_key]),
                                       discovery_store.MIGRATION_RUN).order_by(Run.created_at)).all()
    started = {r.operation: r for r in runs if r.domain == source}
    target_runs = {r.operation for r in runs if r.domain == target_key}

    rows, moving = [], []
    for key in _unique(features):
        f = of_source.get(key)
        if f is None:
            blockers.append(f"{key} isn't a feature of {source}")
            continue
        run = started.get(key)
        clash = discovery_store._package_clash(key, in_target)
        blocked = (STARTED if run else
                   f"{target_key} already has a feature {key}" if key in in_target else
                   f"{target_key} has a migration run of {key} from earlier: reset it there first"
                   if key in target_runs else
                   f"{target_key} can't have it: {clash}" if clash else None)
        ticked = key not in unticked and blocked is None
        if ticked:
            moving.append(f)
        rows.append({"key": key, "name": f.name, "description": f.description, "entry_points": f.entry_points or [],
                     "tables": f.tables or [], "calls": f.calls or [], "used_by": f.used_by or [], "ticked": ticked,
                     "blocked": blocked,
                     "migration": {"id": run.id, "status": run.status} if run else None})
    if not moving and not blockers:
        blockers.append("Tick at least one feature to move")

    moving_keys = {f.key for f in moving}
    remaining = [f for f in of_source.values() if f.key not in moving_keys]
    others = [f for f in everything if f.domain != source]
    domains = s.scalars(select(Domain).where(Domain.project == project).order_by(Domain.key)).all()
    table_rows = _table_rows(moving, remaining, others, domains, source, target_key,
                             set(started), _design_tables(s, project, source))
    if tables is not None:  # the user's choice; a row that can't be chosen any more (it locked since) stays unticked
        chosen = {t.upper() for t in tables}
        for row in table_rows:
            row["selected"] = row["table"].upper() in chosen and not row["locked"]

    moved_tables = {r["table"].upper() for r in table_rows if r["selected"]}
    owners_after = {}
    for d in domains:
        for t in d.owned_tables or []:
            owners_after.setdefault(t.upper(), set()).add(d.key)
    for t in moved_tables:
        owners_after.setdefault(t, set()).discard(source)
        owners_after[t].add(target_key)

    chosen_packages = {p.upper() for p in packages or []}
    package_rows = [{"package": p, "selected": p.upper() in chosen_packages} for p in src.owned_packages or []]

    references = _reference_updates(moving, remaining, others, source, target_key, moved_tables, owners_after)
    for r in references:
        r["selected"] = r["id"] not in skipped_references

    shared = sorted(r["table"] for r in table_rows if r["moving_features"] and r["remaining_features"])
    warnings = ["Calls between features aren't recorded, so calls between the moved and remaining features can't be "
                "found here. They would become calls between domains." +
                (f" Tables used on both sides: {', '.join(shared)}." if shared else ""),
                f"Moved features can't use the {source} domain's shared and util packages: what they need of them "
                "has to be copied or reached through a contract."]
    if moving and not moved_tables:
        warnings.append(f"No table moves, so {target_key} owns nothing new and the boundary check doesn't protect "
                        "these features' tables.")
    if moving and not remaining:
        warnings.append(f"Every feature leaves {source}: listing features for all domains will re-list it.")
    if any(r["kind"] == "used_by" and not r["selected"] for r in references):
        warnings.append("used_by can't be edited by hand: an unticked used_by update can only be fixed by re-listing.")

    staying = sorted({t for f in moving for t in f.tables or []
                      if target_key not in owners_after.get(t.upper(), set())})
    return {
        "source": source, "target": target_key, "new_domain": bool(new_domain), "blockers": blockers, "features": rows,
        "tables": table_rows, "packages": package_rows, "references": references, "warnings": warnings,
        "check_by_hand": _check_by_hand(s, project, src, moving, others),
        "copied": _copied(src, moving) if new_domain else None,
        "summary": {"features": len(moving), "tables": sorted(moved_tables), "staying_tables": staying},
    }


def _copied(src: Domain, moving) -> dict:
    inputs = {}
    for agent, values in (src.agent_inputs or {}).items():
        inputs[agent] = {k: v for k, v in values.items() if k not in ("repository", "base_branch")}
    listing = inputs.get(discovery_store.FEATURES_AGENT) or {}
    inputs[discovery_store.FEATURES_AGENT] = {  # the source's paths would list the whole old domain again
        "paths": _unique(p for f in moving for p in f.entry_points or []), "notes": listing.get("notes", "")}
    return {"context": src.context or "", "default_workflow": src.default_workflow, "agent_inputs": inputs}


def _fingerprint(review: dict) -> str:
    return hashlib.sha256(json.dumps(review, sort_keys=True, default=str).encode()).hexdigest()


def preview(source: str, features: list[str], unticked: list[str] = (), target: str | None = None,
            new_domain: dict | None = None, tables: list[str] | None = None, packages: list[str] | None = None,
            skipped_references: list[str] = ()) -> dict:
    """Everything the move would change, changing nothing."""
    with session_scope() as s:
        review = _review(s, source, features, list(unticked), target, new_domain, tables, packages,
                         list(skipped_references))
    return {**review, "fingerprint": _fingerprint(review)}


def _apply_reference(row: Feature, update: dict, target: str) -> None:
    if update["kind"] == "calls":
        calls = list(row.calls or [])
        row.calls = [*calls, update["value"]] if update["op"] == "add" else [c for c in calls if c != update["value"]]
    elif update["op"] == "remove":
        row.used_by = [u for u in row.used_by or [] if u != update["value"]] or None
    else:
        row.used_by = [{**u, "domain": target} if u == update["value"] else u for u in row.used_by or []]


def move(source: str, features: list[str], fingerprint: str, user: str, unticked: list[str] = (),
         target: str | None = None, new_domain: dict | None = None, tables: list[str] | None = None,
         packages: list[str] | None = None, skipped_references: list[str] = ()) -> dict:
    """Apply the move as reviewed, or nothing at all. Refused if anything changed since `fingerprint`'s review."""
    project = projects.current()
    target_key = new_domain["key"] if new_domain else target
    with session_scope() as s:
        for key in sorted({source, target_key}):  # an approved feature list waits for this, and this for it
            s.get(Domain, (project, key), with_for_update=True)
        review = _review(s, source, features, list(unticked), target, new_domain, tables, packages,
                         list(skipped_references))
        if review["blockers"]:
            raise Refused("; ".join(review["blockers"]))
        blocked = [f"{f['key']}: {f['blocked']}" for f in review["features"]
                   if f["blocked"] and f["key"] not in unticked]
        if blocked:
            raise Refused("; ".join(blocked))
        locked = [r["table"] for r in review["tables"]
                  if r["locked"] and r["table"].upper() in {t.upper() for t in tables or []}]
        if locked:
            raise Refused(f"These tables can't move: {', '.join(locked)}")
        if _fingerprint(review) != fingerprint:
            raise Refused("The data changed since this review: review the move again")

        src = s.get(Domain, (project, source))
        if new_domain:
            copied = review["copied"]
            tgt = Domain(project=project, key=target_key, name=new_domain["name"],
                         description=new_domain.get("description") or "", context=copied["context"],
                         default_workflow=copied["default_workflow"], agent_inputs=copied["agent_inputs"])
            s.add(tgt)
            setting = s.get(Setting, (project, discovery_store.CONTEXTS_SETTING))
            if setting is not None and setting.value:  # or the overview would say the latest contexts didn't find it
                setting.value = {**setting.value, "keys": [*setting.value["keys"], target_key]}
        else:
            tgt = s.get(Domain, (project, target_key))

        moved_tables = [r["table"] for r in review["tables"] if r["selected"]]
        moved_packages = [p["package"] for p in review["packages"] if p["selected"]]
        for field, values in (("owned_tables", moved_tables), ("owned_packages", moved_packages)):
            leaving = {v.upper() for v in values}
            setattr(src, field, [v for v in getattr(src, field) or [] if v.upper() not in leaving])
            present = {v.upper() for v in getattr(tgt, field) or []}
            setattr(tgt, field, [*(getattr(tgt, field) or []), *(v for v in values if v.upper() not in present)])

        rows = {(f.domain, f.key): f for f in s.scalars(select(Feature).where(Feature.project == project))}
        for update in review["references"]:
            if update["selected"]:
                _apply_reference(rows[update["domain"], update["feature"]], update, target_key)
        moved = [f["key"] for f in review["features"] if f["ticked"]]
        for key in moved:
            row = rows[source, key]
            row.domain, row.missing_from_latest = target_key, False
            row.moved_from = [*(d for d in row.moved_from or [] if d not in (source, target_key)), source]
    log.info("%s moved %s from %s to %s, with tables %s and packages %s", user, moved, source, target_key,
             moved_tables, moved_packages)
    return {"source": source, "target": target_key, "new_domain": bool(new_domain), "moved": moved,
            "tables": moved_tables, "packages": moved_packages}
