import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import (
    AgentInputsIn, AnswerIn, ApprovalOut, ArtifactOut, ArtifactSummary, ArtifactUpdate, Decision, EventOut, QuestionOut,
    RunCreate, RunDetail, RunOut, Rerun,
)
from app.config import settings
from app.db.models import Approval, Artifact, AuditEvent, Domain, Question, Run, StageExecution
from app.db.session import emit, get_db, session_scope
from app import contracts_store, discovery_store, workflow_store
from app.domain_store import normalize_inputs
from app.orchestrator import questions
from app.orchestrator.runner import RunBusyError, get_runner
from app.tools import git_ops

router = APIRouter(prefix="/api")


def _run_or_404(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db)):
    return db.scalars(select(Run).order_by(Run.domain, Run.operation)).all()


SCOPE_LABELS = {"operation": "migration", "domain": "feature listing", "monolith": "bounded-context discovery"}


def start_run(db: Session, domain: str, operation: str, scope: str, workflow_key: str | None,
              inputs: dict | None = None) -> Run:
    """Create a run of a workflow of the given scope and start it."""
    try:
        workflow = workflow_store.get(workflow_key or "")
    except KeyError:
        raise HTTPException(422, f"Unknown workflow {workflow_key}" if workflow_key else
                                 "There is no workflow for this; add one on the Workflows page")
    if workflow["scope"] != scope:
        raise HTTPException(422, f"{workflow['name']} is not a {SCOPE_LABELS[scope]} workflow")
    active = db.scalar(select(Run).where(Run.domain == domain, Run.operation == operation,
                                         Run.status != "completed"))
    if active:
        raise HTTPException(409, f"{discovery_store.subject(domain, operation)} already has an unfinished run")
    branch = (f"migration/{domain}/{operation}" if scope == "operation" else
              "discovery/bounded-contexts" if scope == "monolith" else f"discovery/{domain}/features")
    run = Run(domain=domain, operation=operation, branch=branch, inputs=inputs, scope=scope,
              workflow=workflow["key"], workflow_stages=workflow["stages"])
    db.add(run)
    db.flush()
    run.workspace = str(settings.workspaces_dir / run.id)
    db.commit()
    emit(run.id, "status", f"Run created for {discovery_store.subject(domain, operation)} ({workflow['name']})")
    get_runner().start(run.id)
    return run


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(body: RunCreate, db: Session = Depends(get_db)):
    """A migration of one feature. The domain's features must have been listed first."""
    domain = db.get(Domain, body.domain)
    if domain is None:
        raise HTTPException(422, f"Unknown domain {body.domain}; add it on the Domains page first")
    feature = discovery_store.feature(body.domain, body.operation)
    if feature is None:
        if not discovery_store.features_of(body.domain):
            raise HTTPException(422, f"List {domain.name}'s features first: open the domain's Features tab")
        raise HTTPException(422, f"{body.operation} is not in {domain.name}'s feature list; add it on the domain's "
                                 "Features tab first")
    inputs = normalize_inputs(body.inputs.model_dump()) if body.inputs else {"paths": [], "notes": ""}
    if not inputs["paths"]:
        inputs["paths"] = feature["entry_points"]
    return start_run(db, body.domain, body.operation, "operation",
                     body.workflow or workflow_store.default_for(domain),
                     inputs if inputs["paths"] or inputs["notes"] else None)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    detail = RunDetail.model_validate(run)
    if run.status in ("awaiting_approval", "changes_requested"):
        detail.pending_gate = get_runner().pending_gate(run_id)
    return detail


