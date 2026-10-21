from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import custom_guardrails, settings_store
from app.auth import require, require_global

router = APIRouter(prefix="/api/guardrails")


class GuardrailsIn(BaseModel):
    text: str = Field(max_length=50_000)


@router.get("", dependencies=[Depends(require("agents.manage"))])
def get_guardrails() -> dict:
    """The global guardrails in plain words, and what the built-in checks enforce (read-only)."""
    return {"text": settings_store.get_global()["custom_guardrails"], "builtin": custom_guardrails.builtin()}


@router.put("", dependencies=[Depends(require_global("agents.manage"))])
def update_guardrails(body: GuardrailsIn) -> dict:
    """Change the global guardrails. Every code-changing step that starts from now on gets them."""
    try:
        settings_store.update_global({"custom_guardrails": body.text})
    except KeyError as e:
        raise HTTPException(422, str(e))
    return get_guardrails()
