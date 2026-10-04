"""LangGraph workflow: agent stages run in order; gates pause with `interrupt()` until a human decides.
One graph is built per workflow definition (each run uses the snapshot of the workflow it started with).

A gate resumes with {"decision": "approve"} or {"decision": "reject", "target_stage": ..., "comments": ...}.
Rejection rolls the workspace back to where the target stage started and re-runs from there with the comments.
"""

from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import func, select

from app import agent_store, contracts_store
from app.agents import get_agent
from app.agents.base_agent import AgentContext, AgentInputs, PriorDocument
from app.db.models import Artifact, Domain, Run, StageExecution, utcnow
from app.db.session import emit, session_scope
from app.domain_store import normalize_inputs
from app.orchestrator import questions
from app.orchestrator.stages import StageDef, Workflow
from app.orchestrator.state import MigrationState
from app.settings_store import get_all as get_settings
from app.tools import git_ops


def _set_run(run_id: str, **fields) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        for k, v in fields.items():
            setattr(run, k, v)


def _inputs(run_id: str, stage: str) -> AgentInputs:
    with session_scope() as s:
        run = s.get(Run, run_id)
        domain = s.get(Domain, run.domain)
        run_inputs = normalize_inputs(run.inputs)
        if domain is None:
            return AgentInputs(domain_name=run.domain, run_paths=run_inputs["paths"], run_notes=run_inputs["notes"])
        stage_inputs = normalize_inputs((domain.agent_inputs or {}).get(stage))
        return AgentInputs(
            domain_name=domain.name, domain_description=domain.description, domain_context=domain.context,
            stage_paths=stage_inputs["paths"], stage_notes=stage_inputs["notes"],
            run_paths=run_inputs["paths"], run_notes=run_inputs["notes"],
        )


def _ownership() -> dict[str, dict]:
    with session_scope() as s:
        return {d.key: {"tables": d.owned_tables or [], "packages": d.owned_packages or []}
                for d in s.scalars(select(Domain))}


def _previous_artifacts(workflow: Workflow, run_id: str, stage: StageDef, ws: Path) -> list[PriorDocument]:
    """The latest attempt's documents from each earlier stage this stage uses. Contents come from the workspace,
    so reviewers' manual edits are included."""
    sources = workflow.inputs_of(stage.key)
    with session_scope() as s:
        rows = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.stage.in_(sources))).all()
        latest = {}
        for a in rows:
            latest[a.stage] = max(latest.get(a.stage, 0), a.attempt)
        docs = sorted({(a.stage, a.path) for a in rows if a.attempt == latest[a.stage]},
                      key=lambda d: (sources.index(d[0]), d[1]))
    out = []
    for source, path in docs:
        file = ws / path
        if not file.is_file():
            continue
        out.append(PriorDocument(stage=source, stage_title=workflow.by_key[source].title, path=path,
                                 content=file.read_text(errors="replace") if stage.include_content else None))
    return out


def make_agent_node(workflow: Workflow, stage: StageDef):
    def node(state: MigrationState) -> dict:
        run_id = state["run_id"]
        ws = Path(state["workspace"])
        feedback = state.get("feedback", {}).get(stage.key)
        settings = get_settings()

        with session_scope() as s:
            attempt = 1 + s.scalar(
                select(func.count()).where(StageExecution.run_id == run_id, StageExecution.stage == stage.key)
            )
            start_sha = git_ops.head(ws)
            execution = StageExecution(run_id=run_id, stage=stage.key, attempt=attempt, status="running",
                                       start_sha=start_sha, feedback=feedback)
            s.add(execution)
            s.flush()
            exec_id = execution.id
        _set_run(run_id, status="running", current_stage=stage.key)
        emit(run_id, "status", f"{stage.title} started (attempt {attempt})", stage.key)

        try:
            config = agent_store.get(stage.key)
        except KeyError:
            config = None
        domain = state["domain"]
        if contracts_store.export_specs(ws):
            emit(run_id, "log", f"Copied the contracts catalogue's specs to {contracts_store.CATALOGUE_DIR}/",
                 stage.key)
        ctx = AgentContext(
            run_id=run_id, domain=state["domain"], operation=state["operation"], stage=stage.key,
            attempt=attempt, workspace=ws, feedback=feedback, settings=settings,
            log=lambda msg: emit(run_id, "log", msg, stage.key),
            config=config or {}, stage_def=stage, inputs=_inputs(run_id, stage.key),
            previous_artifacts=_previous_artifacts(workflow, run_id, stage, ws),
            answered=questions.answered_for(run_id, stage.key),
            catalogue=contracts_store.catalogue_brief(domain, settings), ownership=_ownership(),
            service_dir=settings["service_dir_pattern"].format(domain=domain),
            ask=lambda question, options=None: questions.ask(run_id, stage.key, attempt, question, options),
        )
        try:
            if config is None:
                raise RuntimeError(f"Agent {stage.key!r} no longer exists in the agent library")
            outputs = get_agent(stage.key, config, settings["ai_provider"]).run(ctx)
        except Exception as e:
            git_ops.reset_hard(ws, start_sha)  # leave a clean tree for the retry
            with session_scope() as s:
                ex = s.get(StageExecution, exec_id)
                ex.status, ex.error, ex.finished_at = "failed", str(e), utcnow()
            emit(run_id, "error", f"{stage.title} failed: {e}", stage.key)
            raise

        end_sha = git_ops.commit_all(ws, f"[{stage.key}] {state['domain']}/{state['operation']} attempt {attempt}")
        with session_scope() as s:
            for out in outputs:
                s.add(Artifact(run_id=run_id, stage=stage.key, attempt=attempt, name=out.name, path=out.path,
                               kind=out.kind, content=(ws / out.path).read_text()))
            ex = s.get(StageExecution, exec_id)
            ex.status, ex.end_sha, ex.finished_at = "completed", end_sha, utcnow()
        emit(run_id, "status", f"{stage.title} completed: {len(outputs)} artifact(s)", stage.key)

        remaining_feedback = {k: v for k, v in state.get("feedback", {}).items() if k != stage.key}
        return {
            "feedback": remaining_feedback,
            "stage_shas": {**state.get("stage_shas", {}), stage.key: {"start": start_sha, "end": end_sha}},
        }

    return node


def make_gate_node(workflow: Workflow, gate: StageDef):
    after = workflow.next_stage(gate.key) or END

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
            return {"route": after}

        target = decision.get("target_stage") or gate.reviews[0]
        git_ops.reset_hard(Path(state["workspace"]), state["stage_shas"][target]["start"])
        emit(run_id, "approval", f"{gate.title}: rejected, re-running from {workflow.by_key[target].title}", gate.key)
        return {
            "route": target,
            "feedback": {**state.get("feedback", {}), target: decision.get("comments") or "Rejected by reviewer."},
        }

    return node, [*gate.reviews, after]


def build_graph(workflow: Workflow, checkpointer):
    g = StateGraph(MigrationState)
    for stage in workflow.stages:
        if stage.kind == "agent":
            g.add_node(stage.key, make_agent_node(workflow, stage))
        else:
            node, targets = make_gate_node(workflow, stage)
            g.add_node(stage.key, node)
            g.add_conditional_edges(stage.key, lambda s: s["route"], targets)

    g.add_edge(START, workflow.stages[0].key)
    for stage in workflow.stages:
        if stage.kind == "agent":
            g.add_edge(stage.key, workflow.next_stage(stage.key) or END)
    return g.compile(checkpointer=checkpointer)
