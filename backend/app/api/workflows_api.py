from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import projects, workflow_store
from app.api.schemas import WorkflowCreate, WorkflowIn, WorkflowOut
from app.auth import ProjectAccess, project_context, require, sees_all_projects
from app.db.models import Run
from app.db.session import get_db
from app.workflow_store import WorkflowInvalid

router = APIRouter(prefix="/api/workflows")


Library = Literal["shared", "project"]


def _with_runs(db: Session, workflow: dict) -> dict:
    return {**workflow, "runs": db.scalar(select(func.count()).where(Run.project == projects.current(),
                                                                     Run.workflow == workflow["key"]))}


def _check_library(library: Library, access: ProjectAccess) -> None:
    """The shared workflows are used by every project; changing them needs Manage workflows from the user's own
    roles."""
    if library == "shared" and not access.user.can("workflows.manage"):
        raise HTTPException(403, "Changing the shared workflow affects every project. You can customise it for this "
                                 "project instead.")


@router.get("/browse", response_model=list[WorkflowOut])
def browse_workflows(of: str, access: ProjectAccess = Depends(project_context)):
    """The workflows a project uses (`of` = its key), or every workflow of the projects the user can open
    (`of` = "all"), each with the project it belongs to: to look through them from any project. Editing one still
    happens in its own project."""
    visible = [p["key"] for p in projects.visible(access.user.id, sees_all_projects(access.user))]
    if of != "all" and of not in visible:
        raise HTTPException(404, f"There is no project {of} you can open")
    return workflow_store.browse(of, visible)


@router.get("", response_model=list[WorkflowOut])
def list_workflows(db: Session = Depends(get_db)):
    return [_with_runs(db, w) for w in workflow_store.list_all()]


@router.post("", response_model=WorkflowOut, status_code=201, dependencies=[Depends(require("workflows.manage"))])
def create_workflow(body: WorkflowCreate, library: Library = "project", db: Session = Depends(get_db),
                    access: ProjectAccess = Depends(project_context)):
    _check_library(library, access)
    scope = body.scope
    if body.copy_from:
        try:
            source = workflow_store.get(body.copy_from)
        except KeyError:
            raise HTTPException(404, f"Workflow {body.copy_from} not found")
        steps, scope = source["steps"], source["scope"]  # a copy covers what the original covers
        git = source["git"]
    else:
        steps, git = [s.model_dump() for s in body.steps or []], None
    try:
        return _with_runs(db, workflow_store.create(body.key, body.name, body.description, steps, scope, library,
                                                    git))
    except WorkflowInvalid as e:
        raise HTTPException(422, str(e))


@router.get("/{key}", response_model=WorkflowOut, dependencies=[Depends(require("workflows.manage"))])
def get_workflow(key: str, db: Session = Depends(get_db)):
    try:
        return _with_runs(db, workflow_store.get(key))
    except KeyError:
        raise HTTPException(404, "Workflow not found")


@router.put("/{key}", response_model=WorkflowOut, dependencies=[Depends(require("workflows.manage"))])
def update_workflow(key: str, body: WorkflowIn, library: Library = "project", db: Session = Depends(get_db),
                    access: ProjectAccess = Depends(project_context)):
    """Applies to runs started afterwards; runs in progress keep the workflow they started with. library=project
    changes this project's copy (made from the shared workflow if needed)."""
    _check_library(library, access)
    values = body.model_dump(exclude_none=True)
    if body.steps is not None:
        values["steps"] = [s.model_dump() for s in body.steps]
    try:
        return _with_runs(db, workflow_store.update(key, values, library))
    except KeyError:
        raise HTTPException(404, "Workflow not found")
    except WorkflowInvalid as e:
        raise HTTPException(422, str(e))


@router.post("/{key}/revert", response_model=WorkflowOut, dependencies=[Depends(require("workflows.manage"))])
def revert_workflow(key: str, db: Session = Depends(get_db)):
    """Drop this project's copy and use the shared workflow again."""
    try:
        return _with_runs(db, workflow_store.revert(key))
    except KeyError:
        raise HTTPException(404, "Workflow not found")
    except WorkflowInvalid as e:
        raise HTTPException(409, str(e))


@router.get("/{key}/usage")
def workflow_usage(key: str, library: Library = "project", access: ProjectAccess = Depends(project_context)) -> dict:
    """What deleting the workflow would affect, and why it can't be deleted yet when it can't (workflow_store.usage)."""
    try:
        return workflow_store.usage(key, library)
    except KeyError:
        raise HTTPException(404, "Workflow not found")


@router.delete("/{key}", status_code=204, dependencies=[Depends(require("workflows.manage"))])
def delete_workflow(key: str, library: Library = "project", access: ProjectAccess = Depends(project_context)):
    _check_library(library, access)
    try:
        workflow_store.delete(key, library)
    except KeyError:
        raise HTTPException(404, "Workflow not found")
    except WorkflowInvalid as e:
        raise HTTPException(409, str(e))
