"""Named workflows: ordered agent steps (referencing the agent library) and human approval gates.

Like agents, workflows are shared by every project (project SHARED), and a project can have its own copy of one, or
workflows of its own.
"""

import re
from urllib.parse import urlsplit
from typing import Any

from sqlalchemy import select

from app import agent_store, projects
from app.db.models import GLOBAL, SHARED, Domain, Setting, WorkflowDef
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
             "include_content": s.include_content, "publish_docs": s.publish_docs} if s.kind == "agent" else
            {"kind": "gate", "key": s.key, "title": s.title, "description": s.description,
             "reviews": list(s.reviews)} for s in workflow.stages]


STANDARD_DESCRIPTION = ("AS-IS → deep dive → dependency finder → Modulith design (linked-domain contracts, domain, "
                        "orchestrator) → cross-domain integration → module → orchestrator (cross-domain features) → "
                        "microservice → orchestrator service (when there is an orchestrator) → acceptance tests → "
                        "modernisation → service tests → integration, with four approval gates.")


def _shape(steps: list[dict]) -> list[tuple]:
    return [(st["kind"], st["key"], tuple(st.get("reviews") or ())) for st in steps]


# Earlier releases of the standard workflow, as (kind, key, reviews). A stored workflow equal to one of these has not
# been customised, so seed() brings it up to the current STANDARD. These are literal on purpose: STANDARD changes.
_TAIL = [("agent", "modular_monolith", ()), ("agent", "microservice_scaffold", ()),
         ("agent", "independent_tests", ()),
         ("gate", "gate_tests", ("modular_monolith", "microservice_scaffold", "independent_tests")),
         ("agent", "modernise", ()), ("agent", "monolith_integration", ()),
         ("gate", "gate_release", ("modernise", "monolith_integration"))]
_HEAD = [("agent", "as_is_doc", ()), ("agent", "deep_dive", ()), ("gate", "gate_analysis", ("as_is_doc", "deep_dive"))]
# First release: no cross-domain integration design step.
_STANDARD_V1 = [*_HEAD, ("agent", "boundary_design", ()), ("gate", "gate_design", ("boundary_design",)), *_TAIL]
# Second release: boundary design and cross-domain integration design.
_STANDARD_V2 = [*_HEAD, ("agent", "boundary_design", ()), ("agent", "integration_design", ()),
                ("gate", "gate_design", ("boundary_design", "integration_design")), *_TAIL]
# Third release: dependency finder, then domain, wrapper and orchestrator design.
_STANDARD_V3 = [*_HEAD, ("agent", "domain_dependencies", ()), ("agent", "domain_design", ()),
                ("agent", "wrapper_design", ()), ("agent", "orchestrator_design", ()),
                ("agent", "integration_design", ()),
                ("gate", "gate_design", ("domain_dependencies", "domain_design", "wrapper_design",
                                         "orchestrator_design", "integration_design")), *_TAIL]


# Fourth release: linked-domain contracts instead of the wrapper; no Orchestrator in monolith step yet.
_STANDARD_V4 = [*_HEAD, ("agent", "domain_dependencies", ()), ("agent", "linked_domain_contracts", ()),
                ("agent", "domain_design", ()), ("agent", "orchestrator_design", ()), ("agent", "integration_design", ()),
                ("gate", "gate_design", ("domain_dependencies", "linked_domain_contracts", "domain_design",
                                         "orchestrator_design", "integration_design")), *_TAIL]


# Fifth release: Orchestrator in monolith after the module; no orchestrator microservice yet.
_STANDARD_V5 = [*_STANDARD_V4[:_STANDARD_V4.index(("agent", "modular_monolith", ())) + 1],
                ("agent", "monolith_orchestrator", ()), ("agent", "microservice_scaffold", ()),
                ("agent", "independent_tests", ()),
                ("gate", "gate_tests", ("modular_monolith", "monolith_orchestrator", "microservice_scaffold",
                                        "independent_tests")), *_TAIL[4:]]
# Sixth release: the orchestrator microservice; tests on the lifted service, no Service tests step yet.
_STANDARD_V6 = [*_STANDARD_V5[:_STANDARD_V5.index(("agent", "microservice_scaffold", ())) + 1],
                ("agent", "orchestrator_service", ()), ("agent", "independent_tests", ()),
                ("gate", "gate_tests", ("modular_monolith", "monolith_orchestrator", "microservice_scaffold",
                                        "orchestrator_service", "independent_tests")), *_TAIL[4:]]
