import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, ForeignKey, Identity, Integer, LargeBinary, String, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.types import JSONText, LongText, NonNullText, UTCDateTime
from app.orchestrator.stages import STANDARD


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# Project keys. Rows of the shared agent/workflow library and global settings use these instead of a project key
# (a primary key column can't be NULL, and Oracle stores '' as NULL).
SHARED = "_shared"
GLOBAL = "_global"


class Run(Base):
    """One migration of a single (domain, operation) through a workflow. Discovery runs use the same machinery with a
    reserved domain or operation (see app/discovery_store.py)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project: Mapped[str] = mapped_column(String(64), index=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(128))
    # pending | running | awaiting_input | awaiting_approval | changes_requested | awaiting_commit | completed | failed
    # (awaiting_commit: the workflow finished; a person reviews the code changes and commits them, see app/delivery.py)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    workspace: Mapped[str | None] = mapped_column(LongText)
    branch: Mapped[str | None] = mapped_column(String(255))
    base_sha: Mapped[str | None] = mapped_column(String(64))  # monolith commit the run branch started from
    repository: Mapped[str | None] = mapped_column(LongText)  # git URL or path the run works on (None: blank workspace)
    base_branch: Mapped[str | None] = mapped_column(String(255))  # the branch `branch` was cut from (None: default)
    started_by: Mapped[int | None] = mapped_column(Integer)  # user id; their git credentials fetch the repository
    # The other repositories agent steps change code in, each checked out inside the workspace on the same feature
    # branch: [{"key", "name", "url", "base_branch", "path", "base_sha"}]. The workspace root is `repository`.
    repositories: Mapped[list | None] = mapped_column(JSONText)
    # A run that only reads the monolith uses the project's shared read-only copy instead of its own checkout:
    # {"url", "branch", "sha"} (see app/legacy.py). None: the workspace root is its own checkout (or blank).
    legacy: Mapped[dict | None] = mapped_column(JSONText)
    # Committing the code: {"commit": bool (review and commit at the end)} at start, then per repository once
    # committed: {"repos": {key: {"status": "pushed" | "skipped", "sha", "branch", "files", "by", "at", ...}}}
    delivery: Mapped[dict | None] = mapped_column(JSONText)
    error: Mapped[str | None] = mapped_column(LongText)
    # Operation-specific guidance for every agent: {"paths": [...], "notes": "..."}
    inputs: Mapped[dict | None] = mapped_column(JSONText)
    workflow: Mapped[str | None] = mapped_column(String(64))  # key of the workflow the run started from
    # Snapshot of that workflow's stages (StageDef dicts), so later edits to the workflow don't affect this run.
    workflow_stages: Mapped[list | None] = mapped_column(JSONText)
    scope: Mapped[str | None] = mapped_column(String(16))  # operation (None) | domain | monolith: see WorkflowDef
    # A rejection held back so people can amend agents, inputs or documents before the re-run:
    # {"gate", "target_stage", "comments"}. Set while the status is changes_requested.
    pending_rerun: Mapped[dict | None] = mapped_column(JSONText)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    stages: Mapped[list["StageExecution"]] = relationship(back_populates="run", order_by="StageExecution.id")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="run", order_by="Approval.id")

    @property
    def stage_defs(self) -> list[dict]:
        """The run's workflow; runs from before workflows were editable used the standard one."""
        return self.workflow_stages or STANDARD.to_dicts()


class StageExecution(Base):
    """One attempt of one stage. Rejections and retries create new attempts."""

    __tablename__ = "stage_executions"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # running | completed | failed | awaiting_approval | approved | rejected
    status: Mapped[str] = mapped_column(String(32))
    start_sha: Mapped[str | None] = mapped_column(String(64))
    end_sha: Mapped[str | None] = mapped_column(String(64))
    feedback: Mapped[str | None] = mapped_column(LongText)
    error: Mapped[str | None] = mapped_column(LongText)
    diff: Mapped[str | None] = mapped_column(LongText)  # git diff of the attempt, kept so it outlives the workspace
    # The run's other repositories (Run.repositories): {key: {"start": sha, "end": sha, "diff": text}}; every
    # repository's start is recorded, end and diff only where the attempt changed something.
    repos: Mapped[dict | None] = mapped_column(JSONText)
    # A restart from a completed stage: the attempt updated the previous documents in place instead of starting over.
    rerun: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    run: Mapped[Run] = relationship(back_populates="stages")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024))  # relative to the run workspace
    kind: Mapped[str] = mapped_column(String(32))  # markdown | yaml | openapi | java | text
    content: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    gate: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))  # approve | reject
    target_stage: Mapped[str | None] = mapped_column(String(64))
    comments: Mapped[str | None] = mapped_column(LongText)
    reviewer: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    run: Mapped[Run] = relationship(back_populates="approvals")


