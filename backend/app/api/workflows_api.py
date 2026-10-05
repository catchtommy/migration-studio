from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import workflow_store
from app.api.schemas import WorkflowCreate, WorkflowIn, WorkflowOut
from app.db.models import Run
from app.db.session import get_db
from app.workflow_store import WorkflowInvalid

router = APIRouter(prefix="/api/workflows")


def _with_runs(db: Session, workflow: dict) -> dict:
    return {**workflow, "runs": db.scalar(select(func.count()).where(Run.workflow == workflow["key"]))}


@router.get("", response_model=list[WorkflowOut])
def list_workflows(db: Session = Depends(get_db)):
    return [_with_runs(db, w) for w in workflow_store.list_all()]


@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(body: WorkflowCreate, db: Session = Depends(get_db)):
    scope = body.scope
    if body.copy_from:
        try:
            source = workflow_store.get(body.copy_from)
        except KeyError:
            raise HTTPException(404, f"Workflow {body.copy_from} not found")
        steps, scope = source["steps"], source["scope"]  # a copy covers what the original covers
    else:
        steps = [s.model_dump() for s in body.steps or []]
    try:
        return _with_runs(db, workflow_store.create(body.key, body.name, body.description, steps, scope))
    except WorkflowInvalid as e:
        raise HTTPException(422, str(e))


@router.get("/{key}", response_model=WorkflowOut)
def get_workflow(key: str, db: Session = Depends(get_db)):
    try:
        return _with_runs(db, workflow_store.get(key))
    except KeyError:
        raise HTTPException(404, "Workflow not found")


@router.put("/{key}", response_model=WorkflowOut)
def update_workflow(key: str, body: WorkflowIn, db: Session = Depends(get_db)):
    """Applies to runs started afterwards; runs in progress keep the workflow they started with."""
    values = body.model_dump(exclude_none=True)
    if body.steps is not None:
        values["steps"] = [s.model_dump() for s in body.steps]
    try:
        return _with_runs(db, workflow_store.update(key, values))
    except KeyError:
        raise HTTPException(404, "Workflow not found")
    except WorkflowInvalid as e:
        raise HTTPException(422, str(e))


@router.delete("/{key}", status_code=204)
def delete_workflow(key: str):
    try:
        workflow_store.delete(key)
    except KeyError:
        raise HTTPException(404, "Workflow not found")
    except WorkflowInvalid as e:
        raise HTTPException(409, str(e))
