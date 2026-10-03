"""Workflow definitions: an ordered list of agent stages and human approval gates. Order is the order of the graph.

Workflows are edited on the Workflows page (see app/workflow_store.py); each run keeps a snapshot of the workflow it
started with. STANDARD is the built-in migration workflow, seeded on first start and used by runs created before
workflows were editable.
"""

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class StageDef:
    key: str
    title: str
    kind: str  # agent | gate
    description: str
    reviews: tuple[str, ...] = field(default_factory=tuple)  # gates only: agent stages this gate reviews
    # Agents only: earlier agent stages whose documents this stage receives. None = all earlier agent stages.
    uses: tuple[str, ...] | None = None
    include_content: bool = False  # agents only: put those documents' contents in the prompt, not just their paths

    def to_dict(self) -> dict:
        return {**asdict(self), "reviews": list(self.reviews),
                "uses": None if self.uses is None else list(self.uses)}

    @classmethod
    def from_dict(cls, d: dict) -> "StageDef":
        uses = d.get("uses")
        return cls(key=d["key"], title=d["title"], kind=d["kind"], description=d.get("description", ""),
                   reviews=tuple(d.get("reviews") or ()), uses=None if uses is None else tuple(uses),
                   include_content=bool(d.get("include_content")))


class Workflow:
    def __init__(self, stages: Iterable[StageDef]):
        self.stages = tuple(stages)
        self.by_key = {s.key: s for s in self.stages}
        self.keys = [s.key for s in self.stages]

    @classmethod
    def from_dicts(cls, stages: list[dict]) -> "Workflow":
        return cls(StageDef.from_dict(d) for d in stages)

    def to_dicts(self) -> list[dict]:
        return [s.to_dict() for s in self.stages]

    @property
    def agent_keys(self) -> list[str]:
        return [s.key for s in self.stages if s.kind == "agent"]

    def next_stage(self, key: str) -> str | None:
        i = self.keys.index(key)
        return self.keys[i + 1] if i + 1 < len(self.keys) else None

    def agents_before(self, key: str) -> list[str]:
        return [s.key for s in self.stages[:self.keys.index(key)] if s.kind == "agent"]

    def inputs_of(self, key: str) -> list[str]:
        """Earlier agent stages whose documents this stage receives, in workflow order."""
        earlier = self.agents_before(key)
        uses = self.by_key[key].uses
        return earlier if uses is None else [k for k in earlier if k in uses]


STANDARD = Workflow((
    StageDef("as_is_doc", "AS-IS documentation", "agent",
             "Trace JSP → Controller → Service → DAO → iBatis SQL and document the current behaviour."),
    StageDef("deep_dive", "Deep dive", "agent",
             "Business rules, edge cases, exception paths, cross-domain calls and a test scenario catalogue."),
    StageDef("gate_analysis", "Approve analysis", "gate",
             "Review the AS-IS documentation and deep dive.", ("as_is_doc", "deep_dive")),
    StageDef("boundary_design", "Modular monolith design", "agent",
             "Bounded context, table ownership, cross-domain dependencies and the proposed REST contract."),
    StageDef("gate_design", "Approve design", "gate",
             "Review the proposed boundaries and OpenAPI contract.", ("boundary_design",)),
    StageDef("modular_monolith", "Module in monolith", "agent",
             "Extract a Java 8 / Spring 3 / iBatis module shaped like the future microservice."),
    StageDef("microservice_scaffold", "Spring Boot microservice", "agent",
             "Wrap the module in a Spring Boot app exposing the approved REST contract."),
    StageDef("independent_tests", "Independent tests", "agent",
             "Contract, integration and characterization tests covering every scenario."),
    StageDef("gate_tests", "Approve tests", "gate",
             "Review the module, microservice and scenario coverage.",
             ("modular_monolith", "microservice_scaffold", "independent_tests")),
    StageDef("modernise", "Modernisation", "agent",
             "Java 25, Spring Boot 4, iBatis → Spring Data JPA on the existing tables, code improvements."),
    StageDef("monolith_integration", "Monolith integration", "agent",
             "REST client and feature flag in the monolith to switch between module and microservice."),
    StageDef("gate_release", "Approve release", "gate",
             "Final review of the modernised service and monolith integration.",
             ("modernise", "monolith_integration")),
))
