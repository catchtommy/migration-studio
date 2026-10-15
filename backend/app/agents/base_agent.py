from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.orchestrator.stages import StageDef


@dataclass
class ArtifactOut:
    name: str
    path: str  # relative to workspace
    kind: str  # markdown | yaml | openapi | java | text


@dataclass
class PriorDocument:
    """A document an earlier stage of this run produced, handed to a later stage."""

    stage: str
    stage_title: str
    path: str  # relative to the workspace
    content: str | None = None  # set when the stage is configured to include contents in its prompt


@dataclass
class AgentInputs:
    """Guidance a person gave the agent: from the domain (shared and stage-specific) and from the run."""

    domain_name: str = ""
    domain_description: str = ""
    domain_context: str = ""
    stage_paths: list[str] = field(default_factory=list)
    stage_notes: str = ""
    run_paths: list[str] = field(default_factory=list)
    run_notes: str = ""
    run_label: str = "this operation"  # what the run's own paths and notes are for, in headings

    def as_markdown(self) -> str:
        sections = []
        if self.domain_description or self.domain_context:
            sections.append("## Domain: " + self.domain_name + "\n" +
                            "\n\n".join(t for t in (self.domain_description, self.domain_context) if t))
        if self.stage_paths:
            sections.append("## Where to look (domain)\n" + "\n".join(f"- {p}" for p in self.stage_paths))
        if self.stage_notes:
            sections.append("## Notes for this stage (domain)\n" + self.stage_notes)
        if self.run_paths:
            sections.append(f"## Entry points and files for {self.run_label}\n" +
                            "\n".join(f"- {p}" for p in self.run_paths))
        if self.run_notes:
            sections.append(f"## Notes for {self.run_label}\n" + self.run_notes)
        return "\n\n".join(sections)


@dataclass
class AgentContext:
    run_id: str
    domain: str
    operation: str
    stage: str
    attempt: int
    workspace: Path
    feedback: str | None  # reviewer comments from a rejection, fed back into the agent
    settings: dict
    log: Callable[[str], None]
    config: dict = field(default_factory=dict)  # this stage's agent config (see app/agent_store.py)
    stage_def: StageDef | None = None  # the stage as defined in the run's workflow
    inputs: AgentInputs = field(default_factory=AgentInputs)
    # Documents from the earlier stages this stage uses (see StageDef.uses), latest attempt of each, in order.
    previous_artifacts: list[PriorDocument] = field(default_factory=list)
    answered: list[tuple[str, str]] = field(default_factory=list)  # (question, answer) from earlier attempts
    catalogue: str = ""  # cross-domain ownership, conventions and contracts (markdown)
    ownership: dict = field(default_factory=dict)  # domain key -> {"tables": [...], "packages": [...]}
    service_dir: str = ""  # this domain's microservice directory, relative to the workspace
    # Asks a person a question (with optional suggested answers) and blocks until it is answered.
    ask: Callable[[str, list[str] | None], str] = lambda question, options=None: ""

    @property
    def output_dir(self) -> Path:
        """Where analysis/design documents for this operation live inside the workspace."""
        d = self.workspace / docs_dir(self.domain, self.operation)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.workspace))


def docs_dir(domain: str, operation: str) -> str:
    """The run's documents folder, relative to its workspace."""
    return f".migration/{domain}/{operation}"


class StageAgent(ABC):
    """One agent per workflow stage. `run` does the work and must verify its own output before returning;
    raising an exception fails the stage and surfaces the error in the UI."""

    stage: str

    @abstractmethod
    def run(self, ctx: AgentContext) -> list[ArtifactOut]: ...
