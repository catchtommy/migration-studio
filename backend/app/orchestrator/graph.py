"""LangGraph workflow: agent stages run in order; gates pause with `interrupt()` until a human decides.
One graph is built per workflow definition (each run uses the snapshot of the workflow it started with).

A gate resumes with {"decision": "approve"} or {"decision": "reject", "target_stage": ..., "comments": ...}.
Rejection rolls the workspace back to where the target stage started and re-runs from there with the comments.
"""

from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import func, select

from app import agent_store, contracts_store, discovery_store, modulith_scope
from app import legacy, projects, workspace
from app.agents import get_agent
from app.agents.base_agent import AgentContext, AgentInputs, ArtifactOut, PriorDocument, docs_dir
from app.agents.common import kind
from app.db.models import Artifact, Domain, Feature, Run, StageExecution, utcnow
from app.db.session import emit, session_scope
from app.domain_store import normalize_inputs
from app.orchestrator import questions
from app.orchestrator.stages import StageDef, Workflow
from app.orchestrator.state import MigrationState
from app.settings_store import get_all as get_settings
from app.tools import git_ops

MAX_DOCUMENT_BYTES = 5_000_000


def _project_of(run_id: str) -> str:
    with session_scope() as s:
        return s.get(Run, run_id).project


def _set_run(run_id: str, **fields) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        for k, v in fields.items():
            setattr(run, k, v)


def _inputs(run_id: str, stage: str) -> AgentInputs:
    with session_scope() as s:
        run = s.get(Run, run_id)
        domain = s.get(Domain, (run.project, run.domain))
        run_inputs = normalize_inputs(run.inputs)
        label = "this operation" if (run.scope or "operation") == "operation" else "this discovery run"
        if domain is None:
            name = "Whole monolith" if run.domain == discovery_store.MONOLITH else run.domain
            return AgentInputs(domain_name=name, run_paths=run_inputs["paths"], run_notes=run_inputs["notes"],
                               run_label=label)
        stage_inputs = normalize_inputs((domain.agent_inputs or {}).get(stage))
        feature, others = None, []
        if (run.scope or "operation") == "operation":  # a migration covers one feature of the domain: say which
            rows = s.scalars(select(Feature).where(Feature.project == run.project, Feature.domain == run.domain)
                             .order_by(Feature.key)).all()
            info = lambda f: {"key": f.key, "name": f.name, "description": f.description,  # noqa: E731
                              "entry_points": f.entry_points or [], "tables": f.tables or [], "calls": f.calls or []}
            feature = next((info(f) for f in rows if f.key == run.operation), None)
            others = [info(f) for f in rows if f.key != run.operation]
        return AgentInputs(
            domain_name=domain.name, domain_description=domain.description, domain_context=domain.context,
            stage_paths=stage_inputs["paths"], stage_notes=stage_inputs["notes"],
            run_paths=run_inputs["paths"], run_notes=run_inputs["notes"], run_label=label,
            feature=feature, other_features=others,
        )


def _ownership() -> dict[str, dict]:
    with session_scope() as s:
        return {d.key: {"tables": d.owned_tables or [], "packages": d.owned_packages or []}
                for d in s.scalars(select(Domain).where(Domain.project == projects.current()))}


def _previous_artifacts(workflow: Workflow, run_id: str, stage: StageDef) -> list[PriorDocument]:
    """The latest attempt's documents from each earlier stage this stage uses. Contents come from the database, which
    holds reviewers' manual edits."""
    sources = workflow.inputs_of(stage.key)
    with session_scope() as s:
        rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(sources))
                         .order_by(Artifact.id)).all()
        latest = {}
        for a in rows:
            latest[a.stage] = max(latest.get(a.stage, 0), a.attempt)
        docs = {(a.stage, a.path): a.content for a in rows if a.attempt == latest[a.stage]}
    return [PriorDocument(stage=source, stage_title=workflow.by_key[source].title, path=path,
                          content=content if stage.include_content else None)
            for (source, path), content in sorted(docs.items(), key=lambda d: (sources.index(d[0][0]), d[0][1]))]


