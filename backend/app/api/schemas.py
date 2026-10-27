from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.defaults import CHECKS, EFFORTS, TOOLS
from app.contracts_store import CONTRACT_STATUSES


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
    code: dict | None = None  # agents only: the step changes code ({"repository", "base_branch"})
    publish_docs: bool = False


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
    base_branch: str | None = Field(default=None, max_length=255)  # the monolith's; default: the project's
    base_branches: dict[str, str] = {}  # per repository key (see the git plan); defaults: the steps' settings
    feature_branch: str | None = Field(default=None, max_length=255)  # defaults to the workflow's branch pattern


class StageExecutionOut(ORM):
    id: int
    stage: str
    attempt: int
    status: str
    start_sha: str | None
    end_sha: str | None
    feedback: str | None
    error: str | None
    rerun: bool | None = None  # re-ran a completed stage in place
    checklist: list[dict] | None = None  # gates: the checklist items ticked at this attempt [{"item", "by", "at"}]
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
    scope: str = "operation"  # operation (a migration) | domain | monolith (discovery runs)
    workflow: str | None
    workflow_stages: list[StageDefOut] = Field(validation_alias="stage_defs")
    status: str
    current_stage: str | None
    branch: str | None
    base_branch: str | None = None
    repository: str | None = None
    repositories: list[dict] | None = None
    legacy: dict | None = None  # the shared read-only copy of the monolith the run reads ({"url", "branch", "sha"})
    delivery: dict | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("scope", mode="before")
    @classmethod
    def _default_scope(cls, scope: str | None) -> str:
        return scope or "operation"


class RunDetail(RunOut):
    workspace: str | None
    inputs: dict | None
    pending_rerun: dict | None  # a rejection waiting for amendments: {"gate", "target_stage", "comments"}
    stages: list[StageExecutionOut]
    approvals: list[ApprovalOut]
    pending_gate: dict | None = None
    pending_branches: dict | None = None  # awaiting_branches: what to confirm before a step changes code
    pending_commit: dict | None = None  # awaiting_commit right after a step: the step and repository to commit
    pending_publish: dict | None = None  # awaiting_publish: approved AS-IS documents to publish to the monolith
    base_sha: str | None = None  # the monolith commit the run started from
    monolith_behind: int | None = None  # commits the base branch has gained since (as last fetched), docs folder aside
    # The run's workflow has changed since it started: what updating its upcoming steps would do (run_workflow.plan)
    workflow_update: dict | None = None
    stopping: bool = False  # an admin asked the working run to stop: its agent ends at its next turn


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
    reviewer: str | None = Field(default=None, max_length=128)  # ignored: the signed-in user is recorded
    # Rejections only: "now" re-runs straight away; "later" holds the run so agents, inputs and documents can be
    # amended first, and it re-runs on POST /runs/{id}/rerun.
    rerun: Literal["now", "later"] = "now"
    # Rejections only: "amend" makes only the requested change, in place (the step's other work and the steps after
    # it up to the gate stay as they are); "redo" re-runs the step from where it started.
    scope: Literal["amend", "redo"] = "amend"


class ChecklistTick(BaseModel):
    item: str = Field(max_length=300)
    done: bool = True


class Restart(BaseModel):
    stage: str
    notes: str | None = Field(default=None, max_length=20_000)  # for the first re-run stage
    fresh: bool = False  # regenerate from scratch instead of updating the existing documents


class StartOverIn(BaseModel):
    workflow: str = Field(min_length=1, max_length=64)  # the migration workflow the feature starts over with


class RetryIn(BaseModel):
    fresh: bool = False  # start the failed step over instead of continuing from what its attempt kept
    notes: str | None = Field(default=None, max_length=20_000)  # for the failed step's agent: what to do differently


class WorkflowUpdateIn(BaseModel):
    rerun: bool = False  # take all the workflow's current steps and re-run from the earliest one a change affects


class Rerun(BaseModel):
    comments: str | None = None  # replaces the rejection's comments when given
    target_stage: str | None = None
    scope: Literal["amend", "redo"] | None = None  # replaces the rejection's scope when given


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
    answered_by: str | None = Field(default=None, max_length=128)  # ignored: the signed-in user is recorded


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
    checks: list[Literal[CHECKS]] = []  # verifications the agent must pass before finishing
    guardrails: str = Field(default="", max_length=50_000)  # this agent's own, in plain words

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
    instructions: str  # empty for users who can't manage agents
    key: str
    builtin: bool
    scope: str = "shared"  # shared (the library every project uses) | project (this project's own or copy)
    customised: bool = False  # a project copy of a shared agent
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
    owned_tables: list[str] | None = None
    owned_packages: list[str] | None = None
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
    owned_tables: list[str] | None
    owned_packages: list[str] | None
    created_at: datetime
    updated_at: datetime


class DomainSummary(DomainOut):
    runs: int = 0  # migration runs
    completed: int = 0
    features: int = 0


class OperationDocs(BaseModel):
    run: RunOut
    artifacts: list[ArtifactSummary]  # latest attempt of each stage


