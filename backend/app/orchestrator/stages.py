"""Workflow definitions: an ordered list of agent stages and human approval gates. Order is the order of the graph.

Workflows are edited on the Workflows page (see app/workflow_store.py); each run keeps a snapshot of the workflow it
started with. STANDARD is the built-in migration workflow, seeded on first start and used by runs created before
workflows were editable. BOUNDED_CONTEXTS and DOMAIN_FEATURES are the built-in discovery workflows that come before
any migration: find the monolith's bounded contexts, then list each domain's features.
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
    # Agents only: the step changes code, in {"repository": "" (the project's monolith) | git URL ({domain} filled
    # in), "base_branch": ""}. None: it writes documents only (it still reads the monolith).
    code: dict | None = field(default=None, hash=False, compare=False)
    # Agents only: once approved, the step's documents are published to the monolith's docs/<domain>/<feature>/
    # (AS-IS documentation, for later agents; see app/docs_publish.py).
    publish_docs: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "reviews": list(self.reviews),
                "uses": None if self.uses is None else list(self.uses)}

    @classmethod
    def from_dict(cls, d: dict) -> "StageDef":
        uses = d.get("uses")
        return cls(key=d["key"], title=d["title"], kind=d["kind"], description=d.get("description", ""),
                   reviews=tuple(d.get("reviews") or ()), uses=None if uses is None else tuple(uses),
                   include_content=bool(d.get("include_content")), code=d.get("code") or None,
                   publish_docs=bool(d.get("publish_docs")))


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
             "Trace JSP → Controller → Service → DAO → iBatis SQL and document the current behaviour.",
             publish_docs=True),
    StageDef("deep_dive", "Deep dive", "agent",
             "Business rules, edge cases, exception paths, cross-domain calls and a test scenario catalogue.",
             publish_docs=True),
    StageDef("gate_analysis", "Approve analysis", "gate",
             "Review the AS-IS documentation and deep dive.", ("as_is_doc", "deep_dive")),
    # Modulith design: find what this feature depends on, define the contracts with those domains, then design the
    # domain and the orchestrator on top of them.
    StageDef("domain_dependencies", "Cross-domain dependency finder", "agent",
             "For this feature, find exactly which other domains it calls, reads, writes or is called by, down to "
             "the service, DAO and table.", uses=("as_is_doc", "deep_dive")),
    StageDef("linked_domain_contracts", "Modulith design: Linked-domain contracts", "agent",
             "Define the contracts this feature needs from each linked domain (API, event or command), with the "
             "temporary monolith binding that provides each until the domain is migrated.",
             uses=("deep_dive", "domain_dependencies")),
    StageDef("domain_design", "Modulith design: Domain", "agent",
             "Design the feature's domain inside the monolith on the same stack, without changing the database or "
             "the UI.", uses=("as_is_doc", "deep_dive", "domain_dependencies", "linked_domain_contracts")),
    StageDef("orchestrator_design", "Modulith design: Orchestrator", "agent",
             "Design how the feature keeps working as is while other domains are not ready, and how it moves from "
             "their existing services to the contracts.",
             uses=("domain_dependencies", "linked_domain_contracts", "domain_design")),
    StageDef("integration_design", "Cross-domain integration design", "agent",
             "Decide how each cross-domain interaction is handled: API, event, saga, read model or deferred."),
    StageDef("gate_design", "Approve design", "gate",
             "Review the dependencies, the linked-domain contracts, the domain and orchestrator designs and the "
             "cross-domain integration decisions.",
             ("domain_dependencies", "linked_domain_contracts", "domain_design", "orchestrator_design",
              "integration_design")),
    StageDef("modular_monolith", "Module in monolith", "agent",
             "Extract a Java 8 / Spring 3 / iBatis module shaped like the future microservice."),
    StageDef("monolith_orchestrator", "Orchestrator in monolith", "agent",
             "Cross-domain features only: build the feature's orchestrator as its own Java module in the monolith "
             "(controller → orchestrator → domain module). It sequences the domain API and the calls to other "
             "domains, and holds no database access."),
    StageDef("microservice_scaffold", "Spring Boot microservice", "agent",
             "Lift the module into its own service (same Spring 3.2 / iBatis stack, executable jar) exposing the approved REST "
             "contract; code and SQL copied unchanged."),
    StageDef("orchestrator_service", "Orchestrator microservice", "agent",
             "Only when the feature has an orchestrator: lift the orchestrator module into its own Spring Boot "
             "service. It calls the domain microservice and the other domains over their APIs, and holds no "
             "database."),
    StageDef("independent_tests", "Independent tests", "agent",
             "Contract, integration and characterization tests covering every scenario."),
    StageDef("gate_tests", "Approve tests", "gate",
             "Review the module, microservice and scenario coverage.",
             ("modular_monolith", "monolith_orchestrator", "microservice_scaffold", "orchestrator_service",
              "independent_tests")),
    StageDef("modernise", "Modernisation", "agent",
             "Java 25, Spring Boot 4, iBatis → Spring Data JPA on the existing tables, code improvements."),
    StageDef("monolith_integration", "Monolith integration", "agent",
             "REST client and feature flag in the monolith to switch between module and microservice."),
    StageDef("gate_release", "Approve release", "gate",
             "Final review of the modernised service and monolith integration.",
             ("modernise", "monolith_integration")),
))

BOUNDED_CONTEXTS = Workflow((
    StageDef("bounded_contexts", "Bounded contexts", "agent",
             "Find the monolith's bounded contexts: what each one does, the packages and tables it owns, and where "
             "its code starts."),
    StageDef("gate_bounded_contexts", "Approve bounded contexts", "gate",
             "Review the bounded contexts. Approving adds new ones as domains and fills in their ownership.",
             ("bounded_contexts",)),
))

DOMAIN_FEATURES = Workflow((
    StageDef("domain_features", "Domain features", "agent",
             "List every feature of the domain, with its entry points, tables and calls to other domains."),
    StageDef("gate_features", "Approve feature list", "gate",
             "Review the feature list. Approving makes these features available to migrate.", ("domain_features",)),
))

# Built-in agents that no built-in workflow uses any more. They stay in the agent library for custom workflows.
SUPERSEDED = (
    StageDef("boundary_design", "Modular monolith design", "agent",
             "Bounded context, table ownership, cross-domain dependencies and the proposed REST contract. "
             "Replaced in the Standard migration workflow by the dependency finder and the Modulith design "
             "agents."),
    StageDef("wrapper_design", "Modulith design: Wrapper", "agent",
             "REST wrappers over the existing services of the other domains this feature depends on. Replaced in "
             "the Standard migration workflow by Modulith design: Linked-domain contracts, which defines the "
             "contract first and treats the wrapper as its temporary binding."),
)

# Agent stages of every built-in workflow: the built-in agents.
BUILTIN_STAGES = {s.key: s for s in (*(s for w in (BOUNDED_CONTEXTS, DOMAIN_FEATURES, STANDARD) for s in w.stages),
                                     *SUPERSEDED) if s.kind == "agent"}
