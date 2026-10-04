"""GitHub Copilot account: sign-in status, browser sign-in, token sign-in, sign-out and available models."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.copilot_service import get_service

router = APIRouter(prefix="/api/copilot")


class LoginBegin(BaseModel):
    kind: Literal["github", "entra"] = "github"  # github: the GitHub host from Settings


class LoginAdvance(BaseModel):
    input: str | None = None  # answers an input-required step, e.g. a GitHub Enterprise Cloud host


class TokenLogin(BaseModel):
    token: str = Field(min_length=1)
    host: str = ""  # defaults to the GitHub host from Settings


def _fail(e: Exception) -> HTTPException:
    return HTTPException(502, f"GitHub Copilot: {e}")


@router.get("/status")
def status():
    return get_service().status()


@router.get("/models")
def models():
    return get_service().models()


@router.post("/login")
def login_begin(body: LoginBegin):
    try:
        return get_service().login_begin(body.kind)
    except Exception as e:
        raise _fail(e)


@router.post("/login/{flow_id}/advance")
def login_advance(flow_id: str, body: LoginAdvance):
    """Moves the browser sign-in on; waits while the person completes it in their browser."""
    try:
        return get_service().login_advance(flow_id, body.input)
    except Exception as e:
        raise _fail(e)


@router.delete("/login/{flow_id}", status_code=204)
def login_cancel(flow_id: str):
    try:
        get_service().login_cancel(flow_id)
    except Exception as e:
        raise _fail(e)


@router.post("/token")
def login_with_token(body: TokenLogin):
    try:
        get_service().login_with_token(body.token.strip(), body.host.strip())
    except Exception as e:
        raise _fail(e)
    return get_service().status()


@router.post("/logout")
def logout():
    try:
        get_service().logout()
    except Exception as e:
        raise _fail(e)
    return get_service().status()
