"""Named workflows: ordered agent steps (referencing the agent library) and human approval gates."""

import re
from typing import Any

from sqlalchemy import select

from app import agent_store
from app.db.models import Domain, Setting, WorkflowDef
from app.db.session import session_scope
from app.orchestrator.stages import BOUNDED_CONTEXTS, DOMAIN_FEATURES, STANDARD, StageDef, Workflow

STANDARD_KEY = "standard"
# Built-in discovery workflows: (key, name, description, workflow), by scope.
DISCOVERY = {
    "monolith": ("discover_bounded_contexts", "Discover bounded contexts",
                 "Find the monolith's bounded contexts, with their packages, tables and entry points.",
                 BOUNDED_CONTEXTS),
    "domain": ("list_features", "List domain features",
               "List every feature of a domain, with its entry points, tables and calls to other domains.",
               DOMAIN_FEATURES),
}
_GATE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class WorkflowInvalid(ValueError):
    pass


def steps_of(workflow: Workflow) -> list[dict]:
    return [{"kind": "agent", "key": s.key, "uses": None if s.uses is None else list(s.uses),
             "include_content": s.include_content} if s.kind == "agent" else
            {"kind": "gate", "key": s.key, "title": s.title, "description": s.description,
             "reviews": list(s.reviews)} for s in workflow.stages]


STANDARD_DESCRIPTION = ("AS-IS → deep dive → design → cross-domain integration → module → microservice → "
                        "tests → modernisation → integration, with four approval gates.")


def _shape(steps: list[dict]) -> list[tuple]:
    return [(st["kind"], st["key"], tuple(st.get("reviews") or ())) for st in steps]


# The standard workflow as first released, before the cross-domain integration design step.
_STANDARD_V1 = [(k, key, ("boundary_design",) if key == "gate_design" else r)
                for k, key, r in _shape(steps_of(STANDARD)) if key != "integration_design"]


def seed() -> None:
    with session_scope() as s:
        if s.scalar(select(WorkflowDef).limit(1)) is None:
            s.add(WorkflowDef(key=STANDARD_KEY, name="Standard migration", steps=steps_of(STANDARD),
                              description=STANDARD_DESCRIPTION))
            s.add(Setting(key="_standard_workflow_v2", value=True))
        elif s.get(Setting, "_standard_workflow_v2") is None:
            # Upgrade the standard workflow only if nobody has changed it; customised ones are left alone.
            standard = s.get(WorkflowDef, STANDARD_KEY)
            if standard is not None and _shape(standard.steps) == _STANDARD_V1:
                standard.steps, standard.description = steps_of(STANDARD), STANDARD_DESCRIPTION
            s.add(Setting(key="_standard_workflow_v2", value=True))
        if s.get(Setting, "_added_discovery_workflows") is None:
            for scope, (key, name, description, workflow) in DISCOVERY.items():
                if s.get(WorkflowDef, key) is None:
                    s.add(WorkflowDef(key=key, name=name, description=description, steps=steps_of(workflow),
                                      scope=scope))
            s.add(Setting(key="_added_discovery_workflows", value=True))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def validate(steps: list[dict]) -> list[dict]:
    """Check a workflow's steps and return them normalised. Gates without a key get one from their title."""
    agents = agent_store.titles()
    out: list[dict] = []
    keys: set[str] = set()
    agents_so_far: list[str] = []
    for i, step in enumerate(steps, 1):
        kind = step.get("kind")
        if kind == "agent":
            key = step.get("key")
            if key not in agents:
                raise WorkflowInvalid(f"Step {i}: unknown agent {key!r}")
            if key in agents_so_far:
                raise WorkflowInvalid(f"Step {i}: agent {agents[key][0]!r} is already in this workflow")
            uses = step.get("uses")
            if uses is not None:
                uses = list(dict.fromkeys(uses))
                late = [u for u in uses if u not in agents_so_far]
                if late:
                    raise WorkflowInvalid(f"Step {i}: {agents[key][0]!r} can only use the output of agent steps "
                                          f"before it (not {', '.join(late)})")
            agents_so_far.append(key)
            keys.add(key)
            out.append({"kind": "agent", "key": key, "uses": uses,
                        "include_content": bool(step.get("include_content"))})
        elif kind == "gate":
            title = (step.get("title") or "").strip()
            if not title:
                raise WorkflowInvalid(f"Step {i}: an approval gate needs a title")
            key = step.get("key") or ""
            if not key:
                base = key = "gate_" + (_slug(title) or "approval")
                n = 2
                while key in keys or key in agents or any(s.get("key") == key for s in steps):
                    key, n = f"{base}_{n}", n + 1
            if not _GATE_KEY.match(key) or key in agents or key in keys:
                raise WorkflowInvalid(f"Step {i}: gate key {key!r} is invalid or already used")
            reviews = list(dict.fromkeys(step.get("reviews") or []))
            if not reviews:
                raise WorkflowInvalid(f"Step {i}: approval gate {title!r} must review at least one earlier agent step")
            late = [r for r in reviews if r not in agents_so_far]
            if late:
                raise WorkflowInvalid(f"Step {i}: approval gate {title!r} can only review agent steps before it "
                                      f"(not {', '.join(late)})")
            keys.add(key)
            out.append({"kind": "gate", "key": key, "title": title,
                        "description": (step.get("description") or "").strip(), "reviews": reviews})
        else:
            raise WorkflowInvalid(f"Step {i}: kind must be 'agent' or 'gate'")
    if not agents_so_far:
        raise WorkflowInvalid("A workflow needs at least one agent step")
    return out