# The releases a stored standard workflow (shared, or a project's copy) is brought up from, by marker.
_RELEASES = (("_standard_workflow_v2", _STANDARD_V1), ("_standard_workflow_v3", _STANDARD_V2),
             ("_standard_workflow_v4", _STANDARD_V3), ("_standard_workflow_v5", _STANDARD_V4),
             ("_standard_workflow_v6", _STANDARD_V5), ("_standard_workflow_v7", _STANDARD_V6))
# Projects' copies are upgraded from these releases on (copies older than that were never upgraded, so stay as they are).
_PROJECT_RELEASES = (("_standard_project_copies_v5", _STANDARD_V4), ("_standard_project_copies_v6", _STANDARD_V5),
                     ("_standard_project_copies_v7", _STANDARD_V6))


def _upgraded(steps: list[dict]) -> list[dict]:
    """The current STANDARD, keeping the settings of the stored steps (code, uses, publishing). A step new in this
    release that changes the same code as an earlier one (the orchestrator, after the module) gets its settings."""
    stored = {st["key"]: st for st in steps if st.get("kind") == "agent"}
    # A step that changes the same repository as another gets its code settings: new steps, and Independent tests,
    # which now writes its acceptance suite in the domain service's repository (it could only write documents before).
    inherit = {"monolith_orchestrator": "modular_monolith", "service_tests": "modernise",
               "independent_tests": "microservice_scaffold"}
    out = []
    for st in steps_of(STANDARD):
        if st["kind"] == "agent":
            own = stored.get(st["key"])
            source = own if own and own.get("code") else stored.get(inherit.get(st["key"], "")) or own
            if source and source.get("code"):
                st = {**st, "code": source["code"]}
            if st["key"] in stored:
                st = {**st, **{k: stored[st["key"]][k] for k in ("uses", "include_content", "publish_docs")
                               if k in stored[st["key"]]}}
        out.append(st)
    return out


def seed() -> None:
    with session_scope() as s:
        if s.scalar(select(WorkflowDef).limit(1)) is None:
            s.add(WorkflowDef(project=SHARED, key=STANDARD_KEY, name="Standard migration", steps=steps_of(STANDARD),
                              description=STANDARD_DESCRIPTION))
            for marker, _previous in (*_RELEASES, *_PROJECT_RELEASES):  # it is the current release already
                s.add(Setting(key=marker, value=True))
            s.add(Setting(key="_standard_publishes_as_is_docs", value=True))
        else:
            # Upgrade the standard workflow only if nobody has changed it; customised ones are left alone.
            for marker, previous in _RELEASES:
                if s.get(Setting, (GLOBAL, marker)) is None:
                    standard = s.get(WorkflowDef, (SHARED, STANDARD_KEY))
                    if standard is not None and _shape(standard.steps) == previous:
                        standard.steps, standard.description = _upgraded(standard.steps), STANDARD_DESCRIPTION
                    s.add(Setting(key=marker, value=True))
        for marker, previous in _PROJECT_RELEASES:
            if s.get(Setting, (GLOBAL, marker)) is None:
                # Projects' copies of the standard workflow whose steps are still a previous release's (only their
                # settings differ, such as the documents a step reads) get the new steps too.
                for copy in s.scalars(select(WorkflowDef).where(WorkflowDef.key == STANDARD_KEY,
                                                                WorkflowDef.project != SHARED)):
                    if _shape(copy.steps) == previous:
                        copy.steps = _upgraded(copy.steps)
                s.add(Setting(key=marker, value=True))
        if s.get(Setting, (GLOBAL, "_standard_publishes_as_is_docs")) is None:
            # The standard workflow's AS-IS steps publish their approved documents to the monolith's docs folder.
            standard = s.get(WorkflowDef, (SHARED, STANDARD_KEY))
            if standard is not None:
                standard.steps = [{**st, "publish_docs": True}
                                  if st.get("kind") == "agent" and st.get("key") in ("as_is_doc", "deep_dive")
                                  and "publish_docs" not in st else st for st in standard.steps]
            s.add(Setting(key="_standard_publishes_as_is_docs", value=True))
        if s.get(Setting, (GLOBAL, "_added_discovery_workflows")) is None:
            for scope, (key, name, description, workflow) in DISCOVERY.items():
                if s.get(WorkflowDef, (SHARED, key)) is None:
                    s.add(WorkflowDef(project=SHARED, key=key, name=name, description=description,
                                      steps=steps_of(workflow), scope=scope))
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
                        "include_content": bool(step.get("include_content")),
                        "code": _code(step.get("code"), i, agents[key][0]),
                        "publish_docs": bool(step.get("publish_docs"))})
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
    bases: dict[str, str] = {}
    for step in out:  # steps sharing a repository share its checkout, so they need the same base branch
        code = step.get("code")
        if code and code["repository"]:
            if bases.setdefault(code["repository"], code["base_branch"]) != code["base_branch"]:
                raise WorkflowInvalid(f"Steps working in {code['repository']} must use the same base branch")
    return out


