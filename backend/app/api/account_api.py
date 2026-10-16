"""The signed-in user's own settings: their git identity and access tokens (one per git host), used when they
fetch or push a project's repository."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import auth, git_auth, projects, repos, secret_box
from app.auth import CurrentUser, current_user
from app.db.models import GitCredential, User
from app.db.session import session_scope
from app.tools import git_ops

router = APIRouter(prefix="/api/account")


class GitIdentity(BaseModel):
    name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=255, pattern=r"^$|^[^@\s]+@[^@\s]+$")


class CredentialIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)  # e.g. github.com, or a repository URL
    username: str = Field(min_length=1, max_length=255)
    token: str = Field(min_length=1, max_length=4096)


class TestIn(BaseModel):
    project: str
    repository: str | None = None  # one of the project's repositories; default: its monolith


def _out(user_id: int) -> dict:
    with session_scope() as s:
        user = s.get(User, user_id)
        rows = s.scalars(select(GitCredential).where(GitCredential.user_id == user_id).order_by(GitCredential.host))
        return {"name": user.git_name or "", "email": user.git_email or "", "default_name": user.display_name,
                "credentials": [{"id": c.id, "host": c.host, "username": c.username, "updated_at": c.updated_at}
                                for c in rows]}


@router.get("/git")
def get_git(user: CurrentUser = Depends(current_user)) -> dict:
    """The user's git identity and the hosts they have tokens for (never the tokens)."""
    return _out(user.id)


@router.put("/git")
def set_identity(body: GitIdentity, user: CurrentUser = Depends(current_user)) -> dict:
    with session_scope() as s:
        row = s.get(User, user.id)
        row.git_name = body.name.strip() or None
        row.git_email = body.email.strip() or None
    return _out(user.id)


@router.put("/git/credentials")
def set_credential(body: CredentialIn, user: CurrentUser = Depends(current_user)) -> dict:
    """Save (or replace) the user's token for a git host."""
    host = git_auth.normalize_host(body.host)
    if not host or "/" in host or " " in host:
        raise HTTPException(422, "Enter the git host, e.g. github.com or mycompany.ghe.com")
    with session_scope() as s:
        row = s.scalar(select(GitCredential).where(GitCredential.user_id == user.id, GitCredential.host == host))
        if row is None:
            row = GitCredential(user_id=user.id, host=host, username="", token="")
            s.add(row)
        row.username, row.token = body.username.strip(), secret_box.encrypt(body.token.strip())
    return _out(user.id)


@router.delete("/git/credentials/{credential_id}")
def remove_credential(credential_id: int, user: CurrentUser = Depends(current_user)) -> dict:
    with session_scope() as s:
        s.query(GitCredential).filter(GitCredential.id == credential_id, GitCredential.user_id == user.id).delete()
    return _out(user.id)


@router.post("/git/test")
def test_access(body: TestIn, user: CurrentUser = Depends(current_user)) -> dict:
    """Check the user can reach one of a project's repositories (only those: this never contacts other URLs)."""
    if auth.project_permissions(user, body.project) is None:
        raise HTTPException(403, "You don't have access to this project")
    url = repos.project_repository(body.project)
    if body.repository and body.repository != url:
        from app import workflow_store
        with projects.use(body.project):
            known = {w["git"]["repository"] for w in workflow_store.list_all()}
        if body.repository not in known:
            raise HTTPException(422, "That isn't one of this project's repositories")
        url = body.repository
    if not url:
        raise HTTPException(422, "This project has no repository")
    env = git_auth.env_for(user.id, url)
    try:
        git_ops.git(repos.settings.data_dir, "ls-remote", "--heads", "--", url, env=env)
    except git_ops.GitError as e:
        return {"ok": False, "repository": url, "with_token": env is not None, "error": git_auth.scrub(str(e))}
    return {"ok": True, "repository": url, "with_token": env is not None, "error": None}