class AuditEvent(Base):
    """Append-only log of everything that happens in a run; streamed to the UI over SSE."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))  # log | status | artifact | approval | error
    message: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    project: Mapped[str] = mapped_column(String(64), primary_key=True, default=GLOBAL)  # GLOBAL or a project key
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSONText)


class Domain(Base):
    """A business domain of the monolith, with the guidance its agents need to find their way around it."""

    __tablename__ = "domains"

    project: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    context: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)  # shared with every agent working on this domain
    # agent key -> {"paths": [...], "notes": "..."}: where that agent should look and what to know
    agent_inputs: Mapped[dict] = mapped_column(JSONText, default=dict)
    default_workflow: Mapped[str | None] = mapped_column(String(64))
    # Ownership, for the boundary check and the agents: tables (in the monolith schema) and Java package prefixes
    # that belong to this domain. No other domain's service may use them directly.
    owned_tables: Mapped[list | None] = mapped_column(JSONText)
    owned_packages: Mapped[list | None] = mapped_column(JSONText)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Agent(Base):
    """An agent in the library; workflows use it as one of their stages. Edited from the Agents page."""

    __tablename__ = "agents"

    project: Mapped[str] = mapped_column(String(64), primary_key=True, default=SHARED)  # SHARED: the library
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    mode: Mapped[str] = mapped_column(String(16))  # stub | llm
    model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    jdk: Mapped[str] = mapped_column(String(16))  # legacy | modern: which JDK run_command uses
    instructions: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    tools: Mapped[list] = mapped_column(JSONText)
    outputs: Mapped[list] = mapped_column(JSONText)  # documents the agent must write, relative to the operation folder
    max_turns: Mapped[int] = mapped_column(Integer)
    checks: Mapped[list | None] = mapped_column(JSONText)  # extra verifications after the agent finishes: ["boundary"]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SharedText(Base):
    """A text agents share: the preamble every agent gets, or a block several agents' instructions include as
    {{key}}. Edited from the Agents page (Shared texts); see app/shared_texts.py."""

    __tablename__ = "shared_texts"

    project: Mapped[str] = mapped_column(String(64), primary_key=True, default=SHARED)  # SHARED: every project's
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    text: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class WorkflowDef(Base):
    """A named workflow: ordered agent steps and approval gates. Edited from the Workflows page."""

    __tablename__ = "workflows"

    project: Mapped[str] = mapped_column(String(64), primary_key=True, default=SHARED)  # SHARED: the library
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    # [{"kind": "agent", "key": <agent key>} | {"kind": "gate", "key", "title", "description", "reviews": [...]}]
    steps: Mapped[list] = mapped_column(JSONText)
    # What one run covers: operation (None; a migration of one feature), domain (discovering a domain's features)
    # or monolith (discovering the bounded contexts).
    scope: Mapped[str | None] = mapped_column(String(16))
    # Code changes: {"commit": bool, "repository": "", "base_branch": "", "branch_pattern": ""}; empty values use the
    # project's repository and base branch and the default pattern. None: the defaults (see app/delivery.py).
    git: Mapped[dict | None] = mapped_column(JSONText)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Question(Base):
    """A question an agent asked mid-stage. The stage waits until a person answers it."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    options: Mapped[list | None] = mapped_column(JSONText)  # suggested answers, if the agent offered any
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | answered | cancelled
    answer: Mapped[str | None] = mapped_column(LongText)
    answered_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Contract(Base):
    """A cross-domain contract shared by all domain migrations: an API, event or command one domain provides.
    Proposed by integration design; recorded when the design gate is approved."""

    __tablename__ = "contracts"

    project: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)  # e.g. customer.get-customer, loan.loan-approved
    provider: Mapped[str] = mapped_column(String(64), index=True)  # domain that owns it
    kind: Mapped[str] = mapped_column(String(16))  # api | event | command
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32), default="1")
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    spec: Mapped[str | None] = mapped_column(LongText)  # OpenAPI / AsyncAPI document (YAML)
    spec_path: Mapped[str | None] = mapped_column(LongText)
    consumers: Mapped[list] = mapped_column(JSONText, default=list)
    # proposed | approved | provided-by-monolith | provided-by-service | deprecated
    status: Mapped[str] = mapped_column(String(32), default="approved")
    run_id: Mapped[str | None] = mapped_column(String(36))  # run whose design last recorded it
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Dependency(Base):
    """One cross-domain interaction found in an operation, with the integration pattern chosen for it."""

    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    interaction: Mapped[str] = mapped_column(String(128))  # id within the run's cross-domain catalogue
    source: Mapped[str] = mapped_column(String(64), index=True)  # domain that calls / depends
    target: Mapped[str] = mapped_column(String(64), index=True)  # domain it depends on
    kind: Mapped[str] = mapped_column(String(32))  # query | bulk-query | side-effect | command | shared-write | reference-data
    pattern: Mapped[str] = mapped_column(String(32))  # sync-api | read-model | event | saga | deferred | reference-copy
    contract: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="planned")  # planned | deferred | done
    summary: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    code_refs: Mapped[list] = mapped_column(JSONText, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Feature(Base):
    """A feature (business operation) of a domain: what a migration run migrates. Listed by the domain's feature
    discovery when its gate is approved, or added by hand."""

    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("project", "domain", "key"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    domain: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(128))  # the operation name of its migration runs
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    entry_points: Mapped[list] = mapped_column(JSONText, default=list)
    tables: Mapped[list] = mapped_column(JSONText, default=list)
    calls: Mapped[list] = mapped_column(JSONText, default=list)  # other domains it reaches into
    run_id: Mapped[str | None] = mapped_column(String(36))  # discovery run that listed it; None = added by hand
    # Kept because it has migration runs, although the latest approved feature list no longer has it.
    missing_from_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class GraphCheckpoint(Base):
    """A LangGraph checkpoint of a run (thread_id = run id). See app/orchestrator/checkpointer.py."""

    __tablename__ = "graph_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(32))
    checkpoint: Mapped[bytes | None] = mapped_column(LargeBinary)
    meta: Mapped[bytes | None] = mapped_column(LargeBinary)


class GraphWrite(Base):
    """A pending write of a LangGraph task, linked to a checkpoint."""

    __tablename__ = "graph_writes"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(32))
    value: Mapped[bytes | None] = mapped_column(LargeBinary)