def _code(code: dict | None, i: int, title: str) -> dict | None:
    """A step's code settings, cleaned: None (documents only) or {"repository", "base_branch"}."""
    if not code:
        return None
    from app.tools.git_ops import valid_branch
    repository = str(code.get("repository") or "").strip()
    base = str(code.get("base_branch") or "").strip()
    if repository.startswith("-") or any(c.isspace() for c in repository):
        raise WorkflowInvalid(f"Step {i}: {title!r} needs a git URL or path for its repository")
    if "://" in repository:
        parts = urlsplit(repository)
        if not parts.netloc or parts.path.strip("/") == "":
            raise WorkflowInvalid(f"Step {i}: {title!r}: {repository!r} isn't a complete repository URL; give the "
                                  "host and the repository, e.g. https://github.com/acme/broker-service.git")
    unknown = set(re.findall(r"{(\w+)}", repository)) - {"domain"}
    if unknown:
        raise WorkflowInvalid(f"Step {i}: unknown placeholder {{{sorted(unknown)[0]}}} in the repository; use {{domain}}")
    if base and not valid_branch(base):
        raise WorkflowInvalid(f"Step {i}: {base!r} isn't a valid base branch name")
    return {"repository": repository, "base_branch": base if repository else ""}


def resolve(steps: list[dict]) -> Workflow:
    """Stage definitions for a workflow's steps, with agent titles from the library."""
    agents = agent_store.titles()
    stages = []
    for step in steps:
        if step["kind"] == "agent":
            title, description = agents.get(step["key"], (step["key"], ""))
            uses = step.get("uses")
            stages.append(StageDef(step["key"], title, "agent", description, uses=None if uses is None else tuple(uses),
                                   include_content=bool(step.get("include_content")), code=step.get("code") or None,
                                   publish_docs=bool(step.get("publish_docs"))))
        else:
            stages.append(StageDef(step["key"], step["title"], "gate", step.get("description", ""),
                                   tuple(step["reviews"])))
    return Workflow(stages)


def _as_dict(row: WorkflowDef, customised: bool = False) -> dict[str, Any]:
    from app.delivery import git_settings
    out = {"key": row.key, "name": row.name, "description": row.description, "steps": row.steps,
           "scope": row.scope or "operation", "library": "shared" if row.project == SHARED else "project",
           "customised": customised, "stages": resolve(row.steps).to_dicts(), "created_at": row.created_at,
           "updated_at": row.updated_at}
    return {**out, "git": git_settings({"scope": out["scope"], "git": row.git})}


def _effective_rows(s, project: str | None) -> list[tuple[WorkflowDef, bool]]:
    """(row, customised) for the workflows a project uses: shared ones, replaced by its copies, plus its own."""
    rows = {r.key: r for r in s.scalars(select(WorkflowDef).where(WorkflowDef.project == SHARED))}
    shared = set(rows)
    if project:
        for r in s.scalars(select(WorkflowDef).where(WorkflowDef.project == project)):
            rows[r.key] = r
    return [(r, r.project != SHARED and r.key in shared) for r in sorted(rows.values(), key=lambda r: r.created_at)]


def _effective(s, key: str, project: str | None) -> WorkflowDef | None:
    return (s.get(WorkflowDef, (project, key)) if project else None) or s.get(WorkflowDef, (SHARED, key))


def list_all() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [_as_dict(r, c) for r, c in _effective_rows(s, projects.current_or_none())]


def get(key: str) -> dict[str, Any]:
    project = projects.current_or_none()
    with session_scope() as s:
        row = _effective(s, key, project)
        if row is None:
            raise KeyError(key)
        return _as_dict(row, row.project != SHARED and s.get(WorkflowDef, (SHARED, key)) is not None)


