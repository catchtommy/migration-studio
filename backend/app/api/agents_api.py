from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent_store, workflow_store
from app import settings_store
from app.agents.defaults import AUTO, CLAUDE_MODELS, EFFORTS, TOOLS
from app.copilot_service import get_service
from app.api.schemas import AgentCatalog, AgentCreate, AgentIn, AgentOut
from app.db.models import Run
from app.db.session import get_db

router = APIRouter(prefix="/api/agents")


@router.get("", response_model=list[AgentOut])
def list_agents():
    return agent_store.list_all()


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(body: AgentCreate):
    try:
        return agent_store.create(body.key, body.title, body.description)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/catalog", response_model=AgentCatalog)
def catalog():
    """Choices offered by the agent editor. Copilot models depend on the signed-in account."""
    provider = settings_store.get("ai_provider")
    models = [m["id"] for m in get_service().models()] if provider == "copilot" else list(CLAUDE_MODELS)
    return {"provider": provider, "models": [AUTO, *models], "efforts": list(EFFORTS),
            "tools": [{"name": k, "description": v} for k, v in TOOLS.items()]}


@router.get("/{key}", response_model=AgentOut)
def get_agent(key: str):
    try:
        return agent_store.get(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.put("/{key}", response_model=AgentOut)
def update_agent(key: str, body: AgentIn):
    try:
        return agent_store.update(key, body.model_dump())
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.post("/{key}/reset", response_model=AgentOut)
def reset_agent(key: str):
    try:
        return agent_store.reset(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.delete("/{key}", status_code=204)
def delete_agent(key: str, db: Session = Depends(get_db)):
    if workflows := workflow_store.using_agent(key):
        raise HTTPException(409, f"Remove this agent from these workflows first: {', '.join(workflows)}")
    unfinished = [r for r in db.scalars(select(Run).where(Run.status != "completed"))
                  if any(st["key"] == key for st in r.stage_defs)]
    if unfinished:
        raise HTTPException(409, f"{len(unfinished)} unfinished run(s) still use this agent")
    try:
        agent_store.delete(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
