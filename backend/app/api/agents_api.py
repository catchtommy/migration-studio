from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from app import agent_files, agent_store, settings_store, shared_texts, workflow_store
from app.agents.defaults import AUTO, CLAUDE_MODELS, EFFORTS, TOOLS
from app.copilot_service import get_service
from app.api.schemas import AgentCatalog, AgentCreate, AgentIn, AgentOut
from app.auth import ProjectAccess, project_context, require

router = APIRouter(prefix="/api/agents")


Scope = Literal["shared", "project"]


def _visible(agent: dict, access: ProjectAccess) -> dict:
    """Other pages need agents' names and modes; only people who manage agents see their instructions."""
    return agent if access.can("agents.manage") else {**agent, "instructions": ""}


def _check_placeholders(instructions: str, guardrails: str | None = None) -> None:
    for what, text in (("instructions", instructions), ("guardrails", guardrails or "")):
        missing = shared_texts.unknown(text)
        if missing:
            raise HTTPException(422, f"The {what} include {', '.join('{{' + m + '}}' for m in missing)}, which is "
                                     "not a shared text. Check the spelling, or see Shared texts on the Agents page.")


def _check_scope(scope: Scope, access: ProjectAccess) -> None:
    """The shared library is used by every project, so changing it needs Manage agents from the user's own roles."""
    if scope == "shared" and not access.user.can("agents.manage"):
        raise HTTPException(403, "Changing the shared agent affects every project. You can customise it for this "
                                 "project instead.")


class ModeAll(BaseModel):
    mode: Literal["stub", "llm"]


@router.post("/mode", dependencies=[Depends(require("agents.manage"))])
def set_mode_all(body: ModeAll, access: ProjectAccess = Depends(project_context)) -> dict:
    """Switch every agent to AI (`llm`) or back to the stub in one go: the shared library, which every project uses
    (so it needs Manage agents from the user's own roles), and this project's own agents and copies."""
    _check_scope("shared", access)
    return {"changed": agent_store.set_mode_all(body.mode), "mode": body.mode}


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
        parsed = agent_files.parse(body.content)
    except agent_files.InvalidAgentFile as e:
        raise HTTPException(422, str(e))
    return {**parsed, "instructions": shared_texts.template(parsed["instructions"])}


@router.post("/render-agent-md", dependencies=[Depends(require("agents.manage"))])
def render_agent_md(body: AgentIn):
    """The (possibly unsaved) agent fields as agent.md text, for the agent.md editor."""
    fields = body.model_dump()
    return {"content": agent_files.render({**fields, "instructions": shared_texts.expand(fields["instructions"])})}


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
    if "instructions" in parsed:  # the file has the shared texts written out
        parsed["instructions"] = shared_texts.template(parsed["instructions"])
    try:
        fields = AgentIn.model_validate({**{f: current[f] for f in AgentIn.model_fields}, **parsed})
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()))
    _check_scope(scope, access)
    _check_placeholders(fields.instructions, fields.guardrails)
    return {"agent": agent_store.update(key, fields.model_dump(), scope), "warnings": warnings}


@router.get("/{key}/agent.md", response_class=PlainTextResponse, dependencies=[Depends(require("agents.manage"))])
def export_agent_md(key: str):
    try:
        agent = agent_store.get(key)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    filename = f"{key.replace('_', '-')}.agent.md"
    # Outside the studio there are no shared texts: the file has them written out.
    agent = {**agent, "instructions": shared_texts.expand(agent["instructions"])}
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
    _check_placeholders(body.instructions, body.guardrails)
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


@router.get("/{key}/usage")
def agent_usage(key: str, scope: Scope = "shared", access: ProjectAccess = Depends(project_context)) -> dict:
    """What deleting the agent would affect, and why it can't be deleted yet when it can't (agent_store.usage)."""
    try:
        return agent_store.usage(key, scope)
    except KeyError:
        raise HTTPException(404, "Agent not found")


@router.delete("/{key}", status_code=204, dependencies=[Depends(require("agents.manage"))])
def delete_agent(key: str, scope: Scope = "shared", access: ProjectAccess = Depends(project_context)):
    """Delete an agent no workflow has as a step and no unfinished run uses (agent_store.usage says why not)."""
    _check_scope(scope, access)
    try:
        found = agent_store.usage(key, scope)
    except KeyError:
        raise HTTPException(404, "Agent not found")
    if found["blocked"]:
        raise HTTPException(409, f"The {found['title']} agent can't be deleted: {found['blocked']}.")
    try:
        agent_store.delete(key, scope)
    except KeyError:
        raise HTTPException(404, "Agent not found")
