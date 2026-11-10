"""The Modulith scope decision: whether a feature needs the Linked-domain contracts, Orchestrator design and
Orchestrator in monolith steps.

The dependency step (Domain dependencies in the lean workflow, the dependency finder in the standard one) records
`decision.scope` in `domain-dependencies.yaml`. A feature that only uses its own
domain (`domain-only`) has no contract to define and nothing to orchestrate, so those steps are skipped without
running an agent.
"""

from pathlib import Path

from app import contracts_store

# Agent stages skipped for domain-only features (wrapper_design is the superseded step, kept for custom workflows).
SKIPPABLE = ("linked_domain_contracts", "wrapper_design", "orchestrator_design", "monolith_orchestrator",
             "orchestrator_service", "orchestrator_tests")
NOTHING_TO = {"linked_domain_contracts": "no contract to define with another domain",
              "wrapper_design": "nothing to wrap", "orchestrator_design": "nothing to orchestrate",
              "monolith_orchestrator": "no orchestrator to build: controllers call the domain module directly",
              "orchestrator_service": "no orchestrator microservice to build: the monolith calls the domain "
                                      "service directly",
              "orchestrator_tests": "no orchestrator service to test: the domain service's acceptance suite covers "
                                    "the feature"}
# Steps that build on the orchestrator module: skipped too when the Orchestrator in monolith step built none.
NEEDS_ORCHESTRATOR = ("orchestrator_service", "orchestrator_tests")
NO_ORCHESTRATOR = "The Orchestrator in monolith step built no orchestrator"
# The design's decision: a cross-domain feature that only publishes to the other domains has no orchestrator.
NO_ORCHESTRATOR_DESIGNED = "The design has no orchestrator"
ORCHESTRATOR_STEPS = ("monolith_orchestrator", *NEEDS_ORCHESTRATOR)


def skip_reason(stage: str, output_dir: Path) -> str | None:
    """Why this stage is skipped for the run whose documents are in `output_dir`, or None when it must run: the stage
    is not skippable, the dependency finder's file is missing (a workflow without the finder) or invalid, or the
    scope is cross-domain. Reads the workspace copy, so a reviewer's edit of the decision is honoured."""
    if stage not in SKIPPABLE:
        return None
    if stage in ORCHESTRATOR_STEPS:
        designed = _designed_without_orchestrator(output_dir)
        if designed:
            return f"{NO_ORCHESTRATOR_DESIGNED}: {designed}"
    if stage in NEEDS_ORCHESTRATOR:
        built = output_dir / contracts_store.ORCHESTRATOR_EXTRACTION_FILE
        if built.is_file():
            try:
                declared = contracts_store.parse_orchestrator_extraction(built.read_text(encoding="utf-8"))
            except contracts_store.InvalidDocument:
                declared = None
            if declared and declared["orchestrator"] == "none":
                return f"{NO_ORCHESTRATOR}: {declared['reason']}"
    if stage == "orchestrator_tests":  # no orchestrator service was built: nothing to test
        built = output_dir / contracts_store.ORCHESTRATOR_SERVICE_FILE
        if built.is_file():
            try:
                declared = contracts_store.parse_orchestrator_service(built.read_text(encoding="utf-8"))
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


# The steps that write domain-dependencies.yaml (the scope decision), by workflow: the first one a workflow has.
DECIDED_BY = ("dependency_analysis", "domain_dependencies", "analysis")


def _designed_without_orchestrator(output_dir: Path) -> str | None:
    """The reason the design gives for having no orchestrator (domain-design.yaml `orchestrator_module: none` with
    `no_orchestrator_reason`), when it gives one; None otherwise (domain-only features are skipped by their scope)."""
    import yaml

    f = output_dir / contracts_store.DOMAIN_DESIGN_FILE
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else None
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict) or data.get("orchestrator_module") not in ("none", None) \
            or "orchestrator_module" not in data:
        return None
    reason = str(data.get("no_orchestrator_reason") or "").strip()
    if not reason:
        return None
    # With an approved functional allocation, it decides: no orchestrator when it places nothing on one (the domain
    # calls the domains it may call directly and publishes its events itself), even when the design was edited by
    # hand at its gate.
    allocation = contracts_store.load_functional_allocation(output_dir)
    if allocation is not None:
        return None if contracts_store.has_orchestrator(allocation) else reason
    if (output_dir / contracts_store.FUNCTIONAL_ALLOCATION_FILE).is_file():
        return None  # present but invalid: never skip on a guess (the steps run, and their checks report it)
    # Runs from before the allocation: only for a feature that just publishes; one needing an answer from another
    # domain keeps its orchestrator.
    deps = output_dir / contracts_store.DOMAIN_DEPENDENCIES_FILE
    try:
        found = contracts_store.load_domain_dependencies(deps.read_text(encoding="utf-8"))["dependencies"] \
            if deps.is_file() else []
    except contracts_store.InvalidDocument:
        return None
    if any(d.get("direction", "outbound") == "outbound" and d.get("strength") != "side-effect" for d in found):
        return None
    return reason


def skip_note(stage: str, title: str, reason: str, decided_by: str = "the dependency step") -> str:
    if reason.startswith(NO_ORCHESTRATOR_DESIGNED):
        return (f"# {title}: skipped\n\nThe design has no orchestrator for this feature, so there is "
                f"{NOTHING_TO[stage]}.\n\n**{reason}**\n\nNothing is placed on an orchestrator: the domain publishes "
                "its events, and calls any domain it may call directly, itself. To build one, change the functional "
                "allocation (re-run from the dependency step) and the design.\n")
    if reason.startswith(NO_ORCHESTRATOR):
        return (f"# {title}: skipped\n\nThis feature has no orchestrator, so there is {NOTHING_TO[stage]}.\n\n"
                f"**{reason}**\n\nTo build one, re-run the Orchestrator in monolith step once the orchestrator "
                "design has one.\n")
    return (f"# {title}: skipped\n\n"
            f"This feature only uses its own domain, so there is {NOTHING_TO[stage]}.\n\n"
            f"**Decision ({decided_by}):** {reason}\n\n"
            f"To run this step anyway, the decision must change, and it was approved with the analysis, so its "
            f"document is read-only now: re-run the run from {decided_by} (Restart on that step), with notes saying "
            f"which dependency it missed, so it writes `decision.scope: cross-domain` in "
            f"`{contracts_store.DOMAIN_DEPENDENCIES_FILE}`; the steps after it run again, this one included.\n")
