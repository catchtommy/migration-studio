"""The Modulith scope decision: whether a feature needs the Linked-domain contracts, Orchestrator design and
Orchestrator in monolith steps.

The dependency finder records `decision.scope` in `domain-dependencies.yaml`. A feature that only uses its own
domain (`domain-only`) has no contract to define and nothing to orchestrate, so those steps are skipped without
running an agent.
"""

from pathlib import Path

from app import contracts_store

# Agent stages skipped for domain-only features (wrapper_design is the superseded step, kept for custom workflows).
SKIPPABLE = ("linked_domain_contracts", "wrapper_design", "orchestrator_design", "monolith_orchestrator",
             "orchestrator_service")
NOTHING_TO = {"linked_domain_contracts": "no contract to define with another domain",
              "wrapper_design": "nothing to wrap", "orchestrator_design": "nothing to orchestrate",
              "monolith_orchestrator": "no orchestrator to build: controllers call the domain module directly",
              "orchestrator_service": "no orchestrator microservice to build: the monolith calls the domain "
                                      "service directly"}
# Steps that build on the orchestrator module: skipped too when the Orchestrator in monolith step built none.
NEEDS_ORCHESTRATOR = ("orchestrator_service",)
NO_ORCHESTRATOR = "The Orchestrator in monolith step built no orchestrator"


def skip_reason(stage: str, output_dir: Path) -> str | None:
    """Why this stage is skipped for the run whose documents are in `output_dir`, or None when it must run: the stage
    is not skippable, the dependency finder's file is missing (a workflow without the finder) or invalid, or the
    scope is cross-domain. Reads the workspace copy, so a reviewer's edit of the decision is honoured."""
    if stage not in SKIPPABLE:
        return None
    if stage in NEEDS_ORCHESTRATOR:
        built = output_dir / contracts_store.ORCHESTRATOR_EXTRACTION_FILE
        if built.is_file():
            try:
                declared = contracts_store.parse_orchestrator_extraction(built.read_text(encoding="utf-8"))
            except contracts_store.InvalidDocument:
                declared = None
            if declared and declared["orchestrator"] == "none":
                return f"{NO_ORCHESTRATOR}: {declared['reason']}"
    f = output_dir / contracts_store.DOMAIN_DEPENDENCIES_FILE
    if not f.is_file():
        return None
    try:
        decision = contracts_store.load_domain_dependencies(f.read_text(encoding="utf-8"))["decision"]
    except contracts_store.InvalidDocument:
        return None
    if decision["scope"] != "domain-only":
        return None
    return f"Scope decision is domain-only: {decision['reason']}"


def skip_note(stage: str, title: str, reason: str) -> str:
    if reason.startswith(NO_ORCHESTRATOR):
        return (f"# {title}: skipped\n\nThis feature has no orchestrator, so there is {NOTHING_TO[stage]}.\n\n"
                f"**{reason}**\n\nTo build one, re-run the Orchestrator in monolith step once the orchestrator "
                "design has one.\n")
    return (f"# {title}: skipped\n\n"
            f"This feature only uses its own domain, so there is {NOTHING_TO[stage]}.\n\n"
            f"**Decision (cross-domain dependency finder):** {reason}\n\n"
            f"To run this step anyway, change `decision.scope` to `cross-domain` in "
            f"`{contracts_store.DOMAIN_DEPENDENCIES_FILE}` (and list the dependency), then reject the design gate "
            "so the steps re-run.\n")
