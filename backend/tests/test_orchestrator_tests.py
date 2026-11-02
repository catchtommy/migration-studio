"""The Lean workflow tests the domain service and the orchestrator service separately, each in its own repository and
with its own gate: Independent tests → Approve domain service → Orchestrator microservice → Orchestrator acceptance
tests → Approve orchestrator service. A requirement counts as covered only when its owner's suite proves it (report
only)."""

import itertools

from sqlalchemy import select

from app import delivery, modulith_scope, workflow_store
from app import test_guardrails as tg
from app.agents import defaults
from app.db.models import GLOBAL, SHARED, Agent, Approval, Setting, WorkflowDef
from app.db.session import session_scope
from app.orchestrator import graph
from app.orchestrator.stages import LEAN, STANDARD
from tests.conftest import approve, start_run, wait_for
from tests.test_test_guardrails import put, service, suite  # noqa: F401

_ids = itertools.count()


def _gates(client, run_id):
    run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    seen = []
    while run["status"] == "awaiting_approval":
        seen.append((run["pending_gate"]["gate"], run["pending_gate"]["reviews"]))
        assert approve(client, run_id).status_code == 200
        run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    return run, seen


def test_the_domain_service_is_approved_before_the_orchestrator_is_built():
    keys = LEAN.keys
    assert keys[keys.index("microservice_scaffold"):keys.index("modernise_and_test")] == [
        "microservice_scaffold", "independent_tests", "gate_tests", "orchestrator_service", "orchestrator_tests",
        "gate_orchestrator"]
    assert LEAN.by_key["gate_tests"].reviews == ("modular_monolith", "monolith_orchestrator", "microservice_scaffold",
                                                 "independent_tests")
    assert LEAN.by_key["gate_orchestrator"].reviews == ("orchestrator_service", "orchestrator_tests")
    assert "orchestrator_tests" not in STANDARD.by_key and STANDARD.by_key["gate_tests"].title == "Approve tests"


def test_a_cross_domain_feature_passes_both_test_gates(client):
    run_id = start_run(client, {"domain": "loan", "operation": f"orch{next(_ids)}", "workflow": "lean"}).json()["id"]
    done, gates = _gates(client, run_id)
    assert [g for g, _ in gates] == ["gate_analysis", "gate_design", "gate_tests", "gate_orchestrator", "gate_release"]
    assert dict(gates)["gate_orchestrator"] == ["orchestrator_service", "orchestrator_tests"]
    steps = [s["stage"] for s in done["stages"] if not s["stage"].startswith("gate_")]
    assert steps.index("independent_tests") < steps.index("orchestrator_service") < steps.index("orchestrator_tests")
    coverage = done["functional_coverage"]
    fr = [i for i in coverage["ids"] if i["kind"] == "FR"]
    assert fr and all(i["proven_by_owner_suite"] for i in fr)  # each requirement, by its owner's suite


def test_a_domain_only_feature_skips_the_orchestrator_and_its_gate_passes_by_itself(client, monkeypatch):
    original = modulith_scope.skip_reason
    monkeypatch.setattr(modulith_scope, "skip_reason", lambda stage, out: (
        "Scope decision is domain-only: own tables only" if stage in modulith_scope.SKIPPABLE else original(stage, out)))
    run_id = start_run(client, {"domain": "loan", "operation": f"own{next(_ids)}", "workflow": "lean"}).json()["id"]
    done, gates = _gates(client, run_id)
    assert [g for g, _ in gates] == ["gate_analysis", "gate_design", "gate_tests", "gate_release"]  # no wait
    gate = next(s for s in done["stages"] if s["stage"] == "gate_orchestrator")
    assert gate["status"] == "approved" and "domain-only" in gate["feedback"]
    with session_scope() as s:
        record = s.scalar(select(Approval).where(Approval.run_id == run_id, Approval.gate == "gate_orchestrator"))
        assert record.reviewer == graph.SYSTEM_REVIEWER and record.decision == "approve"
    skipped = [s for s in done["stages"] if s["stage"] in ("orchestrator_service", "orchestrator_tests")]
    assert [s["status"] for s in skipped] == ["completed", "completed"]  # each with its skip note


def test_the_tests_step_skips_when_the_orchestrator_service_declared_none(tmp_path):
    (tmp_path / "orchestrator-service-extraction.yaml").write_text(
        "feature: approve\ndomain: loan\norchestrator: none\nreason: The module holds the sequence alone\n")
    assert "built no orchestrator" in modulith_scope.skip_reason("orchestrator_tests", tmp_path)


