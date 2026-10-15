"""Signing in and out, the signed-in user, and creating the first admin."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import auth
from app.auth import COOKIE, CurrentUser, current_user
from app.db.models import User
from app.db.session import session_scope

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class BootstrapIn(BaseModel):
    username: str = Field(max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def me_out(user: CurrentUser) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "roles": list(user.roles),
            "permissions": sorted(user.permissions)}


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=auth.SESSION_HOURS * 3600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                        path="/")


@router.get("/me")
def me(request: Request) -> dict:
    """The signed-in user, or who must act first: {"user": null, "needs_bootstrap": true} before any user exists."""
    user = auth.user_for_token(request.cookies.get(COOKIE))
    return {"user": me_out(user) if user else None, "needs_bootstrap": user is None and auth.needs_bootstrap()}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    username = body.username.strip().lower()
    if auth.throttled(username):
        raise HTTPException(429, "Too many failed attempts; try again in 15 minutes")
    with session_scope() as s:
        user = s.scalar(select(User).where(User.username == username))
        ok = user is not None and user.active and auth.verify_password(body.password, user.password_hash)
        user_id = user.id if ok else None
    if not ok:
        auth.record_failure(username)
        raise HTTPException(401, "Wrong username or password")
    auth.clear_failures(username)
    _set_cookie(request, response, auth.start_session(user_id))
    return {"user": me_out(_fresh(user_id))}


def _fresh(user_id: int) -> CurrentUser:
    with session_scope() as s:
        user = s.get(User, user_id)
        roles = list(user.roles or [])
        return CurrentUser(id=user.id, username=user.username, display_name=user.display_name, roles=tuple(roles),
                           permissions=frozenset(auth.permissions_of(s, roles)))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    auth.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")


@router.post("/bootstrap")
def bootstrap(body: BootstrapIn, request: Request, response: Response) -> dict:
    """Create the first user, an admin. Only while there are no users."""
    if not auth.needs_bootstrap():
        raise HTTPException(409, "The first admin has already been created; sign in instead")
    try:
        username = auth.normalize_username(body.username)
        auth.check_password(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with session_scope() as s:
        if s.scalar(select(User.id).limit(1)) is not None:
            raise HTTPException(409, "The first admin has already been created; sign in instead")
        user = User(username=username, display_name=body.display_name.strip(),
                    password_hash=auth.hash_password(body.password), active=True, roles=[auth.ADMIN])
        s.add(user)
        s.flush()
        user_id = user.id
    _set_cookie(request, response, auth.start_session(user_id))
    return {"user": me_out(_fresh(user_id))}


@router.put("/password", status_code=204)
def change_password(body: PasswordChange, user: CurrentUser = Depends(current_user)) -> None:
    try:
        auth.check_password(body.new_password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with session_scope() as s:
        row = s.get(User, user.id)
        if not auth.verify_password(body.current_password, row.password_hash):
            raise HTTPException(422, "The current password is wrong")
        row.password_hash = auth.hash_password(body.new_password)
