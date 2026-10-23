"""Projects: listing the ones a user can open, creating them, their settings and their members."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import auth, projects, repos, settings_store
from app.auth import CurrentUser, current_user, require_admin, require_global
from app.db.models import Project, ProjectMember, Role, Run, User
from app.db.session import session_scope

router = APIRouter(prefix="/api/projects")


class ProjectCreate(BaseModel):
    key: str = Field(max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    settings: dict[str, Any] = {}  # project settings to start with (language, repository, ...)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class MemberIn(BaseModel):
    role: str


def _access(key: str, user: CurrentUser, permission: str | None = None) -> frozenset[str]:
    """The user's permissions in a project (404/403 otherwise); the project becomes current. Members get their role's
    permissions; global project admins who aren't members get Manage project only (settings and members, no runs)."""
    if not projects.exists(key):
        raise HTTPException(404, f"There is no project {key}")
    permissions = auth.project_permissions(user, key)
    if permissions is None and auth.sees_all_projects(user):
        permissions = frozenset({"project.manage"})
    if permissions is None:
        raise HTTPException(403, "You don't have access to this project. Ask its admin to add you.")
    if permission and permission not in permissions:
        raise HTTPException(403, f"You don't have permission to do this ({auth.PERMISSIONS[permission][0]}).")
    projects.set_current(key)
    return permissions


def _with_permissions(item: dict, user: CurrentUser) -> dict:
    permissions = auth.project_permissions(user, item["key"])
    if permissions is None:  # a global project admin looking at a project they aren't in
        permissions = {"project.manage"}
    return {**item, "permissions": sorted(permissions)}


@router.get("")
def list_projects(user: CurrentUser = Depends(current_user)) -> list[dict]:
    """The projects this user can open, each with what they may do in it."""
    return [_with_permissions(p, user) for p in projects.visible(user.id, auth.sees_all_projects(user))]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, user: CurrentUser = Depends(require_global("projects.manage"))) -> dict:
    key = body.key.strip().lower()
    if not projects.KEY.match(key) or key in projects.RESERVED:
        raise HTTPException(422, "Project keys are 2–64 lowercase letters, digits and dashes, starting with a letter")
    with session_scope() as s:
        if s.get(Project, key) is not None:
            raise HTTPException(409, f"There is already a project {key}")
        s.add(Project(key=key, name=body.name.strip(), description=body.description.strip()))
        s.add(ProjectMember(project=key, user_id=user.id, role=auth.ADMIN))  # the creator runs it
    if body.settings:
        try:
            settings_store.update_project(key, body.settings)
        except KeyError as e:
            with session_scope() as s:  # don't leave a half-made project behind
                s.query(ProjectMember).filter(ProjectMember.project == key).delete()
                s.query(Project).filter(Project.key == key).delete()
            raise HTTPException(422, str(e))
        if body.settings.get("monolith_repo"):
            repos.fetch_in_background(key, user.id)  # cloned to the server now, so the first run doesn't wait for it
    return get_project(key, user)


@router.get("/{key}")
def get_project(key: str, user: CurrentUser = Depends(current_user)) -> dict:
    permissions = _access(key, user)
    with session_scope() as s:
        p = s.get(Project, key)
        runs = s.scalar(select(func.count()).where(Run.project == key))
        members = s.scalar(select(func.count()).where(ProjectMember.project == key))
        out = projects.as_dict(p, runs, members, projects.role_of(user.id, key))
    return {**out, "permissions": sorted(permissions), "settings": settings_store.get_project(key),
            "languages": settings_store.LANGUAGES}


@router.put("/{key}")
def update_project(key: str, body: ProjectUpdate, user: CurrentUser = Depends(current_user)) -> dict:
    _access(key, user, "project.manage")
    with session_scope() as s:
        p = s.get(Project, key)
        if body.name is not None:
            p.name = body.name.strip()
        if body.description is not None:
            p.description = body.description.strip()
    return get_project(key, user)


