"""The lean migration workflow: fewer agents, beside the standard workflow, which stays as it is."""

import itertools
import re

from app import workflow_store
from app.agents import defaults
from app.orchestrator.stages import LEAN, STANDARD
from tests.conftest import start_run, wait_for

_ids = itertools.count()
LEAN_AGENTS = ("analysis", "modulith_design", "modernise_and_test")


def _approve(client, run_id):
    return client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})


def test_lean_workflow_is_seeded_beside_the_standard_one(client):
    lean = client.get("/api/workflows/lean").json()
    assert lean["name"] == "Lean migration"
    assert [s["key"] for s in lean["stages"]] == [
        "analysis", "gate_analysis", "modulith_design", "gate_design", "modular_monolith", "monolith_orchestrator",
        "microservice_scaffold", "orchestrator_service", "independent_tests", "gate_tests", "modernise_and_test",
        "monolith_integration", "gate_release"]
    # The standard workflow is unchanged, and still the one a run uses unless its domain defaults to another.
    standard = client.get("/api/workflows/standard").json()
    assert [s["key"] for s in standard["stages"]] == list(STANDARD.keys)
    assert workflow_store.default_for(None) == workflow_store.STANDARD_KEY


def test_lean_workflow_reuses_the_standard_steps_that_change_code(client):
    shared = [s for s in LEAN.stages if s.key in STANDARD.by_key and s.kind == "agent"]
    assert [s.key for s in shared] == ["modular_monolith", "monolith_orchestrator", "microservice_scaffold",
                                       "orchestrator_service", "independent_tests", "monolith_integration"]
    assert all(s is STANDARD.by_key[s.key] for s in shared)  # the same agents, titles and settings


def test_lean_agents_write_the_files_the_later_steps_and_checks_read():
    later = {"modular_monolith", "monolith_orchestrator", "microservice_scaffold", "orchestrator_service",
             "independent_tests", "monolith_integration"}
    written = {f for k in LEAN_AGENTS for f in defaults.OUTPUTS[k]} | {f for k in later for f in defaults.OUTPUTS[k]}
    standard_inputs = {f for k in ("as_is_doc", "deep_dive", "domain_dependencies", "linked_domain_contracts",
                                   "domain_design", "orchestrator_design", "integration_design")
                       for f in defaults.OUTPUTS[k] if f.endswith(".yaml") or f in ("test-scenarios.md",
                                                                                     "orchestrator-design.md",
                                                                                     "domain-design.md")}
    assert standard_inputs <= written, standard_inputs - written
    # Modernisation keeps its checks: the acceptance suite unchanged, the release limits, the boundary.
    assert defaults._CHECKS["modernise_and_test"] == defaults._CHECKS["modernise"]


def test_lean_agents_include_shared_texts_rather_than_repeat_them():
    for key in LEAN_AGENTS:
        template = defaults.TEMPLATES[key]
        used = set(re.findall(r"\{\{(\w+)\}\}", template))
        assert "fixed_rules" in used, key
        assert used <= set(defaults.SHARED_TEXTS), key
        for name, (_title, _description, text) in defaults.SHARED_TEXTS.items():
            if name not in ("preamble", "feature_rules"):
                assert text not in template, f"{key} repeats the shared text {name} instead of including it"


def test_the_new_shared_texts_leave_the_standard_agents_as_they_were():
    for name in ("fixed_rules", "not_contracts"):
        text = defaults.SHARED_TEXTS[name][2]
        assert not [k for k, v in defaults.INSTRUCTIONS.items() if k not in LEAN_AGENTS and text in v], name


def test_a_cross_domain_feature_runs_through_the_lean_workflow(client):
    domain = "loan"
    run = start_run(client, {"domain": domain, "operation": f"lean{next(_ids)}", "workflow": "lean"})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    for gate, reviews in (("gate_analysis", ["analysis"]), ("gate_design", ["modulith_design"]),
                          ("gate_tests", ["modular_monolith", "monolith_orchestrator", "microservice_scaffold",
                                          "orchestrator_service", "independent_tests"]),
                          ("gate_release", ["modernise_and_test", "monolith_integration"])):
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"] == {"gate": gate, "reviews": reviews}
        assert _approve(client, run_id).status_code == 200

    done = wait_for(client, run_id, {"completed"})
    assert [s["stage"] for s in done["stages"] if not s["stage"].startswith("gate_")] == [
        "analysis", "modulith_design", "modular_monolith", "monolith_orchestrator", "microservice_scaffold",
        "orchestrator_service", "independent_tests", "modernise_and_test", "monolith_integration"]
    # The design gate recorded the merged design's contracts in the catalogue, as the standard design gate does.
    consumed = [c for c in client.get("/api/contracts").json() if domain in (c.get("consumers") or [])]
    assert consumed


def test_an_existing_database_gets_the_lean_workflow_with_the_standard_repositories(client):
    from app.db.models import GLOBAL, SHARED, Setting, WorkflowDef
    from app.db.session import session_scope

    repo = {"repository": "https://git.example.com/acme/{domain}-service.git", "base_branch": "main"}
    with session_scope() as s:
        standard = s.get(WorkflowDef, (SHARED, workflow_store.STANDARD_KEY))
        before = standard.steps
        standard.steps = [{**st, "code": repo} if st.get("key") in ("modernise", "microservice_scaffold") else st
                          for st in before]
        s.delete(s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY)))
        s.delete(s.get(Setting, (GLOBAL, "_added_lean_workflow")))
    try:
        workflow_store.seed()
        workflow_store.seed()  # once only
        with session_scope() as s:
            steps = {st["key"]: st for st in s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY)).steps}
        assert steps["microservice_scaffold"]["code"] == repo  # the same step: its own settings
        assert steps["modernise_and_test"]["code"] == repo  # the merged step: the settings of the step it replaces
        assert not steps["analysis"].get("code")
    finally:
        with session_scope() as s:
            s.get(WorkflowDef, (SHARED, workflow_store.STANDARD_KEY)).steps = before