def _previous_outputs(run_id: str, stage: str) -> list[str]:
    """The documents of the stage's latest completed attempt, for a re-run that updates them."""
    with session_scope() as s:
        latest = s.scalar(select(func.max(StageExecution.attempt)).where(
            StageExecution.run_id == run_id, StageExecution.stage == stage, StageExecution.status == "completed"))
        if latest is None:
            return []
        return [a.path for a in s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage == stage,
                                                                 Artifact.attempt == latest).order_by(Artifact.id))]


def _documents(ws: Path, outputs: list[ArtifactOut], docs: str, before: dict[str, str]) -> list[ArtifactOut]:
    """The stage's documents: what the agent returned plus anything else it wrote or changed in the run's documents
    folder (contract specs, notes), so the database has every document."""
    found = {out.path: out for out in outputs if (ws / out.path).is_file()}
    after = workspace.snapshot_documents(ws)
    for path, digest in after.items():
        if path in found or not path.startswith(docs + "/") or before.get(path) == digest:
            continue
        f = ws / path
        if f.stat().st_size > MAX_DOCUMENT_BYTES:
            continue
        try:
            f.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # not a text document
            continue
        found[path] = ArtifactOut(name=f.name, path=path, kind=kind(path))
    return list(found.values())


def _skip(ctx: AgentContext, stage: StageDef, reason: str) -> list[ArtifactOut]:
    """Skip a Modulith design step the scope decision makes unnecessary: no agent runs, a short note is the document."""
    ctx.log(f"Skipped {stage.title}: {reason}")
    note = ctx.output_dir / f"{stage.key}.md"
    note.write_text(modulith_scope.skip_note(stage.key, stage.title, reason), encoding="utf-8")
    return [ArtifactOut(name=note.name, path=ctx.rel(note), kind="markdown")]


def _code_dir(stage: StageDef, nested: list[dict], domain: str) -> str | None:
    """Where the step changes code, relative to the workspace: the root (the monolith), or the run's checkout of the
    repository the step names (see app/delivery.py). None when the step names a repository the run has not checked
    out: it must not change the monolith instead."""
    repository = (stage.code or {}).get("repository")
    if not repository:
        return "."
    url = repository.replace("{domain}", domain)
    return next((r["path"] for r in nested if r["url"] == url), None)


def _repository_notes(nested: list[dict]) -> list[dict]:
    return [{"key": r["key"], "url": r["url"], "path": r["path"], "base_branch": r.get("base_branch")} for r in nested]


def _undo_code_changes(ws: Path, start_sha: str, nested: list[dict], nested_starts: dict[str, str]) -> list[str]:
    """Put the workspace's code (the root and the run's other repositories) back as it was when a step started,
    keeping its documents (which git ignores); returns the paths that had changed."""
    undone = []
    for prefix, path, sha in [("", ws, start_sha)] + [(r["path"] + "/", ws / r["path"], nested_starts[r["key"]])
                                                     for r in nested if r["key"] in nested_starts]:
        changed = git_ops.git(path, "diff", "--name-only", sha).splitlines()
        changed += git_ops.git(path, "ls-files", "--others", "--exclude-standard").splitlines()
        if changed or git_ops.head(path) != sha:
            git_ops.reset_hard(path, sha)
            git_ops.git(path, "clean", "-fdq")  # untracked files; ignored ones (the documents) stay
            undone += [prefix + f for f in dict.fromkeys(changed)]
    return undone


