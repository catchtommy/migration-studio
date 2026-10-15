"""Who is signed in and what they may do.

Users sign in with a username and password and get a session cookie. Each user has roles; a role is a set of the
permissions below. Every API route needs a signed-in user; routes that change things also need a permission.
"""

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select

from app import projects
from app.db.models import Project, Role, User, UserSession, utcnow
from app.db.session import session_scope

PERMISSIONS: dict[str, tuple[str, str]] = {
    "agents.manage": ("Manage agents", "Create, edit, reset and delete agents"),
    "workflows.manage": ("Manage workflows", "Create, edit and delete workflows"),
    "domains.manage": ("Manage domains & features", "Domains, feature lists, contracts, and discovery runs"),
    "runs.start": ("Run migrations", "Start migration runs and retry failed ones"),
    "runs.approve": ("Approve workflow runs", "Approve or reject runs at their gates"),
    "runs.edit": ("Edit documents & answer agents", "Edit run documents and inputs, answer agents' questions"),
    "project.manage": ("Manage project", "The project's settings (repository, stack, toolchains) and its members"),
    "settings.manage": ("Manage global settings & database", "AI provider, network, Copilot sign-in, database"),
    "users.manage": ("Manage users & roles", "Add users, reset passwords, assign roles, edit roles"),
    "projects.manage": ("Manage projects", "Create and delete projects, and give people access to any project"),
}
# Permissions that only mean something globally: given by a user's own roles, never by a project role.
GLOBAL_ONLY = {"settings.manage", "users.manage", "projects.manage"}
ADMIN = "admin"
DEFAULT_ROLES = [
    (ADMIN, "Admin", "Everything, including users and settings.", list(PERMISSIONS)),
    ("architect", "Architect", "Designs the migration: agents, workflows, domains; runs and approves.",
     ["agents.manage", "workflows.manage", "domains.manage", "runs.start", "runs.approve", "runs.edit"]),
    ("developer", "Developer", "Runs migrations and works on their documents.", ["runs.start", "runs.edit"]),
    ("reviewer", "Reviewer", "Reviews runs at their gates.", ["runs.approve", "runs.edit"]),
    ("viewer", "Viewer", "Sees everything, changes nothing.", []),
]

COOKIE = "studio_session"
SESSION_HOURS = 12
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@-]{1,63}$")
MIN_PASSWORD = 8


# --- passwords -----------------------------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                   dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters")


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME.match(name):
        raise ValueError("Usernames are 2–64 characters: letters, digits, and . _ @ - (starting with a letter or digit)")
    return name


# --- roles ---------------------------------------------------------------------------------------------------------

def seed() -> None:
    with session_scope() as s:
        if s.scalar(select(Role).limit(1)) is None:
            for key, name, description, permissions in DEFAULT_ROLES:
                s.add(Role(key=key, name=name, description=description, permissions=permissions, locked=key == ADMIN))


def permissions_of(s, role_keys: list[str]) -> set[str]:
    if ADMIN in role_keys:
        return set(PERMISSIONS)
    roles = s.scalars(select(Role).where(Role.key.in_(role_keys))).all() if role_keys else []
    return {p for r in roles for p in (r.permissions or []) if p in PERMISSIONS}


def needs_bootstrap() -> bool:
    """No one can sign in yet: the first visitor creates the first admin."""
    with session_scope() as s:
        return s.scalar(select(User.id).limit(1)) is None


# --- sessions ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with session_scope() as s:
        s.add(UserSession(token_hash=_hash(token), user_id=user_id,
                          expires_at=utcnow() + timedelta(hours=SESSION_HOURS)))
        user = s.get(User, user_id)
        user.last_login_at = utcnow()
        s.execute(delete(UserSession).where(UserSession.expires_at < utcnow()))  # tidy up old ones
    return token


def end_session(token: str | None) -> None:
    if token:
        with session_scope() as s:
            s.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


def end_sessions_of(user_id: int) -> None:
    with session_scope() as s:
        s.execute(delete(UserSession).where(UserSession.user_id == user_id))