def resolve(steps: list[dict]) -> Workflow:
    """Stage definitions for a workflow's steps, with agent titles from the library."""
    agents = agent_store.titles()
    stages = []
    for step in steps:
        if step["kind"] == "agent":
            title, description = agents.get(step["key"], (step["key"], ""))
            uses = step.get("uses")
            stages.append(StageDef(step["key"], title, "agent", description, uses=None if uses is None else tuple(uses),
                                   include_content=bool(step.get("include_content"))))
        else:
            stages.append(StageDef(step["key"], step["title"], "gate", step.get("description", ""),
                                   tuple(step["reviews"])))
    return Workflow(stages)


def _as_dict(row: WorkflowDef) -> dict[str, Any]:
    return {"key": row.key, "name": row.name, "description": row.description, "steps": row.steps,
            "scope": row.scope or "operation",
            "stages": resolve(row.steps).to_dicts(), "created_at": row.created_at, "updated_at": row.updated_at}


def list_all() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [_as_dict(r) for r in s.scalars(select(WorkflowDef).order_by(WorkflowDef.created_at))]


def get(key: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(WorkflowDef, key)
        if row is None:
            raise KeyError(key)
        return _as_dict(row)


def create(key: str, name: str, description: str, steps: list[dict], scope: str = "operation") -> dict[str, Any]:
    steps = validate(steps)
    with session_scope() as s:
        if s.get(WorkflowDef, key) is not None:
            raise WorkflowInvalid(f"A workflow with key {key} already exists")
        s.add(WorkflowDef(key=key, name=name, description=description, steps=steps,
                          scope=None if scope == "operation" else scope))
    return get(key)


def update(key: str, values: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(WorkflowDef, key)
        if row is None:
            raise KeyError(key)
        if values.get("steps") is not None:
            row.steps = validate(values["steps"])
        for field in ("name", "description"):
            if values.get(field) is not None:
                setattr(row, field, values[field])
    return get(key)


def delete(key: str) -> None:
    """Runs keep their own snapshot, so deleting only affects new runs. Domains defaulting to it lose the default."""
    with session_scope() as s:
        row = s.get(WorkflowDef, key)
        if row is None:
            raise KeyError(key)
        scope = row.scope or "operation"
        if sum(1 for w in s.scalars(select(WorkflowDef)) if (w.scope or "operation") == scope) == 1:
            raise WorkflowInvalid("The last migration workflow cannot be deleted" if scope == "operation" else
                                  "The last discovery workflow of its kind cannot be deleted")
        s.query(Domain).filter(Domain.default_workflow == key).update({Domain.default_workflow: None})
        s.delete(row)


def using_agent(agent_key: str) -> list[str]:
    """Names of workflows with a step for this agent."""
    with session_scope() as s:
        return [w.name for w in s.scalars(select(WorkflowDef))
                if any(st["kind"] == "agent" and st["key"] == agent_key for st in w.steps)]


def scope_of(key: str) -> str | None:
    with session_scope() as s:
        row = s.get(WorkflowDef, key)
        return None if row is None else row.scope or "operation"


def default_for(domain: Domain | None, scope: str = "operation") -> str | None:
    """The workflow a new run of this scope uses: for migrations the domain's default, otherwise the built-in one,
    otherwise the first one created."""
    with session_scope() as s:
        rows = [w for w in s.scalars(select(WorkflowDef).order_by(WorkflowDef.created_at))
                if (w.scope or "operation") == scope]
        keys = [w.key for w in rows]
        if scope == "operation" and domain is not None and domain.default_workflow in keys:
            return domain.default_workflow
        preferred = STANDARD_KEY if scope == "operation" else DISCOVERY[scope][0]
        return preferred if preferred in keys else (keys[0] if keys else None)