def make_agent_node(workflow: Workflow, stage: StageDef):
    def node(state: MigrationState) -> dict:
        run_id = state["run_id"]
        projects.set_current(_project_of(run_id))  # nodes may run on another thread than the runner's
        ws = Path(state["workspace"])
        feedback = state.get("feedback", {}).get(stage.key)
        refresh = stage.key in (state.get("refresh") or [])
        settings = get_settings()

        # Before the first code change in a repository, a person confirms its base branch and the feature branch.
        # (Everything before interrupt() runs again on resume, so nothing above has side effects.)
        from app import delivery
        proposal = delivery.confirmation_needed(run_id, stage.key)
        if proposal:
            answer = interrupt(proposal)  # the runner shows the run as awaiting_branches
            delivery.apply_confirmation(run_id, proposal["repository"]["key"], answer,
                                        lambda msg: emit(run_id, "log", msg, stage.key))

        workspace.exclude_nested(ws)  # documents live in the database, never in the workspace's git
        workspace.untrack_documents(ws)
        with session_scope() as s:
            attempt = 1 + s.scalar(
                select(func.count()).where(StageExecution.run_id == run_id, StageExecution.stage == stage.key)
            )
            run_row = s.get(Run, run_id)
            nested = [dict(r) for r in run_row.repositories or [] if r.get("base_sha")]
            legacy_info, started_by = run_row.legacy, run_row.started_by
            start_sha = git_ops.head(ws)
            nested_starts = workspace.nested_heads(ws, nested)  # the run's other repositories, for rollbacks
            execution = StageExecution(run_id=run_id, stage=stage.key, attempt=attempt, status="running",
                                       start_sha=start_sha, feedback=feedback, rerun=refresh or None,
                                       repos={k: {"start": v} for k, v in nested_starts.items()} or None)
            s.add(execution)
            s.flush()
            exec_id = execution.id
        _set_run(run_id, status="running", current_stage=stage.key)
        emit(run_id, "status", f"{stage.title} {'re-run, updating its documents' if refresh else 'started'} "
                               f"(attempt {attempt})", stage.key)

        try:
            config = agent_store.get(stage.key)
        except KeyError:
            config = None
        domain = state["domain"]
        code_dir = _code_dir(stage, nested, domain)
        missing = None if code_dir is not None else (stage.code or {}).get("repository", "").replace("{domain}", domain)
        code_dir = code_dir or "."
        discovery = domain == discovery_store.MONOLITH or state["operation"] == discovery_store.FEATURES_OP
        workspace.materialize_documents(run_id, ws)  # the documents in effect, from the database, for this step
        if not discovery and contracts_store.export_specs(ws):
            emit(run_id, "log", f"Copied the contracts catalogue's specs to {contracts_store.CATALOGUE_DIR}/",
                 stage.key)
        before_docs = workspace.snapshot_documents(ws)
        ctx = AgentContext(
            run_id=run_id, domain=state["domain"], operation=state["operation"], stage=stage.key,
            attempt=attempt, workspace=ws, feedback=feedback, settings=settings,
            log=lambda msg: emit(run_id, "log", msg, stage.key),
            config=config or {}, stage_def=stage, inputs=_inputs(run_id, stage.key),
            previous_artifacts=_previous_artifacts(workflow, run_id, stage),
            answered=questions.answered_for(run_id, stage.key),
            catalogue=(discovery_store.domains_brief(domain) if discovery else
                       contracts_store.catalogue_brief(domain, settings)),
            ownership=_ownership(),
            service_dir=code_dir if code_dir != "." else settings["service_dir_pattern"].format(domain=domain),
            code_dir=code_dir, repositories=_repository_notes(nested),
            legacy=legacy_info, legacy_dir=legacy.path_for(_project_of(run_id), legacy_info, started_by),
            ask=lambda question, options=None: questions.ask(run_id, stage.key, attempt, question, options),
            refresh=refresh, previous_outputs=_previous_outputs(run_id, stage.key) if refresh else [],
        )
        try:
            if config is None:
                raise RuntimeError(f"Agent {stage.key!r} no longer exists in the agent library")
            skipped = modulith_scope.skip_reason(stage.key, ctx.output_dir)
            if skipped:
                outputs = _skip(ctx, stage, skipped)
            elif missing:
                raise RuntimeError(f"{stage.title} changes code in {missing}, but this run has not checked it out, so "
                                   "it would change the wrong repository. Retry the run: it picks up the workflow's "
                                   "current settings and asks for that repository's branches")
            else:
                outputs = get_agent(stage.key, config, settings["ai_provider"]).run(ctx)
        except Exception as e:
            workspace.clear_documents(ws)
            git_ops.reset_hard(ws, start_sha)  # leave clean trees for the retry
            for r in nested:
                if r["key"] in nested_starts:
                    git_ops.reset_hard(ws / r["path"], nested_starts[r["key"]])
            with session_scope() as s:
                ex = s.get(StageExecution, exec_id)
                ex.status, ex.error, ex.finished_at = "failed", str(e), utcnow()
            emit(run_id, "error", f"{stage.title} failed: {e}", stage.key)
            raise

        if not stage.code:  # a step that writes documents only never changes code, whatever its agent did
            undone = _undo_code_changes(ws, start_sha, nested, nested_starts)
            if undone:
                emit(run_id, "log", f"{stage.title} writes documents only, so its changes to code were undone: "
                                    f"{', '.join(undone[:20])}{f' and {len(undone) - 20} more' if len(undone) > 20 else ''}",
                     stage.key)

        message = f"[{stage.key}] {discovery_store.subject(state['domain'], state['operation'])} attempt {attempt}"
        end_sha = git_ops.commit_all(ws, message)
        nested_changes = {}
        for r in nested:  # code changed in the run's other repositories: committed there, diff kept in the database
            if r["key"] not in nested_starts:
                continue
            path, start = ws / r["path"], nested_starts[r["key"]]
            end = git_ops.commit_all(path, message)
            nested_changes[r["key"]] = {"start": start, **({"end": end, "diff": git_ops.diff(path, start, end, binary=True)}
                                                          if end != start else {})}
        outputs = _documents(ws, outputs, docs_dir(domain, state["operation"]), before_docs)
        contents = {out.path: (ws / out.path).read_text(encoding="utf-8") for out in outputs}
        workspace.clear_documents(ws)  # stored in the database below; not kept on disk
        with session_scope() as s:
            for out in outputs:
                s.add(Artifact(run_id=run_id, stage=stage.key, attempt=attempt, name=out.name, path=out.path,
                               kind=out.kind, content=contents[out.path]))
            ex = s.get(StageExecution, exec_id)
            ex.status, ex.end_sha, ex.finished_at = "completed", end_sha, utcnow()
            ex.diff = git_ops.diff(ws, start_sha, end_sha, binary=True)
            ex.repos = nested_changes or None
        emit(run_id, "status", f"{stage.title} completed: {len(outputs)} artifact(s)", stage.key)

        remaining_feedback = {k: v for k, v in state.get("feedback", {}).items() if k != stage.key}
        return {
            "feedback": remaining_feedback,
            "refresh": [k for k in state.get("refresh") or [] if k != stage.key],
            "stage_shas": {**state.get("stage_shas", {}), stage.key: {"start": start_sha, "end": end_sha}},
        }

    return node