def _target(library: str) -> str:
    return SHARED if library == "shared" else projects.current()


def create(key: str, name: str, description: str, steps: list[dict], scope: str = "operation",
           library: str = "shared", git: dict | None = None) -> dict[str, Any]:
    steps = validate(steps)
    target = _target(library)
    with session_scope() as s:
        if s.get(WorkflowDef, (SHARED, key)) is not None or s.get(WorkflowDef, (target, key)) is not None:
            raise WorkflowInvalid(f"A workflow with key {key} already exists")
        s.add(WorkflowDef(project=target, key=key, name=name, description=description, steps=steps,
                          scope=None if scope == "operation" else scope, git=git))
    return get(key)


def update(key: str, values: dict[str, Any], library: str = "shared") -> dict[str, Any]:
    """Change the shared workflow, or the project's copy of it (made from the shared one when there is none yet)."""
    target = _target(library)
    with session_scope() as s:
        row = s.get(WorkflowDef, (target, key))
        if row is None:
            shared = s.get(WorkflowDef, (SHARED, key))
            if shared is None:
                raise KeyError(key)
            row = WorkflowDef(project=target, key=key, name=shared.name, description=shared.description,
                              steps=shared.steps, scope=shared.scope, git=shared.git)
            s.add(row)
        if values.get("steps") is not None:
            row.steps = validate(values["steps"])
        if values.get("git") is not None:
            from app.delivery import validate_git
            try:
                row.git = validate_git(values["git"])
            except ValueError as e:
                raise WorkflowInvalid(str(e)) from e
        for field in ("name", "description"):
            if values.get(field) is not None:
                setattr(row, field, values[field])
    return get(key)


def revert(key: str) -> dict[str, Any]:
    """Drop the current project's copy, going back to the shared workflow."""
    with session_scope() as s:
        if s.get(WorkflowDef, (SHARED, key)) is None:
            raise WorkflowInvalid("This workflow exists only in this project; delete it instead")
        row = s.get(WorkflowDef, (projects.current(), key))
        if row is not None:
            s.delete(row)
    return get(key)


def delete(key: str, library: str = "shared") -> None:
    """Runs keep their own snapshot, so deleting only affects new runs. Domains defaulting to it lose the default."""
    target = _target(library)
    with session_scope() as s:
        row = s.get(WorkflowDef, (target, key))
        if row is None:
            raise KeyError(key)
        scope = row.scope or "operation"
        if library == "shared":
            if sum(1 for w in s.scalars(select(WorkflowDef).where(WorkflowDef.project == SHARED))
                   if (w.scope or "operation") == scope) == 1:
                raise WorkflowInvalid("The last migration workflow cannot be deleted" if scope == "operation" else
                                      "The last discovery workflow of its kind cannot be deleted")
            s.query(Domain).filter(Domain.default_workflow == key).update({Domain.default_workflow: None})
            s.query(WorkflowDef).filter(WorkflowDef.key == key).delete(synchronize_session=False)
        else:
            if s.get(WorkflowDef, (SHARED, key)) is None:  # a project-only workflow: its domains lose the default
                s.query(Domain).filter(Domain.project == target, Domain.default_workflow == key).update(
                    {Domain.default_workflow: None})
            s.delete(row)


def using_agent(agent_key: str) -> list[str]:
    """Names of the workflows (of the current project) with a step for this agent."""
    with session_scope() as s:
        return [w.name for w, _ in _effective_rows(s, projects.current_or_none())
                if any(st["kind"] == "agent" and st["key"] == agent_key for st in w.steps)]


def scope_of(key: str) -> str | None:
    with session_scope() as s:
        row = _effective(s, key, projects.current_or_none())
        return None if row is None else row.scope or "operation"


def default_for(domain: Domain | None, scope: str = "operation") -> str | None:
    """The workflow a new run of this scope uses: for migrations the domain's default, otherwise the built-in one,
    otherwise the first one created."""
    with session_scope() as s:
        keys = [w.key for w, _ in _effective_rows(s, projects.current_or_none()) if (w.scope or "operation") == scope]
        if scope == "operation" and domain is not None and domain.default_workflow in keys:
            return domain.default_workflow
        preferred = STANDARD_KEY if scope == "operation" else DISCOVERY[scope][0]
        return preferred if preferred in keys else (keys[0] if keys else None)
