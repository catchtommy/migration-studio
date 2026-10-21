from fastapi import APIRouter

from app import custom_guardrails

router = APIRouter(prefix="/api/guardrails")


@router.get("")
def get_guardrails() -> dict:
    """What the built-in checks enforce (they live in code and are always on for the agents they belong to).
    Guardrails in plain words are kept per agent, with the agent."""
    return {"builtin": custom_guardrails.builtin()}
