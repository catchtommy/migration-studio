from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent_files, agent_store, settings_store, workflow_store
from app.agents.defaults import AUTO, CLAUDE_MODELS, EFFORTS, TOOLS
from app.copilot_service import get_service
from app.api.schemas import AgentCatalog, AgentCreate, AgentIn, AgentOut
from app.auth import ProjectAccess, project_context, require
from app.db.models import Run
from app.db.session import get_db

router = APIRouter(prefix="/api/agents")


Scope = Literal["shared", "project"]


def _visible(agent: dict, access: ProjectAccess) -> dict:
    """Other pages need agents' names and modes; only people who manage agents see their instructions."""
    return agent if access.can("agents.manage") else {**agent, "instructions": ""}


def _check_scope(scope: Scope, access: ProjectAccess) -> None:
    """The shared library is used by every project, so changing it needs Manage agents from the user's own roles."""
    if scope == "shared" and not access.user.can("agents.manage"):
        raise HTTPException(403, "Changing the shared agent affects every project. You can customise it for this "
                                 "project instead.")


@router.get("", response_model=list[AgentOut])
def list_agents(access: ProjectAccess = Depends(project_context)):
    return [_visible(a, access) for a in agent_store.list_all()]


@router.post("", response_model=AgentOut, status_code=201, dependencies=[Depends(require("agents.manage"))])
def create_agent(body: AgentCreate, scope: Scope = "shared", access: ProjectAccess = Depends(project_context)):
    _check_scope(scope, access)
    try:
        return agent_store.create(body.key, body.title, body.description, scope)
    except ValueError as e:
        raise HTTPException(409, str(e))


class AgentFile(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


@router.post("/parse-agent-md", dependencies=[Depends(require("agents.manage"))])
def parse_agent_md(body: AgentFile):
    """Read a Copilot custom agent file (*.agent.md) into agent fields, without saving anything."""
    try:
        return agent_files.parse(body.content)
    except agent_files.InvalidAgentFile as e:
        raise HTTPException(422, str(e))


@router.post("/render-agent-md", dependencies=[Depends(require("agents.manage"))])
def render_agent_md(body: AgentIn):
    """The (possibly unsaved) agent fields as agent.md text, for the agent.md editor."""
    return {"content": agent_files.render(body.model_dump())}


@router.put("/{key}/agent-md", dependencies=[Depends(require("agents.manage"))])
def save_agent_md(key: str, body: AgentFile, scope: Scope = "shared", access: ProjectAccess = Depends(project_context)):
    """Save an agent from agent.md text. Settings the text leaves out keep their current values."""
    try:
        current = agent_store.get(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    try:
        parsed = agent_files.parse(body.content)
    except agent_files.InvalidAgentFile as e:
        raise HTTPException(422, str(e))
    warnings = parsed.pop("warnings")
    try:
        fields = AgentIn.model_validate({**{f: current[f] for f in AgentIn.model_fields}, **parsed})
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()))
    _check_scope(scope, access)
    return {"agent": agent_store.update(key, fields.model_dump(), scope), "warnings": warnings}


@router.get("/{key}/agent.md", response_class=PlainTextResponse, dependencies=[Depends(require("agents.manage"))])
def export_agent_md(key: str):
    try:
        agent = agent_store.get(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    filename = f"{key.replace('_', '-')}.agent.md"
    return PlainTextResponse(agent_files.render(agent), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/catalog", response_model=AgentCatalog, dependencies=[Depends(require("agents.manage"))])
def catalog():
    """Choices offered by the agent editor. Copilot models depend on the signed-in account."""
    provider = settings_store.get("ai_provider")
    models = [m["id"] for m in get_service().models()] if provider == "copilot" else list(CLAUDE_MODELS)
    return {"provider": provider, "models": [AUTO, *models], "efforts": list(EFFORTS),
            "tools": [{"name": k, "description": v} for k, v in TOOLS.items()]}


@router.get("/{key}", response_model=AgentOut)
def get_agent(key: str, access: ProjectAccess = Depends(project_context)):
    try:
        return _visible(agent_store.get(key), access)
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.put("/{key}", response_model=AgentOut, dependencies=[Depends(require("agents.manage"))])
def update_agent(key: str, body: AgentIn, scope: Scope = "shared", access: ProjectAccess = Depends(project_context)):
    """Change the shared agent, or (scope=project) this project's copy, made from the shared one if needed."""
    _check_scope(scope, access)
    try:
        return agent_store.update(key, body.model_dump(), scope)
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.post("/{key}/customise", response_model=AgentOut, dependencies=[Depends(require("agents.manage"))])
def customise_agent(key: str):
    """Give this project its own copy of a shared agent, to change without affecting other projects."""
    try:
        return agent_store.customise(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.post("/{key}/revert", response_model=AgentOut, dependencies=[Depends(require("agents.manage"))])
def revert_agent(key: str):
    """Drop this project's copy and use the shared agent again."""
    try:
        return agent_store.revert(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/{key}/reset", response_model=AgentOut, dependencies=[Depends(require("agents.manage"))])
def reset_agent(key: str, access: ProjectAccess = Depends(project_context)):
    _check_scope("shared", access)
    try:
        return agent_store.reset(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.delete("/{key}", status_code=204, dependencies=[Depends(require("agents.manage"))])
def delete_agent(key: str, scope: Scope = "shared", db: Session = Depends(get_db),
                 access: ProjectAccess = Depends(project_context)):
    _check_scope(scope, access)
    if workflows := workflow_store.using_agent(key):
        raise HTTPException(409, f"Remove this agent from these workflows first: {', '.join(workflows)}")
    runs = select(Run).where(Run.status != "completed")
    if scope == "project":
        runs = runs.where(Run.project == access.project)
    unfinished = [r for r in db.scalars(runs) if any(st["key"] == key for st in r.stage_defs)]
    if unfinished:
        raise HTTPException(409, f"{len(unfinished)} unfinished run(s) still use this agent")
    try:
        agent_store.delete(key, scope)
    except KeyError:
        raise HTTPException(404, "Agent not found")