@router.delete("/{key}", status_code=204)
def delete_project(key: str, user: CurrentUser = Depends(require_admin("delete a project"))) -> None:
    """Delete a project with everything in it: its runs (stopped first if working) with their documents, approvals,
    logs and workspaces, and its domains, features, contracts, agents, workflows, settings and members. Code pushed
    and documents published to its repositories stay in git."""
    from app import run_cleanup

    _access(key, user)
    with session_scope() as s:
        if s.get(Project, key) is None:
            raise HTTPException(404, f"There is no project {key}")
        if s.scalar(select(func.count()).select_from(Project)) == 1:
            raise HTTPException(409, "The last project can't be deleted")
        run_ids = list(s.scalars(select(Run.id).where(Run.project == key)))
    try:
        run_cleanup.stop_and_delete(key, run_ids)
    except run_cleanup.StillWorking as e:
        raise HTTPException(409, str(e))
    with session_scope() as s:
        from app.db.models import Agent, Contract, Dependency, Domain, Feature, Setting, WorkflowDef
        for model in (Feature, Dependency, Contract, Domain, Agent, WorkflowDef, Setting, ProjectMember):
            s.query(model).filter(model.project == key).delete(synchronize_session=False)
        s.query(Project).filter(Project.key == key).delete()
    repos.remove(key)


# --- the monolith repository on the server -------------------------------------------------------------------------

@router.get("/{key}/repository")
def repository(key: str, user: CurrentUser = Depends(current_user)) -> dict:
    """The server's copy of the project's monolith: when it was last fetched and its base branch's latest commit."""
    _access(key, user)
    return repos.status(key)


@router.post("/{key}/repository/fetch", status_code=202)
def fetch_repository(key: str, user: CurrentUser = Depends(current_user)) -> dict:
    """Fetch the latest changes in the background; new runs start from them. Needs Manage project or Run migrations."""
    permissions = _access(key, user)
    if not permissions & {"project.manage", "runs.start"}:
        raise HTTPException(403, "Fetching the repository needs Manage project or Run migrations")
    if not settings_store.get_project(key)["monolith_repo"]:
        raise HTTPException(422, "Set the project's monolith repository first")
    repos.fetch_in_background(key, user.id)
    return repos.status(key)


@router.get("/{key}/settings")
def get_settings(key: str, user: CurrentUser = Depends(current_user)) -> dict:
    _access(key, user)
    return settings_store.get_project(key)


@router.put("/{key}/settings")
def update_settings(key: str, values: dict[str, Any], user: CurrentUser = Depends(current_user)) -> dict:
    _access(key, user, "project.manage")
    before = settings_store.get_project(key)["monolith_repo"]
    if values.get("default_workflow"):
        from app import workflow_store
        with projects.use(key):
            if workflow_store.scope_of(values["default_workflow"]) != "operation":
                raise HTTPException(422, f"{values['default_workflow']} is not a migration workflow of this project")
    try:
        saved = settings_store.update_project(key, values)
    except KeyError as e:
        raise HTTPException(422, str(e))
    if saved["monolith_repo"] and saved["monolith_repo"] != before:
        # A new repository: clone it to the server now (in the background), which also shows at once on the Project
        # page whether the URL and credentials work, and the first run doesn't wait for the clone.
        repos.fetch_in_background(key, user.id)
    return saved


# --- members -------------------------------------------------------------------------------------------------------

@router.get("/{key}/members")
def list_members(key: str, user: CurrentUser = Depends(current_user)) -> list[dict]:
    _access(key, user)
    return projects.members(key)


@router.get("/{key}/candidates")
def candidates(key: str, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """Active users who could be added, for the project's admins (who may not manage users globally)."""
    _access(key, user, "project.manage")
    with session_scope() as s:
        members = set(s.scalars(select(ProjectMember.user_id).where(ProjectMember.project == key)))
        return [{"id": u.id, "username": u.username, "display_name": u.display_name}
                for u in s.scalars(select(User).where(User.active == True).order_by(User.display_name))
                if u.id not in members]


@router.put("/{key}/members/{user_id}")
def set_member(key: str, user_id: int, body: MemberIn, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """Add a user to the project, or change their role in it."""
    _access(key, user, "project.manage")
    with session_scope() as s:
        if s.get(User, user_id) is None:
            raise HTTPException(404, "User not found")
        if s.get(Role, body.role) is None:
            raise HTTPException(422, f"Unknown role {body.role}")
        s.merge(ProjectMember(project=key, user_id=user_id, role=body.role))
    return projects.members(key)


@router.delete("/{key}/members/{user_id}", status_code=204)
def remove_member(key: str, user_id: int, user: CurrentUser = Depends(current_user)) -> None:
    _access(key, user, "project.manage")
    with session_scope() as s:
        s.query(ProjectMember).filter(ProjectMember.project == key, ProjectMember.user_id == user_id).delete()
