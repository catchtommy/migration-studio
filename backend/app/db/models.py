import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.orchestrator.stages import STANDARD


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One migration of a single (domain, operation) through a workflow."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    domain: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(128))
    # pending | running | awaiting_input | awaiting_approval | completed | failed
    status: Mapped[str] = mapped_column(String(32), default="pending")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    workspace: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    # Operation-specific guidance for every agent: {"paths": [...], "notes": "..."}
    inputs: Mapped[dict | None] = mapped_column(JSON)
    workflow: Mapped[str | None] = mapped_column(String(64))  # key of the workflow the run started from
    # Snapshot of that workflow's stages (StageDef dicts), so later edits to the workflow don't affect this run.
    workflow_stages: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    stages: Mapped[list["StageExecution"]] = relationship(back_populates="run", order_by="StageExecution.id")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="run", order_by="Approval.id")

    @property
    def stage_defs(self) -> list[dict]:
        """The run's workflow; runs from before workflows were editable used the standard one."""
        return self.workflow_stages or STANDARD.to_dicts()


class StageExecution(Base):
    """One attempt of one stage. Rejections and retries create new attempts."""

    __tablename__ = "stage_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # running | completed | failed | awaiting_approval | approved | rejected
    status: Mapped[str] = mapped_column(String(32))
    start_sha: Mapped[str | None] = mapped_column(String(64))
    end_sha: Mapped[str | None] = mapped_column(String(64))
    feedback: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="stages")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text)  # relative to the run workspace
    kind: Mapped[str] = mapped_column(String(32))  # markdown | yaml | openapi | java | text
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    gate: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))  # approve | reject
    target_stage: Mapped[str | None] = mapped_column(String(64))
    comments: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="approvals")


class AuditEvent(Base):
    """Append-only log of everything that happens in a run; streamed to the UI over SSE."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))  # log | status | artifact | approval | error
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON)


class Domain(Base):
    """A business domain of the monolith, with the guidance its agents need to find their way around it."""

    __tablename__ = "domains"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")  # shared with every agent working on this domain
    # agent key -> {"paths": [...], "notes": "..."}: where that agent should look and what to know
    agent_inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    default_workflow: Mapped[str | None] = mapped_column(String(64))
    # Ownership, for the boundary check and the agents: tables (in the monolith schema) and Java package prefixes
    # that belong to this domain. No other domain's service may use them directly.
    owned_tables: Mapped[list | None] = mapped_column(JSON)
    owned_packages: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Agent(Base):
    """An agent in the library; workflows use it as one of their stages. Edited from the Agents page."""

    __tablename__ = "agents"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16))  # stub | llm
    model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    jdk: Mapped[str] = mapped_column(String(16))  # legacy | modern: which JDK run_command uses
    instructions: Mapped[str] = mapped_column(Text)
    tools: Mapped[list] = mapped_column(JSON)
    outputs: Mapped[list] = mapped_column(JSON)  # documents the agent must write, relative to the operation folder
    max_turns: Mapped[int] = mapped_column(Integer)
    checks: Mapped[list | None] = mapped_column(JSON)  # extra verifications after the agent finishes: ["boundary"]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowDef(Base):
    """A named workflow: ordered agent steps and approval gates. Edited from the Workflows page."""

    __tablename__ = "workflows"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # [{"kind": "agent", "key": <agent key>} | {"kind": "gate", "key", "title", "description", "reviews": [...]}]
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Question(Base):
    """A question an agent asked mid-stage. The stage waits until a person answers it."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSON)  # suggested answers, if the agent offered any
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | answered | cancelled
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Contract(Base):
    """A cross-domain contract shared by all domain migrations: an API, event or command one domain provides.
    Proposed by integration design; recorded when the design gate is approved."""

    __tablename__ = "contracts"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)  # e.g. customer.get-customer, loan.loan-approved
    provider: Mapped[str] = mapped_column(String(64), index=True)  # domain that owns it
    kind: Mapped[str] = mapped_column(String(16))  # api | event | command
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32), default="1")
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[str | None] = mapped_column(Text)  # OpenAPI / AsyncAPI document (YAML)
    spec_path: Mapped[str | None] = mapped_column(Text)
    consumers: Mapped[list] = mapped_column(JSON, default=list)
    # proposed | approved | provided-by-monolith | provided-by-service | deprecated
    status: Mapped[str] = mapped_column(String(32), default="approved")
    run_id: Mapped[str | None] = mapped_column(String(36))  # run whose design last recorded it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Dependency(Base):
    """One cross-domain interaction found in an operation, with the integration pattern chosen for it."""

    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    interaction: Mapped[str] = mapped_column(String(128))  # id within the run's cross-domain catalogue
    source: Mapped[str] = mapped_column(String(64), index=True)  # domain that calls / depends
    target: Mapped[str] = mapped_column(String(64), index=True)  # domain it depends on
    kind: Mapped[str] = mapped_column(String(32))  # query | bulk-query | side-effect | command | shared-write | reference-data
    pattern: Mapped[str] = mapped_column(String(32))  # sync-api | read-model | event | saga | deferred | reference-copy
    contract: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="planned")  # planned | deferred | done
    summary: Mapped[str] = mapped_column(Text, default="")
    code_refs: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
