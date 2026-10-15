"""Projects and the project a piece of work belongs to.

API requests name their project (X-Project header, or `project` query parameter where headers can't be sent); the
dependency in app/auth.py checks access and sets it as the current project. Run workers set their run's project.
Stores read it with current() so project data is always filtered by project.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import func, select

from app.db.models import GLOBAL, SHARED, Project, ProjectMember, Run, User
from app.db.session import session_scope

DEFAULT = "default"
KEY = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
RESERVED = {GLOBAL, SHARED}

_current: ContextVar[str | None] = ContextVar("project", default=None)


class NoProject(RuntimeError):
    pass


def current() -> str:
    project = _current.get()
    if project is None:
        raise NoProject("No project chosen")
    return project


def current_or_none() -> str | None:
    return _current.get()


def set_current(project: str | None):
    return _current.set(project)


@contextmanager
def use(project: str) -> Iterator[None]:
    token = _current.set(project)
    try:
        yield
    finally:
        _current.reset(token)


def seed() -> None:
    """Data from before projects lives in the default project; a new installation starts with it too. Users who
    existed before projects become its members, with their first role (they keep their roles everywhere)."""
    with session_scope() as s:
        if s.scalar(select(Project.key).limit(1)) is None:
            s.add(Project(key=DEFAULT, name="Default project", description=""))
            for user in s.scalars(select(User)):
                s.add(ProjectMember(project=DEFAULT, user_id=user.id, role=(user.roles or ["viewer"])[0]))


def exists(key: str) -> bool:
    with session_scope() as s:
        return s.get(Project, key) is not None


def as_dict(p: Project, runs: int = 0, members: int = 0, role: str | None = None) -> dict:
    return {"key": p.key, "name": p.name, "description": p.description, "created_at": p.created_at,
            "updated_at": p.updated_at, "runs": runs, "members": members, "my_role": role,
            "member": role is not None}


def visible(user_id: int, sees_all: bool) -> list[dict]:
    """The projects a user can open (their memberships), plus every other one for global admins (Manage projects), who
    can manage its settings and members but not open its runs."""
    with session_scope() as s:
        runs = dict(s.execute(select(Run.project, func.count()).group_by(Run.project)).all())
        members = dict(s.execute(select(ProjectMember.project, func.count()).group_by(ProjectMember.project)).all())
        mine = {m.project: m.role for m in s.scalars(select(ProjectMember).where(ProjectMember.user_id == user_id))}
        rows = s.scalars(select(Project).order_by(Project.name)).all()
        return [as_dict(p, runs.get(p.key, 0), members.get(p.key, 0), mine.get(p.key)) for p in rows
                if sees_all or p.key in mine]


def role_of(user_id: int, project: str) -> str | None:
    with session_scope() as s:
        m = s.get(ProjectMember, (project, user_id))
        return m.role if m else None


def members(project: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(ProjectMember, User).join(User, User.id == ProjectMember.user_id)
                         .where(ProjectMember.project == project).order_by(User.display_name)).all()
        return [{"user_id": u.id, "username": u.username, "display_name": u.display_name, "active": u.active,
                 "role": m.role, "created_at": m.created_at} for m, u in rows]