def test_the_orchestrator_suite_lives_in_the_orchestrator_repository(client):
    assert "orchestrator_tests" in delivery.CODE_AGENTS
    inputs = {"orchestrator_service": {"repository": "https://git.example/loan-orchestrator.git", "base_branch": "main"}}
    assert delivery.agent_repository(inputs, "orchestrator_tests") == (
        "https://git.example/loan-orchestrator.git", "main", "orchestrator_service")  # inherited
    config = defaults.default_config("orchestrator_tests")
    assert config["checks"] == ["acceptance_test_guardrails"] and "check_test_guardrails" in config["tools"]
    assert config["jdk"] == "modern" and config["outputs"] == ["orchestrator-test-report.md"]


def test_the_orchestrator_suite_stays_black_box(service):  # noqa: F811
    root, base = service
    suite(root)
    assert tg.acceptance(root, base, None, None, ["com.acme.orchestrator.customer"]) == []
    put(root, "acceptance-tests/src/test/java/com/acme/acceptance/OrderTest.java",
        "package com.acme.acceptance;\nimport com.acme.orchestrator.customer.approve.CustomerOrchestrator;\nclass OrderTest {}\n")
    put(root, "src/main/java/com/acme/customer/Changed.java", "class Changed {}\n")
    found = " ".join(tg.acceptance(root, base, None, None, ["com.acme.orchestrator.customer"]))
    assert "imports com.acme.orchestrator.customer" in found and "is outside acceptance-tests/" in found


def test_independent_tests_covers_the_domains_ids_and_names_the_new_step():
    domain, orchestrator = defaults.INSTRUCTIONS["independent_tests"], defaults.INSTRUCTIONS["orchestrator_tests"]
    assert "the scenarios of `test-scenarios.md` that the domain owns" in domain
    assert "Orchestrator acceptance tests step tests it" in defaults.GUARDRAILS["independent_tests"]
    assert "against the real domain service" in orchestrator and "The domain service is never stubbed" in orchestrator
    assert "orchestrator-test-report.md" in defaults.INSTRUCTIONS["modernise_and_test"]
    assert "counts as covered only when this suite proves it" in orchestrator


def test_acceptance_suites_follow_a_fix_by_running_unchanged():
    assert graph.ACCEPTANCE_STEPS == ("independent_tests", "orchestrator_tests")
    assert "Don't change the tests to make them pass" in graph.ACCEPTANCE_FOLLOW


def _v2_steps():
    """The lean workflow as stored before the orchestrator got its own suite and gate."""
    new = {st["key"]: st for st in workflow_store.steps_of(LEAN)}
    steps = []
    for key in workflow_store._LEAN_V2_KEYS:
        st = dict(new[key])
        if key == "gate_tests":
            st.update(title="Approve tests", reviews=["modular_monolith", "monolith_orchestrator",
                                                      "microservice_scaffold", "orchestrator_service",
                                                      "independent_tests"], checklist=["Our own check"])
        if key == "orchestrator_service":
            st["code"] = {"changes_code": True}
        steps.append(st)
    return steps


def test_stored_lean_workflows_take_the_new_order_once_and_a_changed_one_gets_the_step_in_place(client):
    from tests.test_projects import new_project

    project = new_project(client)
    reordered = _v2_steps()
    reordered[-3], reordered[-2] = reordered[-2], reordered[-3]  # integration before modernisation
    with session_scope() as s:
        shared = s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY))
        before = shared.steps
        shared.steps = _v2_steps()
        s.add(WorkflowDef(project=project, key=workflow_store.LEAN_KEY, name="Lean migration", steps=reordered))
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key.in_(
            ("_lean_orchestrator_tests", "_lean_orchestrator_tests_custom"))).delete()
    try:
        workflow_store.seed()
        workflow_store.seed()  # once only
        with session_scope() as s:
            steps = s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY)).steps
            assert [st["key"] for st in steps] == LEAN.keys
            by = {st["key"]: st for st in steps}
            assert by["gate_tests"]["title"] == "Approve domain service"
            assert "orchestrator_service" not in by["gate_tests"]["reviews"]
            assert by["gate_tests"]["checklist"] == ["Our own check"]  # kept
            assert by["orchestrator_tests"]["code"] == {"changes_code": True}  # where the orchestrator service is
            assert by["orchestrator_service"]["code"] == {"changes_code": True}
            copy = s.get(WorkflowDef, (project, workflow_store.LEAN_KEY)).steps
            keys = [st["key"] for st in copy]  # reordered by someone: the step is added, nothing moves
            assert keys == [k for k in [st["key"] for st in reordered] for k in
                            ((k, "orchestrator_tests") if k == "orchestrator_service" else (k,))]
            gate = next(st for st in copy if st["key"] == "gate_tests")
            assert gate["reviews"][gate["reviews"].index("orchestrator_service") + 1] == "orchestrator_tests"
            assert next(st for st in copy if st["key"] == "orchestrator_tests")["code"] == {"changes_code": True}
    finally:
        with session_scope() as s:
            s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY)).steps = before
            s.delete(s.get(WorkflowDef, (project, workflow_store.LEAN_KEY)))