@router.post("/runs/{run_id}/decision", response_model=ApprovalOut)
def decide(run_id: str, body: Decision, db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    runner = get_runner()
    gate = runner.pending_gate(run_id) if run.status == "awaiting_approval" else None
    if gate is None:
        raise HTTPException(409, "Run is not waiting for approval")
    if body.decision == "reject":
        if not body.comments:
            raise HTTPException(422, "Comments are required when rejecting")
        if body.target_stage and body.target_stage not in gate["reviews"]:
            raise HTTPException(422, f"target_stage must be one of {gate['reviews']}")
    else:
        # An approved design becomes part of the company-wide contracts catalogue before the run moves on.
        try:
            recorded = contracts_store.record_approved_design(run_id, list(gate["reviews"]))
        except contracts_store.InvalidDocument as e:
            raise HTTPException(422, f"The integration decisions can't be recorded: {e}. Edit the file or reject "
                                     "the design.")
        if recorded:
            emit(run_id, "artifact", f"Recorded {recorded} cross-domain decision(s) in the contracts catalogue",
                 gate["gate"])
        try:
            summary = discovery_store.record_approved(run_id, list(gate["reviews"]))
        except contracts_store.InvalidDocument as e:
            raise HTTPException(422, f"The discovery results can't be recorded: {e}. Edit the file or reject it.")
        if summary:
            emit(run_id, "artifact", summary, gate["gate"])

    target = (body.target_stage or gate["reviews"][0]) if body.decision == "reject" else None
    approval = Approval(run_id=run_id, gate=gate["gate"], decision=body.decision, comments=body.comments,
                        target_stage=target, reviewer=body.reviewer)
    db.add(approval)
    if body.decision == "reject" and body.rerun == "later":
        # The graph stays paused at the gate; agents and inputs are read when a stage starts, so amendments made now
        # apply to the re-run.
        run.status = "changes_requested"
        run.pending_rerun = {"gate": gate["gate"], "target_stage": target, "comments": body.comments}
        db.commit()
        emit(run_id, "approval", f"Changes requested by {body.reviewer}; {_stage_title(run, target)} re-runs once "
                                 "the amendments are made", gate["gate"])
        return approval
    db.commit()
    try:
        runner.resume(run_id, body.model_dump())
    except RunBusyError as e:
        raise HTTPException(409, str(e))
    return approval


def _stage_title(run: Run, key: str) -> str:
    return next((s["title"] for s in run.stage_defs if s["key"] == key), key)


def _held(db: Session, run_id: str) -> Run:
    run = _run_or_404(db, run_id)
    if run.status != "changes_requested" or not run.pending_rerun:
        raise HTTPException(409, "The run is not waiting for amendments")
    return run


@router.post("/runs/{run_id}/rerun", response_model=RunOut)
def rerun(run_id: str, body: Rerun, db: Session = Depends(get_db)):
    """Re-run a rejection that was held for amendments, from its target stage, with its (possibly updated)
    comments."""
    run = _held(db, run_id)
    pending = dict(run.pending_rerun)
    gate = get_runner().pending_gate(run_id)
    if body.target_stage:
        if gate is None or body.target_stage not in gate["reviews"]:
            raise HTTPException(422, f"target_stage must be one of {gate['reviews'] if gate else []}")
        pending["target_stage"] = body.target_stage
    if body.comments is not None:
        if not body.comments.strip():
            raise HTTPException(422, "Comments are required when rejecting")
        pending["comments"] = body.comments
    latest = db.scalar(select(Approval).where(Approval.run_id == run_id, Approval.gate == pending["gate"])
                       .order_by(Approval.id.desc()).limit(1))
    if latest is not None and latest.decision == "reject":
        latest.comments, latest.target_stage = pending["comments"], pending["target_stage"]
    run.status, run.pending_rerun = "running", None
    db.commit()
    emit(run_id, "approval", f"Amendments done: re-running from {_stage_title(run, pending['target_stage'])}",
         pending["gate"])
    try:
        get_runner().resume(run_id, {"decision": "reject", "target_stage": pending["target_stage"],
                                     "comments": pending["comments"]})
    except RunBusyError as e:
        raise HTTPException(409, str(e))
    return run


@router.post("/runs/{run_id}/back-to-review", response_model=RunOut)
def back_to_review(run_id: str, db: Session = Depends(get_db)):
    """Withdraw a rejection held for amendments: the gate is open for a decision again."""
    run = _held(db, run_id)
    gate = run.pending_rerun["gate"]
    latest = db.scalar(select(Approval).where(Approval.run_id == run_id, Approval.gate == gate)
                       .order_by(Approval.id.desc()).limit(1))
    if latest is not None and latest.decision == "reject":
        db.delete(latest)  # it never took effect
    run.status, run.pending_rerun = "awaiting_approval", None
    db.commit()
    emit(run_id, "approval", "Rejection withdrawn; waiting for a decision again", gate)
    return run


@router.put("/runs/{run_id}/inputs", response_model=RunDetail)
def update_inputs(run_id: str, body: AgentInputsIn, db: Session = Depends(get_db)):
    """Change the run's own paths and notes. Stages started afterwards (re-runs and retries) get the new ones."""
    run = _run_or_404(db, run_id)
    if run.status not in ("awaiting_approval", "changes_requested", "failed"):
        raise HTTPException(409, "Inputs can be changed while the run waits for approval or amendments, or failed")
    inputs = normalize_inputs(body.model_dump())
    run.inputs = inputs if inputs["paths"] or inputs["notes"] else None
    db.commit()
    emit(run_id, "status", "Run inputs updated")
    return get_run(run_id, db)


@router.post("/runs/{run_id}/retry", response_model=RunOut)
def retry(run_id: str, db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    if run.status != "failed":
        raise HTTPException(409, "Only failed runs can be retried")
    emit(run_id, "status", "Retrying")
    try:
        get_runner().retry(run_id)
    except RunBusyError as e:
        raise HTTPException(409, str(e))
    return run


@router.get("/runs/{run_id}/questions", response_model=list[QuestionOut])
def list_questions(run_id: str, db: Session = Depends(get_db)):
    _run_or_404(db, run_id)
    return db.scalars(select(Question).where(Question.run_id == run_id).order_by(Question.id)).all()


@router.post("/questions/{question_id}/answer", response_model=QuestionOut)
def answer_question(question_id: int, body: AnswerIn):
    try:
        return questions.answer(question_id, body.answer, body.answered_by)
    except KeyError:
        raise HTTPException(404, "Question not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactSummary])
def list_artifacts(run_id: str, db: Session = Depends(get_db)):
    _run_or_404(db, run_id)
    return db.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id)).all()


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    return artifact


@router.put("/artifacts/{artifact_id}", response_model=ArtifactOut)
def edit_artifact(artifact_id: int, body: ArtifactUpdate, db: Session = Depends(get_db)):
    """Manual edit before approval: written to the workspace and committed so later stages build on it."""
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    run = db.get(Run, artifact.run_id)
    if run.status not in ("awaiting_approval", "changes_requested"):
        raise HTTPException(409, "Artifacts can only be edited while the run is waiting for approval or amendments")
    ws = Path(run.workspace)
    (ws / artifact.path).write_text(body.content)
    git_ops.commit_all(ws, f"[manual edit] {artifact.path}")
    artifact.content = body.content
    db.commit()
    emit(run.id, "artifact", f"Artifact edited by reviewer: {artifact.path}", artifact.stage)
    return artifact


@router.get("/runs/{run_id}/stages/{execution_id}/diff")
def stage_diff(run_id: str, execution_id: int, db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    ex = db.get(StageExecution, execution_id)
    if ex is None or ex.run_id != run_id or not (ex.start_sha and ex.end_sha):
        raise HTTPException(404, "No diff for this stage execution")
    try:
        return {"diff": git_ops.diff(Path(run.workspace), ex.start_sha, ex.end_sha)}
    except git_ops.GitError:
        raise HTTPException(410, "This attempt was rolled back and its commits are no longer available")


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0):
    """Server-sent events: the run's audit log, then live updates."""
    with session_scope() as s:
        _run_or_404(s, run_id)

    def fetch(last_id: int) -> list[EventOut]:
        with session_scope() as s:
            rows = s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id, AuditEvent.id > last_id)
                             .order_by(AuditEvent.id).limit(500)).all()
            return [EventOut.model_validate(r) for r in rows]

    async def stream():
        last_id = after
        while not await request.is_disconnected():
            for ev in await asyncio.to_thread(fetch, last_id):
                last_id = ev.id
                yield {"id": str(ev.id), "event": "audit", "data": json.dumps(ev.model_dump(mode="json"))}
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())