def _publishes(workflow: Workflow, gate: StageDef) -> bool:
    return any(workflow.by_key[k].publish_docs for k in gate.reviews if k in workflow.by_key)


def publish_node_key(key: str) -> str:
    return f"{key}__publish"


def make_publish_node(key: str):
    """After a gate approved steps that publish their documents (or after such a step no gate reviews): the run waits
    for a person to publish the AS-IS documents to the monolith's docs folder, or skip (see app/docs_publish.py)."""

    def node(state: MigrationState) -> dict:
        from app import docs_publish
        projects.set_current(_project_of(state["run_id"]))
        proposal = docs_publish.publish_needed(state["run_id"], key)
        if proposal:
            interrupt(proposal)  # the runner shows the run as awaiting_publish; publishing or skipping resumes it
        return {}

    return node


def make_gate_node(workflow: Workflow, gate: StageDef):
    after = workflow.next_stage(gate.key) or END
    approved_to = publish_node_key(gate.key) if _publishes(workflow, gate) else after

    def node(state: MigrationState) -> dict:
        run_id = state["run_id"]
        # Everything before interrupt() re-executes on resume, so keep it idempotent.
        with session_scope() as s:
            pending = s.scalar(select(StageExecution).where(
                StageExecution.run_id == run_id, StageExecution.stage == gate.key,
                StageExecution.status == "awaiting_approval"))
            if pending is None:
                attempt = 1 + s.scalar(select(func.count()).where(
                    StageExecution.run_id == run_id, StageExecution.stage == gate.key))
                s.add(StageExecution(run_id=run_id, stage=gate.key, attempt=attempt, status="awaiting_approval"))
                emit(run_id, "status", f"Waiting for human approval: {gate.title}", gate.key)

        decision = interrupt({"gate": gate.key, "reviews": list(gate.reviews)})

        approved = decision.get("decision") == "approve"
        with session_scope() as s:
            ex = s.scalar(select(StageExecution).where(
                StageExecution.run_id == run_id, StageExecution.stage == gate.key,
                StageExecution.status == "awaiting_approval"))
            ex.status = "approved" if approved else "rejected"
            ex.feedback = decision.get("comments")
            ex.finished_at = utcnow()

        if approved:
            emit(run_id, "approval", f"{gate.title}: approved", gate.key)
            return {"route": approved_to}

        target = decision.get("target_stage") or gate.reviews[0]
        workspace.roll_back(run_id, Path(state["workspace"]), state["stage_shas"][target]["start"], target)
        emit(run_id, "approval", f"{gate.title}: rejected, re-running from {workflow.by_key[target].title}", gate.key)
        return {
            "route": target,
            "feedback": {**state.get("feedback", {}), target: decision.get("comments") or "Rejected by reviewer."},
        }

    return node, list(dict.fromkeys([*gate.reviews, after, approved_to]))