def test_the_new_agent_reaches_an_existing_database_with_independent_tests_choices(client):
    from app import agent_store

    with session_scope() as s:
        source = s.get(Agent, (SHARED, "independent_tests"))
        before = (source.mode, source.model)
        source.mode, source.model = "llm", "claude-sonnet-5-5"
        existing = s.get(Agent, (SHARED, "orchestrator_tests"))
        kept = {f: getattr(existing, f) for f in agent_store.FIELDS}
        s.delete(existing)
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == "_added_orchestrator_tests").delete()
    try:
        with session_scope() as s:
            agent_store._add_later_builtins(s)
        with session_scope() as s:
            added = s.get(Agent, (SHARED, "orchestrator_tests"))
            assert (added.mode, added.model) == ("llm", "claude-sonnet-5-5")
    finally:
        with session_scope() as s:
            row = s.get(Agent, (SHARED, "independent_tests"))
            row.mode, row.model = before
            new = s.get(Agent, (SHARED, "orchestrator_tests"))
            for f, v in kept.items():
                setattr(new, f, v)


def test_a_run_whose_steps_moved_around_it_cant_take_the_upcoming_steps():
    from app import run_workflow

    old = [dict(st, title=st.get("title") or st["key"]) for st in _v2_steps()]
    new = [dict(st, title=st.get("title") or st["key"]) for st in workflow_store.steps_of(LEAN)]
    keys = [d["key"] for d in old]
    for at in ("gate_tests", "orchestrator_service"):  # ran Orchestrator microservice / hasn't run Independent tests
        update = run_workflow._upcoming(old, keys, keys.index(at), new)
        assert "moved" in update["blocked"], at
    early = run_workflow._upcoming(old, keys, keys.index("modular_monolith"), new)  # before the moved steps: fine
    assert "blocked" not in early and [d["key"] for d in early["stages"]] == LEAN.keys


def test_a_fresh_lean_workflow_takes_the_orchestrators_code_flag():
    standard = [dict(st, code={"changes_code": True}) if st["key"] == "orchestrator_service" else st
                for st in workflow_store.steps_of(STANDARD)]
    steps = {st["key"]: st for st in workflow_store._lean_steps(standard)}
    assert steps["orchestrator_tests"]["code"] == {"changes_code": True}


def test_an_untouched_description_follows_the_release_and_an_edited_one_is_kept(client):
    from app import agent_store

    old = agent_store.PREVIOUS_DESCRIPTIONS["independent_tests"]
    with session_scope() as s:
        row = s.get(Agent, (SHARED, "independent_tests"))
        before = row.description
        row.description = old
    try:
        with session_scope() as s:
            agent_store._update_untouched_descriptions(s)
        with session_scope() as s:
            assert "for the scenarios the domain owns" in s.get(Agent, (SHARED, "independent_tests")).description
            s.get(Agent, (SHARED, "independent_tests")).description = "Our own words."
        with session_scope() as s:
            agent_store._update_untouched_descriptions(s)
        with session_scope() as s:
            assert s.get(Agent, (SHARED, "independent_tests")).description == "Our own words."
    finally:
        with session_scope() as s:
            s.get(Agent, (SHARED, "independent_tests")).description = before


def test_the_orchestrator_suite_routes_its_findings_to_a_step_that_can_take_them():
    text = defaults.INSTRUCTIONS["orchestrator_tests"]
    assert "goes back to Orchestrator microservice from this step's gate" in text
    assert "needs a re-run from that step (Restart)" in text
    assert "in a run without one, the scenarios that go" in text
