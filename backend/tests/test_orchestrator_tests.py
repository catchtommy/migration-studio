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
from tests.test_agents_domains import ctx  # noqa: F401
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


def test_a_reordered_workflow_keeps_the_gate_wording_someone_changed():
    steps = _v2_steps()
    for st in steps:
        if st["key"] == "gate_design":
            st["title"], st["description"] = "Architecture board", "Our board reviews the design."
    out = {st["key"]: st for st in workflow_store._with_orchestrator_tests(steps)}
    assert (out["gate_design"]["title"], out["gate_design"]["description"]) == ("Architecture board",
                                                                                "Our board reviews the design.")
    assert out["gate_tests"]["title"] == "Approve domain service"  # the old built-in title: the new one
    assert "orchestrator_service" not in out["gate_tests"]["reviews"]  # it has its own gate now
    for st in steps:
        if st["key"] == "gate_tests":
            st["title"] = "QA sign-off"  # a title of its own
    assert {st["key"]: st for st in workflow_store._with_orchestrator_tests(steps)}["gate_tests"]["title"] == \
        "QA sign-off"


def test_the_orchestrator_suite_needs_the_legacy_jdk_for_the_lifted_service(ctx):  # noqa: F811
    from app.agents import common
    from tests.test_agent_loops import _as

    ctx, _, _ = ctx
    _as(ctx, "orchestrator_tests", "Orchestrator acceptance tests", code={"changes_code": True})
    ctx.settings = {**ctx.settings, "jdk_legacy_home": ""}
    stuck = common.preflight(ctx)
    assert stuck and "set the Legacy JDK home in Project settings" in stuck[0]
    text = " ".join(defaults.INSTRUCTIONS["orchestrator_tests"].split())
    assert "built and started with the legacy JDK" in text and "use the target JDK, this step's own" in text


def test_publish_only_events_leave_nothing_to_orchestrate_and_a_rule_decided_elsewhere_is_theirs():
    design = " ".join(defaults.INSTRUCTIONS["modulith_design"].split())
    assert ("places nothing on the orchestrator" in design and "there is nothing to orchestrate: the domain does its "
            "work, its direct calls and its publishing itself" in design)
    assert "`orchestrator_module: none` with `no_orchestrator_reason`" in design
    assert "A rule another domain decides" in design and "is `other domain`, naming the contract" in design
    deps = " ".join(defaults.INSTRUCTIONS["dependency_analysis"].split())
    assert "the feature is `cross-domain`: its contract is designed" in deps


DESIGN = """domain: loan
feature: approve
module: loan-domain
artifact: com.acme:loan-domain
base_package: com.acme.loan
feature_package: com.acme.loan.approve
orchestrator_module: none
no_orchestrator_reason: Only publishes loan.approved, which notification turns into an email
owned_tables: []
read_only_tables: []
utility_modules: []
other_domain_dependencies: []
context_ports: []
schema_changes: []
ui_changes: []
stack_changes: []
god_module_dependencies: []
exceptions: []
"""


def _deps(strength: str) -> str:
    return f"""feature: approve
domain: loan
decision: {{scope: cross-domain, reason: Publishes loan.approved}}
dependencies:
  - domain: notification
    relation: calls
    direction: outbound
    strength: {strength}
    touchpoints: [{{kind: event, ref: loan.approved}}]
"""


def test_a_design_with_no_orchestrator_skips_all_three_orchestrator_steps_without_running(tmp_path):
    (tmp_path / "domain-design.yaml").write_text(DESIGN)
    (tmp_path / "domain-dependencies.yaml").write_text(_deps("side-effect"))
    for stage in ("monolith_orchestrator", "orchestrator_service", "orchestrator_tests"):
        reason = modulith_scope.skip_reason(stage, tmp_path)
        assert reason and reason.startswith(modulith_scope.NO_ORCHESTRATOR_DESIGNED), stage
        assert "Only publishes loan.approved" in reason
    note = modulith_scope.skip_note("monolith_orchestrator", "Orchestrator in monolith",
                                    modulith_scope.skip_reason("monolith_orchestrator", tmp_path))
    assert "The design has no orchestrator for this feature" in note
    assert modulith_scope.skip_reason("linked_domain_contracts", tmp_path) is None  # its contract is still designed
    (tmp_path / "domain-dependencies.yaml").write_text(_deps("hard"))  # e.g. the design edited by hand at its gate
    assert modulith_scope.skip_reason("monolith_orchestrator", tmp_path) is None  # it needs an answer: built