class StepCode(BaseModel):
    repository: str = Field(default="", max_length=1000)  # empty: the project's monolith; {domain} is filled in
    base_branch: str = Field(default="", max_length=255)  # for other repositories; empty: their default branch


class WorkflowStep(BaseModel):
    kind: Literal["agent", "gate"]
    key: str = ""  # agent key for agent steps; gates get one from their title when blank
    title: str = ""  # gates only
    description: str = ""  # gates only
    reviews: list[str] = []  # gates only: agent steps this gate reviews
    checklist: list[str] = []  # gates only: items the approver ticks before approving
    uses: list[str] | None = None  # agents only: earlier agent steps whose output it gets; None = all of them
    include_content: bool = False  # agents only: include those documents' contents in the prompt
    code: StepCode | None = None  # agents only: the step changes code, in this repository
    publish_docs: bool = False  # agents only: once approved, its documents go to the monolith's docs folder



class WorkflowGit(BaseModel):
    """Code changes (the repositories are on the steps that change code): review and commit them when the run
    finishes, and the feature branch's name ({domain}, {feature}, {run}, {date})."""
    commit: bool = True
    branch_pattern: str = Field(default="", max_length=255)


class WorkflowIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    steps: list[WorkflowStep] | None = None
    git: WorkflowGit | None = None


Scope = Literal["operation", "domain", "monolith"]


class WorkflowCreate(BaseModel):
    key: str = Field(pattern=DOMAIN_KEY, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    scope: Scope = "operation"  # what one run covers: a feature, a whole domain or the whole monolith
    steps: list[WorkflowStep] | None = None
    copy_from: str | None = None  # start from another workflow's steps


class WorkflowOut(BaseModel):
    key: str
    name: str
    description: str
    scope: Scope
    steps: list[WorkflowStep]
    stages: list[StageDefOut]  # steps resolved with agent titles
    runs: int = 0
    library: str = "shared"  # shared (every project) | project (this project's own or copy)
    customised: bool = False  # a project copy of a shared workflow
    owner: str | None = None  # browsing: the project it belongs to (None: shared)
    git: WorkflowGit
    created_at: datetime
    updated_at: datetime


class ContractOut(BaseModel):
    key: str
    provider: str
    kind: str
    name: str
    version: str
    description: str
    spec: str | None
    spec_path: str | None
    consumers: list[str]
    status: str
    run_id: str | None
    created_at: datetime
    updated_at: datetime


class ContractUpdate(BaseModel):
    status: Literal[CONTRACT_STATUSES]


class DependencyOut(ORM):
    id: int
    run_id: str
    interaction: str
    source: str
    target: str
    kind: str
    pattern: str
    contract: str | None
    status: str
    summary: str
    code_refs: list[str]
    created_at: datetime
    operation: str | None = None  # the run's operation


class DependencyEdge(BaseModel):
    source: str  # calls / consumes
    target: str  # provides
    count: int
    patterns: dict[str, int]


class MigrationStep(BaseModel):
    domain: str
    reason: str


class DependencyMap(BaseModel):
    domains: list[str]
    edges: list[DependencyEdge]
    order: list[MigrationStep]


FEATURE_KEY = r"^[A-Za-z][A-Za-z0-9_-]*$"


class FeatureIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    entry_points: list[str] | None = None
    tables: list[str] | None = None
    calls: list[str] | None = None

    @field_validator("entry_points", "tables", "calls")
    @classmethod
    def _clean(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else list(dict.fromkeys(v.strip() for v in values if v.strip()))


class FeatureCreate(FeatureIn):
    key: str = Field(pattern=FEATURE_KEY, max_length=128)
    name: str = Field(min_length=1, max_length=255)


class RunBrief(BaseModel):
    id: str
    status: str
    current_stage: str | None
    created_at: datetime


class FeatureOut(BaseModel):
    key: str
    domain: str
    name: str
    description: str
    entry_points: list[str]
    tables: list[str]
    calls: list[str]
    run_id: str | None  # discovery run that listed it; None when added by hand
    missing_from_latest: bool
    migration: RunBrief | None  # latest migration run of this feature
    created_at: datetime
    updated_at: datetime


class DomainDiscovery(BaseModel):
    key: str
    name: str
    features: int
    run: RunBrief | None  # latest feature-listing run


class DiscoveryOverview(BaseModel):
    bounded_contexts: RunBrief | None  # latest bounded-contexts run
    contexts_inputs: AgentInputsIn | None  # paths and notes the latest bounded-contexts run was given
    contexts_approved: bool
    domains: list[DomainDiscovery]
    not_found: list[str]  # domains the latest approved bounded contexts didn't include


class DiscoveryStart(BaseModel):
    workflow: str | None = None  # defaults to the built-in discovery workflow
    inputs: AgentInputsIn | None = None  # where to look (e.g. existing documentation) and notes, for this run


class FeaturesStart(DiscoveryStart):
    only_missing: bool = True  # skip domains that already have a feature list
