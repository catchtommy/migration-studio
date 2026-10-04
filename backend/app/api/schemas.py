from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.defaults import EFFORTS, TOOLS


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageDefOut(BaseModel):
    key: str
    title: str
    kind: str
    description: str
    reviews: list[str]
    uses: list[str] | None = None
    include_content: bool = False


DOMAIN_KEY = r"^[a-z][a-z0-9_-]*$"


class AgentInputsIn(BaseModel):
    """Where an agent should look and what it should know."""

    paths: list[str] = []
    notes: str = ""


class RunCreate(BaseModel):
    domain: str = Field(pattern=DOMAIN_KEY, max_length=64)
    operation: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=128)
    inputs: AgentInputsIn | None = None
    workflow: str | None = None  # defaults to the domain's default workflow


class StageExecutionOut(ORM):
    id: int
    stage: str
    attempt: int
    status: str
    start_sha: str | None
    end_sha: str | None
    feedback: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ApprovalOut(ORM):
    id: int
    gate: str
    decision: str
    target_stage: str | None
    comments: str | None
    reviewer: str
    created_at: datetime


class RunOut(ORM):
    id: str
    domain: str
    operation: str
    workflow: str | None
    workflow_stages: list[StageDefOut] = Field(validation_alias="stage_defs")
    status: str
    current_stage: str | None
    branch: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunOut):
    workspace: str | None
    inputs: dict | None
    stages: list[StageExecutionOut]
    approvals: list[ApprovalOut]
    pending_gate: dict | None = None


class ArtifactSummary(ORM):
    id: int
    stage: str
    attempt: int
    name: str
    path: str
    kind: str
    created_at: datetime
    updated_at: datetime


class ArtifactOut(ArtifactSummary):
    content: str


class ArtifactUpdate(BaseModel):
    content: str


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    comments: str | None = None
    target_stage: str | None = None
    reviewer: str = Field(min_length=1, max_length=128)


class EventOut(ORM):
    id: int
    stage: str | None
    kind: str
    message: str
    created_at: datetime


class QuestionOut(ORM):
    id: int
    stage: str
    attempt: int
    question: str
    options: list[str] | None
    status: str
    answer: str | None
    answered_by: str | None
    created_at: datetime
    answered_at: datetime | None


class AnswerIn(BaseModel):
    answer: str = ""  # blank = skip: the agent is told to use its judgement
    answered_by: str = Field(min_length=1, max_length=128)


class AgentIn(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    description: str = ""
    mode: Literal["stub", "llm"]
    model: str = Field(min_length=1, max_length=128)  # provider model id, or "auto"
    effort: Literal[EFFORTS]
    jdk: Literal["legacy", "modern"]
    instructions: str = Field(min_length=1)
    tools: list[Literal[tuple(TOOLS)]]
    outputs: list[str]
    max_turns: int = Field(ge=1, le=500)

    @field_validator("outputs")
    @classmethod
    def _relative_paths(cls, outputs: list[str]) -> list[str]:
        cleaned = [o.strip() for o in outputs if o.strip()]
        for o in cleaned:
            path = PurePosixPath(o)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"{o}: outputs must be paths inside the operation folder")
        return cleaned


class AgentOut(AgentIn):
    key: str
    builtin: bool
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    title: str = Field(min_length=1, max_length=128)
    description: str = ""


class AgentCatalog(BaseModel):
    provider: str
    models: list[str]  # suggestions; "auto" first
    efforts: list[str]
    tools: list[dict[str, str]]


class DomainCreate(BaseModel):
    key: str = Field(pattern=DOMAIN_KEY, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    context: str | None = None
    agent_inputs: dict[str, AgentInputsIn] | None = None  # keyed by agent
    default_workflow: str | None = None  # "" clears it


class DomainOut(ORM):
    key: str
    name: str
    description: str
    context: str
    agent_inputs: dict[str, AgentInputsIn]
    default_workflow: str | None
    created_at: datetime
    updated_at: datetime


class DomainSummary(DomainOut):
    runs: int = 0
    completed: int = 0


class OperationDocs(BaseModel):
    run: RunOut
    artifacts: list[ArtifactSummary]  # latest attempt of each stage


class WorkflowStep(BaseModel):
    kind: Literal["agent", "gate"]
    key: str = ""  # agent key for agent steps; gates get one from their title when blank
    title: str = ""  # gates only
    description: str = ""  # gates only
    reviews: list[str] = []  # gates only: agent steps this gate reviews
    uses: list[str] | None = None  # agents only: earlier agent steps whose output it gets; None = all of them
    include_content: bool = False  # agents only: include those documents' contents in the prompt


class WorkflowIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    steps: list[WorkflowStep] | None = None


class WorkflowCreate(BaseModel):
    key: str = Field(pattern=DOMAIN_KEY, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    steps: list[WorkflowStep] | None = None
    copy_from: str | None = None  # start from another workflow's steps


class WorkflowOut(BaseModel):
    key: str
    name: str
    description: str
    steps: list[WorkflowStep]
    stages: list[StageDefOut]  # steps resolved with agent titles
    runs: int = 0
    created_at: datetime
    updated_at: datetime