def test_no_orchestrator_is_accepted_only_for_a_feature_that_just_publishes(client, tmp_path):
    from app import domain_modules, projects

    (tmp_path / "domain-design.yaml").write_text(DESIGN)
    (tmp_path / "domain-dependencies.yaml").write_text(_deps("side-effect"))
    project = projects.DEFAULT
    problems = " ".join(domain_modules.design_problems(project, "loan", "approve", tmp_path))
    assert "orchestrator_module" not in problems  # publishes only: no orchestrator is right
    (tmp_path / "domain-dependencies.yaml").write_text(_deps("hard"))  # it needs an answer from notification
    problems = " ".join(domain_modules.design_problems(project, "loan", "approve", tmp_path))
    assert "it needs an answer from notification: add orchestrator_module" in problems
    (tmp_path / "domain-dependencies.yaml").write_text(_deps("side-effect"))
    (tmp_path / "domain-design.yaml").write_text(DESIGN.replace(
        "no_orchestrator_reason: Only publishes loan.approved, which notification turns into an email\n", ""))
    problems = " ".join(domain_modules.design_problems(project, "loan", "approve", tmp_path))
    assert "give no_orchestrator_reason" in problems
    assert modulith_scope.skip_reason("monolith_orchestrator", tmp_path) is None  # no reason: not skipped either


def test_a_feature_that_only_publishes_runs_without_an_orchestrator(client, monkeypatch):
    """Lean, end to end: Domain dependencies records the only cross-domain link as a side effect it publishes, the
    design has no orchestrator: the three orchestrator steps are skipped without an agent and their gate passes."""
    import re as _re

    from app.agents import stub

    original, ran = stub.StubAgent.run, []

    def run(self, ctx):
        ran.append(self.stage)
        out = original(self, ctx)
        deps, design = ctx.output_dir / "domain-dependencies.yaml", ctx.output_dir / "domain-design.yaml"
        if self.stage == "dependency_analysis":
            deps.write_text(deps.read_text().replace("strength: hard", "strength: side-effect"))
            # Nothing waits for the other domain's answer, so nothing is allocated to an orchestrator
            allocation = ctx.output_dir / "functional-allocation.yaml"
            allocation.write_text(_re.sub(r"placement: orchestrator\n    calls: .*\n    interactions: .*\n",
                                          "placement: domain\n", allocation.read_text())
                                  .replace("interactions: [X2]", "interactions: [X2, X1]"))  # X1 is the side effect now
        if self.stage == "modulith_design":
            text = _re.sub(r"orchestrator_module:\n(  .*\n)+", "orchestrator_module: none\n"
                           "no_orchestrator_reason: Only publishes the event the other domain reacts to\n",
                           design.read_text())
            design.write_text(text)
        return out

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run_id = start_run(client, {"domain": "loan", "operation": f"pub{next(_ids)}", "workflow": "lean"}).json()["id"]
    done, gates = _gates(client, run_id)
    assert done["status"] == "completed"
    assert [g for g, _ in gates] == ["gate_analysis", "gate_design", "gate_tests", "gate_release"]
    assert not {"monolith_orchestrator", "orchestrator_service", "orchestrator_tests"} & set(ran)  # no agent ran
    skipped = {s["stage"]: s["status"] for s in done["stages"]
               if s["stage"] in ("monolith_orchestrator", "orchestrator_service", "orchestrator_tests")}
    assert skipped == {k: "completed" for k in ("monolith_orchestrator", "orchestrator_service", "orchestrator_tests")}
    gate = next(s for s in done["stages"] if s["stage"] == "gate_orchestrator")
    assert gate["status"] == "approved" and "The design has no orchestrator" in gate["feedback"]
