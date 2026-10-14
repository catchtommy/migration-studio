"""The Modulith scope decision: whether a feature needs the Wrapper and Orchestrator design steps.

The dependency finder records `decision.scope` in `domain-dependencies.yaml`. A feature that only uses its own
domain (`domain-only`) has nothing to wrap or orchestrate, so those two steps are skipped without running an agent.
"""

from pathlib import Path

from app import contracts_store

SKIPPABLE = ("wrapper_design", "orchestrator_design")  # agent stages skipped for domain-only features
NOTHING_TO = {"wrapper_design": "nothing to wrap", "orchestrator_design": "nothing to orchestrate"}


def skip_reason(stage: str, output_dir: Path) -> str | None:
    """Why this stage is skipped for the run whose documents are in `output_dir`, or None when it must run: the stage
    is not skippable, the dependency finder's file is missing (a workflow without the finder) or invalid, or the
    scope is cross-domain. Reads the workspace copy, so a reviewer's edit of the decision is honoured."""
    if stage not in SKIPPABLE:
        return None
    f = output_dir / contracts_store.DOMAIN_DEPENDENCIES_FILE
    if not f.is_file():
        return None
    try:
        decision = contracts_store.load_domain_dependencies(f.read_text())["decision"]
    except contracts_store.InvalidDocument:
        return None
    if decision["scope"] != "domain-only":
        return None
    return f"Scope decision is domain-only: {decision['reason']}"


def skip_note(stage: str, title: str, reason: str) -> str:
    return (f"# {title}: skipped\n\n"
            f"This feature only uses its own domain, so there is {NOTHING_TO[stage]}.\n\n"
            f"**Decision (cross-domain dependency finder):** {reason}\n\n"
            f"To run this step anyway, change `decision.scope` to `cross-domain` in "
            f"`{contracts_store.DOMAIN_DEPENDENCIES_FILE}` (and list the dependency), then reject the design gate "
            "so the steps re-run.\n")