def make_commit_node(stage: StageDef):
    """After a step that changes code: when it left changes not yet committed, the run waits for a person to review
    and commit them (or go on without committing; they're offered again later)."""

    def node(state: MigrationState) -> dict:
        from app import delivery
        projects.set_current(_project_of(state["run_id"]))
        proposal = delivery.commit_needed(state["run_id"], stage.key)
        if proposal:
            interrupt(proposal)  # the runner shows the run as awaiting_commit; committing or going on resumes it
        return {}

    return node


def commit_node_key(stage_key: str) -> str:
    return f"{stage_key}__commit"


def build_graph(workflow: Workflow, checkpointer):
    g = StateGraph(MigrationState)
    reviewed = {k for st in workflow.stages if st.kind == "gate" for k in st.reviews}
    for stage in workflow.stages:
        if stage.kind == "agent":
            g.add_node(stage.key, make_agent_node(workflow, stage))
            if stage.code:
                g.add_node(commit_node_key(stage.key), make_commit_node(stage))
            if stage.publish_docs and stage.key not in reviewed:  # no gate approves it: publish right after it
                g.add_node(publish_node_key(stage.key), make_publish_node(stage.key))
        else:
            node, targets = make_gate_node(workflow, stage)
            g.add_node(stage.key, node)
            g.add_conditional_edges(stage.key, lambda s: s["route"], targets)
            if _publishes(workflow, stage):
                g.add_node(publish_node_key(stage.key), make_publish_node(stage.key))
                g.add_edge(publish_node_key(stage.key), workflow.next_stage(stage.key) or END)

    g.add_edge(START, workflow.stages[0].key)
    for stage in workflow.stages:
        if stage.kind == "agent":
            chain = [stage.key]
            if stage.code:
                chain.append(commit_node_key(stage.key))
            if stage.publish_docs and stage.key not in reviewed:
                chain.append(publish_node_key(stage.key))
            chain.append(workflow.next_stage(stage.key) or END)
            for a, b in zip(chain, chain[1:]):
                g.add_edge(a, b)
    return g.compile(checkpointer=checkpointer)
