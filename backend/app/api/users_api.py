"""Managing users and roles (permission: users.manage)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import auth
from app.auth import ADMIN, PERMISSIONS, CurrentUser, require_global
from app.db.models import GitCredential, Project, ProjectMember, Role, User
from app.db.session import session_scope

router = APIRouter(prefix="/api", dependencies=[Depends(require_global("users.manage"))])


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    active: bool
    roles: list[str]
    created_at: datetime
    last_login_at: datetime | None
    projects: list[dict] = []  # [{project, name, role}]: the projects they can open, with their role in each


class UserCreate(BaseModel):
    username: str = Field(max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=256)
    roles: list[str] = []


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    active: bool | None = None
    roles: list[str] | None = None


class PasswordReset(BaseModel):
    password: str = Field(max_length=256)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    name: str
    description: str
    permissions: list[str]
    locked: bool
    users: int = 0


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    permissions: list[str] = []


class RoleCreate(RoleIn):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")


def _check_roles(s, roles: list[str]) -> list[str]:
    known = set(s.scalars(select(Role.key)))
    unknown = [r for r in roles if r not in known]
    if unknown:
        raise HTTPException(422, f"Unknown role(s): {', '.join(unknown)}")
    return list(dict.fromkeys(roles))


def _check_permissions(permissions: list[str]) -> list[str]:
    unknown = [p for p in permissions if p not in PERMISSIONS]
    if unknown:
        raise HTTPException(422, f"Unknown permission(s): {', '.join(unknown)}")
    return [p for p in PERMISSIONS if p in permissions]


def _admins_remain(s) -> None:
    """Someone active must still be able to manage users."""
    for user in s.scalars(select(User).where(User.active == True)):
        if "users.manage" in auth.permissions_of(s, list(user.roles or [])):
            return
    raise HTTPException(409, "At least one active user must keep the permission to manage users")


# --- permissions ---------------------------------------------------------------------------------------------------

@router.get("/permissions")
def list_permissions() -> list[dict]:
    return [{"key": k, "label": label, "description": d} for k, (label, d) in PERMISSIONS.items()]


# --- users ---------------------------------------------------------------------------------------------------------

@router.get("/users", response_model=list[UserOut])
def list_users():
    with session_scope() as s:
        access: dict[int, list[dict]] = {}
        rows = s.execute(select(ProjectMember, Project.name).join(Project, Project.key == ProjectMember.project)
                         .order_by(Project.name)).all()
        for m, name in rows:
            access.setdefault(m.user_id, []).append({"project": m.project, "name": name, "role": m.role})
        return [UserOut.model_validate(u).model_copy(update={"projects": access.get(u.id, [])})
                for u in s.scalars(select(User).order_by(User.username))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate):
    try:
        username = auth.normalize_username(body.username)
        auth.check_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with session_scope() as s:
        if s.scalar(select(User).where(User.username == username)):
            raise HTTPException(409, f"There is already a user {username}")
        user = User(username=username, display_name=body.display_name.strip(), active=True,
                    password_hash=auth.hash_password(body.password), roles=_check_roles(s, body.roles))
        s.add(user)
        s.flush()
        return UserOut.model_validate(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, me: CurrentUser = Depends(auth.current_user)):
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(404, "User not found")
        if body.active is False and user.id == me.id:
            raise HTTPException(409, "You can't deactivate yourself")
        if body.display_name is not None:
            user.display_name = body.display_name.strip()
        if body.roles is not None:
            user.roles = _check_roles(s, body.roles)
        if body.active is not None:
            user.active = body.active
        s.flush()
        _admins_remain(s)
        out = UserOut.model_validate(user)
    if body.active is False:
        auth.end_sessions_of(user_id)
    return out


@router.put("/users/{user_id}/password", status_code=204)
def reset_password(user_id: int, body: PasswordReset, me: CurrentUser = Depends(auth.current_user)):
    try:
        auth.check_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(404, "User not found")
        user.password_hash = auth.hash_password(body.password)
    if user_id != me.id:
        auth.end_sessions_of(user_id)  # signed out everywhere; they sign in with the new password


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, me: CurrentUser = Depends(auth.current_user)):
    if user_id == me.id:
        raise HTTPException(409, "You can't delete yourself")
    auth.end_sessions_of(user_id)
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise HTTPException(404, "User not found")
        s.query(ProjectMember).filter(ProjectMember.user_id == user_id).delete()
        s.query(GitCredential).filter(GitCredential.user_id == user_id).delete()
        s.delete(user)
        s.flush()
        _admins_remain(s)


# --- roles ---------------------------------------------------------------------------------------------------------

def _role_out(s, role: Role) -> RoleOut:
    out = RoleOut.model_validate(role)
    if role.locked:
        out.permissions = list(PERMISSIONS)
    out.users = sum(1 for u in s.scalars(select(User)) if role.key in (u.roles or []))
    return out


@router.get("/roles", response_model=list[RoleOut])
def list_roles():
    with session_scope() as s:
        roles = s.scalars(select(Role)).all()
        order = {key: i for i, (key, *_) in enumerate(auth.DEFAULT_ROLES)}
        return [_role_out(s, r) for r in sorted(roles, key=lambda r: (order.get(r.key, 99), r.name.lower()))]


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(body: RoleCreate):
    with session_scope() as s:
        if s.get(Role, body.key):
            raise HTTPException(409, f"There is already a role {body.key}")
        role = Role(key=body.key, name=body.name.strip(), description=body.description.strip(),
                    permissions=_check_permissions(body.permissions), locked=False)
        s.add(role)
        s.flush()
        return _role_out(s, role)


@router.put("/roles/{key}", response_model=RoleOut)
def update_role(key: str, body: RoleIn):
    with session_scope() as s:
        role = s.get(Role, key)
        if role is None:
            raise HTTPException(404, "Role not found")
        role.name, role.description = body.name.strip(), body.description.strip()
        if not role.locked:  # Admin always has every permission
            role.permissions = _check_permissions(body.permissions)
        s.flush()
        _admins_remain(s)
        return _role_out(s, role)


@router.delete("/roles/{key}", status_code=204)
def delete_role(key: str):
    with session_scope() as s:
        role = s.get(Role, key)
        if role is None:
            raise HTTPException(404, "Role not found")
        if role.locked or key == ADMIN:
            raise HTTPException(409, "The Admin role can't be deleted")
        for user in s.scalars(select(User)):
            if key in (user.roles or []):
                user.roles = [r for r in user.roles if r != key]
        in_use = s.scalars(select(ProjectMember.project).where(ProjectMember.role == key).limit(1)).first()
        if in_use:
            raise HTTPException(409, f"Members of project {in_use} have this role. Give them another role first.")
        s.delete(role)
        s.flush()
        _admins_remain(s)