def user_for_token(token: str | None) -> CurrentUser | None:
    if not token:
        return None
    with session_scope() as s:
        session = s.get(UserSession, _hash(token))
        if session is None or session.expires_at < utcnow():
            return None
        user = s.get(User, session.user_id)
        if user is None or not user.active:
            return None
        # Sliding expiry, written at most every half session so polling pages don't write on every request.
        if session.expires_at - utcnow() < timedelta(hours=SESSION_HOURS / 2):
            session.expires_at = utcnow() + timedelta(hours=SESSION_HOURS)
        roles = list(user.roles or [])
        return CurrentUser(id=user.id, username=user.username, display_name=user.display_name, roles=tuple(roles),
                           permissions=frozenset(permissions_of(s, roles)))


# --- sign-in throttling --------------------------------------------------------------------------------------------

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
MAX_FAILURES, WINDOW = 5, 15 * 60


def throttled(username: str) -> bool:
    with _failures_lock:
        recent = [t for t in _failures.get(username, []) if time.time() - t < WINDOW]
        _failures[username] = recent
        return len(recent) >= MAX_FAILURES


def record_failure(username: str) -> None:
    with _failures_lock:
        _failures.setdefault(username, []).append(time.time())


def clear_failures(username: str) -> None:
    with _failures_lock:
        _failures.pop(username, None)


# --- FastAPI dependencies ------------------------------------------------------------------------------------------

def current_user(request: Request) -> CurrentUser:
    user = user_for_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in to continue")
    request.state.user = user
    return user


def sees_all_projects(user: CurrentUser) -> bool:
    """Global project admins see every project, to manage its settings and members; its data still needs membership."""
    return user.can("projects.manage")


def project_permissions(user: CurrentUser, project: str) -> frozenset[str] | None:
    """What the user may do in a project: exactly what their role there gives. None: they aren't a member, so they
    can't open the project or its runs (their own roles never reach into a project)."""
    role = projects.role_of(user.id, project)
    if role is None:
        return None
    with session_scope() as s:
        return frozenset(permissions_of(s, [role]) - GLOBAL_ONLY)


def administers(user: CurrentUser, project: str) -> bool:
    """May change the project's settings and members: Manage project there, or Manage projects globally."""
    return sees_all_projects(user) or "project.manage" in (project_permissions(user, project) or ())


@dataclass(frozen=True)
class ProjectAccess:
    user: CurrentUser
    project: str
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


async def project_context(request: Request, user: CurrentUser = Depends(current_user)) -> ProjectAccess:
    """The project a request is about (X-Project header or `project` query parameter), checked and made current.
    Async on purpose: it then runs in the request's own context, so the current project it sets reaches the endpoint
    (sync dependencies run in a copy of the context)."""
    key = request.headers.get("x-project") or request.query_params.get("project")
    if not key:
        raise HTTPException(400, "Choose a project first")
    with session_scope() as s:
        if s.get(Project, key) is None:
            raise HTTPException(404, f"There is no project {key}")
    permissions = project_permissions(user, key)
    if permissions is None:
        raise HTTPException(403, "You don't have access to this project. Ask its admin to add you.")
    projects.set_current(key)  # for this request (each request runs in its own context)
    return ProjectAccess(user=user, project=key, permissions=permissions)


def _refuse(permission: str) -> HTTPException:
    return HTTPException(403, f"You don't have permission to do this ({PERMISSIONS[permission][0]}). Ask an admin "
                              "for a role that includes it.")


def require(permission: str):
    """A permission in the request's project, from the user's role in it."""

    def dependency(access: ProjectAccess = Depends(project_context)) -> CurrentUser:
        if not access.can(permission):
            raise _refuse(permission)
        return access.user

    return dependency


def require_global(permission: str):
    """A permission from the user's own roles, for things outside any project (users, global settings)."""

    def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.can(permission):
            raise _refuse(permission)
        return user

    return dependency