class User(Base):
    """Someone who can sign in to the studio. What they may do comes from their roles."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)  # lowercase
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list] = mapped_column(JSONText, default=list)  # role keys
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    git_name: Mapped[str | None] = mapped_column(String(128))  # author of the commits they make; display name if unset
    git_email: Mapped[str | None] = mapped_column(String(255))


class GitCredential(Base):
    """A user's access token for one git host, used when they fetch or push. The token is encrypted (app/secret_box)."""

    __tablename__ = "git_credentials"
    __table_args__ = (UniqueConstraint("user_id", "host"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host: Mapped[str] = mapped_column(String(255))  # e.g. github.com, mycompany.ghe.com
    username: Mapped[str] = mapped_column(String(255))
    token: Mapped[str] = mapped_column(LongText)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Role(Base):
    """A named set of permissions (see app/auth.py PERMISSIONS), given to users."""

    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    permissions: Mapped[list] = mapped_column(JSONText, default=list)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # the Admin role: always every permission
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class UserSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Project(Base):
    """A migration project: its own monolith, domains, runs, contracts and settings, and its members."""

    __tablename__ = "projects"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(NonNullText, default="", nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ProjectMember(Base):
    """A user's access to a project, with the role (a key of `roles`) they have in it."""

    __tablename__ = "project_members"

    project: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
