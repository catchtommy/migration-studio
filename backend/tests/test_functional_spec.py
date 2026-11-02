"""The Lean workflow's functional specification: Analysis writes it beside the AS-IS document and the test scenarios,
joined by ids; Domain dependencies links its interactions to it; Modulith design allocates every id; the later steps
report the ids they cover, merged into the feature's coverage (report only)."""

import itertools

import pytest
from sqlalchemy import select

from app import functional_spec as fs
from app import modulith_scope, workflow_store, workspace
from app.agents import defaults, llm
from app.db.models import GLOBAL, SHARED, Agent, AuditEvent, Setting, WorkflowDef
from app.db.session import session_scope
from app.orchestrator.stages import LEAN, LEAN_ANALYSIS_CHECKLIST, STANDARD
from tests.conftest import approve, start_run, wait_for
from tests.test_agents_domains import ScriptedClient, _reply, _text, _tool, ctx  # noqa: F401
from tests.test_agent_loops import _as

_ids = itertools.count()

SPEC = """# Functional specification: approve
Audience: business users and business analysts

## 1. Purpose and business context
Approve loans.
## 2. Scope
Approval only.
## 3. Users and roles
| Role | What they can do | What they can't |
|---|---|---|
| Officer | Approve | - |
## 4. Business process
1. The officer approves.
## 5. Functional requirements
| ID | The system shall … | Criticality | Rules | Other party |
|---|---|---|---|---|
| FR-001 | record the approval | to be confirmed | BR-001 | - |
| FR-002 | check the customer's identity status | to be confirmed | - | customer records |
## 6. Business rules
| ID | Rule | When it applies | Outcome |
|---|---|---|---|
| BR-001 | An amount over the limit needs a manager | On approve | Rejected with MSG-01 |
## 7. Use cases
### UC-01 Approve
Main flow; exception ends in MSG-01.
## 8. Information captured and shown
| Item (as on screen) | Meaning | Mandatory | Allowed values |
|---|---|---|---|
| Amount | Loan amount | Yes | > 0 |
## 9. Messages and outcomes
| ID | When | What the user sees (exact text) |
|---|---|---|
| MSG-01 | Over the limit | A manager must approve this amount. |
## 10. Notifications, reports and records
None.
## 11. Business expectations
None observed.
## 12. Assumptions and open questions
None.
## 13. Glossary
None.
"""

AS_IS = """# AS-IS
## Rule enforcement
| BR id | Enforced in | Code location | Condition as implemented | What the code does |
|---|---|---|---|---|
| BR-001 | server | LoanService.java:40 | amount > limit | throws LimitException |

## Messages
| MSG id | Kind | Trigger | Produced by | View or message key |
|---|---|---|---|---|
| MSG-01 | error | UI shows the amount over the limit | LimitException in LoanController | loan.limit |
"""

SCENARIOS = """# Test scenarios: approve
## Summary
| Scenarios | Positive | Negative | Boundary | Exception | Requirements covered |
## Scenarios
### TS-001 Over the limit
- Covers: FR-001, BR-001, MSG-01
- Expected result: MSG-01 shows
### TS-002 Identity checked
- Covers: FR-002
- Expected result:
  the approval goes on
## Not covered
None.
"""


def test_three_documents_that_agree_pass():
    assert fs.analysis_problems(AS_IS, SPEC, SCENARIOS) == []
    assert fs.spec_ids(SPEC) == {"FR": ["FR-001", "FR-002"], "BR": ["BR-001"], "MSG": ["MSG-01"], "UC": ["UC-01"]}


@pytest.mark.parametrize("as_is, spec, scenarios, expected", [
    (AS_IS, SPEC.replace("## 9. Messages and outcomes", "## Messages"), SCENARIOS, "missing sections"),
    (AS_IS, SPEC.replace("| BR-001 | An amount", "| BR-001 | Twice |  |  |\n| BR-001 | An amount"), SCENARIOS,
     "BR-001 defined more than once"),
    (AS_IS.replace("| BR-001 | server", "| BR-002 | server"), SPEC, SCENARIOS, "BR-002 in as-is.md but not in"),
    (AS_IS.replace("## Messages", "## Errors"), SPEC, SCENARIOS, 'no "Messages" section'),
    (AS_IS, SPEC, SCENARIOS.replace("- Covers: FR-002\n", ""), "TS-002 without a `Covers:` line"),
    (AS_IS, SPEC, SCENARIOS.replace("- Covers: FR-002", "- Covers: FR-001"), "FR-002 of functional-spec.md not covered"),
    (AS_IS, SPEC, SCENARIOS.replace("- Expected result: MSG-01 shows\n", ""), "TS-001 without an expected result"),
    (AS_IS, SPEC, SCENARIOS.replace("FR-002\n", "FR-002, BR-009\n"), "covers BR-009, which functional-spec.md"),
    (AS_IS, SPEC, SCENARIOS.replace("## Not covered\nNone.\n", ""), "missing sections: Not covered"),
])
def test_analysis_is_sent_back_when_its_documents_disagree(as_is, spec, scenarios, expected):
    assert any(expected in p for p in fs.analysis_problems(as_is, spec, scenarios)), \
        fs.analysis_problems(as_is, spec, scenarios)


def test_technical_words_in_the_specification_are_reported_not_blocking():
    spec = SPEC.replace("record the approval", "call LoanService and insert into LOAN_APPROVAL via approve.do")
    found = " ".join(fs.wording_problems(spec))
    assert "class names" in found and "table or column names" in found and "file names" in found
    assert fs.wording_problems(SPEC) == []
    assert fs.analysis_problems(AS_IS, spec, SCENARIOS) == []  # a heuristic: never a required check


def test_modulith_design_allocates_every_id_once():
    design = "# Design\n## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n" \
             "| FR-001 | domain | LoanApi#approve |\n| FR-002 | orchestrator | LoanOrchestrator |\n" \
             "| BR-001 | domain | LoanApi#approve |\n| MSG-01 | UI unchanged | approve.jsp |\n"
    assert fs.allocation_problems(SPEC, design) == []
    missing = design.replace("| MSG-01 | UI unchanged | approve.jsp |\n", "")
    assert "lacks MSG-01" in " ".join(fs.allocation_problems(SPEC, missing))
    bad = design.replace("| orchestrator |", "| somewhere |")
    assert "must be domain, orchestrator, other domain or UI unchanged" in " ".join(fs.allocation_problems(SPEC, bad))
    assert "no \"Functional allocation\" section" in fs.allocation_problems(SPEC, "# Design\n")[0]


def _docs(**files):
    return {f".migration/loan/approve/{name.replace('_', '-')}": text for name, text in files.items()}


def _coverage(*ids):
    return "# Step\n## Functional coverage\n| Id | Covered by | Status |\n|---|---|---|\n" + "".join(
        f"| {i} | test_{i} | Covered |\n" for i in ids)


def test_coverage_is_per_feature_an_orchestrator_id_is_not_the_domain_services_gap():
    design = ("## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n| FR-001 | domain | a |\n"
              "| FR-002 | orchestrator | b |\n| BR-001 | domain | a |\n| MSG-01 | domain | a |\n")
    docs = {**_docs(**{"functional-spec.md": SPEC, "as-is.md": AS_IS, "test-scenarios.md": SCENARIOS,
                       "domain-design.md": design}),
            ".migration/loan/approve/modernisation.md": _coverage("FR-001", "BR-001", "MSG-01")}
    steps = [s.to_dict() for s in LEAN.stages]
    report = fs.report(docs, ".migration/loan/approve", steps)
    assert report["available"] and report["gaps"] == []  # FR-002 is the orchestrator's: its suite hasn't run yet
    docs[".migration/loan/approve/orchestrator-module.md"] = _coverage("FR-002")  # a unit test: not the owner's suite
    docs[".migration/loan/approve/orchestrator-test-report.md"] = _coverage()  # the owner's suite didn't prove it
    assert fs.report(docs, ".migration/loan/approve", steps)["gaps"] == ["FR-002"]
    docs[".migration/loan/approve/orchestrator-test-report.md"] = _coverage("FR-002")
    report = fs.report(docs, ".migration/loan/approve", steps)
    by_id = {i["id"]: i for i in report["ids"]}
    assert report["gaps"] == [] and by_id["FR-002"]["proven_by_owner_suite"]
    assert by_id["FR-002"]["owner"] == "orchestrator"
    assert report["summary"]["FR"] == {"total": 2, "covered": 1}  # FR-001: the domain suite hasn't reported yet


def test_other_domains_and_ui_only_ids_are_never_gaps():
    design = ("## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n| FR-001 | domain | a |\n"
              "| FR-002 | other domain (customer) | customer.get-kyc |\n| BR-001 | UI unchanged | approve.jsp |\n"
              "| MSG-01 | domain | a |\n")
    docs = {**_docs(**{"functional-spec.md": SPEC, "domain-design.md": design}),
            ".migration/loan/approve/modernisation.md": _coverage("FR-001", "MSG-01")}
    report = fs.report(docs, ".migration/loan/approve", [s.to_dict() for s in LEAN.stages])
    assert report["gaps"] == []
    assert {i["id"]: i["owner"] for i in report["ids"]}["BR-001"] == "ui unchanged"


def test_not_covered_rows_are_listed_with_their_reason_and_logged():
    docs = {**_docs(**{"functional-spec.md": SPEC}),
            ".migration/loan/approve/modernisation.md": _coverage("FR-001") +
            "| BR-001 | - | Not covered: enforced in the JSP |\n"}
    report = fs.report(docs, ".migration/loan/approve", [s.to_dict() for s in LEAN.stages])
    br = next(i for i in report["ids"] if i["id"] == "BR-001")
    assert br["not_covered"][0]["reason"] == "enforced in the JSP"
    line = fs.step_line(docs[".migration/loan/approve/modernisation.md"], report, "modernise_and_test")
    assert "1 id(s) covered by this step" in line and "BR-001 (enforced in the JSP)" in line


def test_a_run_without_a_specification_says_so():
    report = fs.report(_docs(**{"as-is.md": AS_IS}), ".migration/loan/approve")
    assert report == {"available": False, "message": report["message"]} and "No functional specification" in \
        report["message"]


def _dependency_docs(tmp_path, interactions: str, internal: str = ""):
    for name, text in (("functional-spec.md", SPEC), ("test-scenarios.md", SCENARIOS)):
        (tmp_path / name).write_text(text)
    (tmp_path / "cross-domain.yaml").write_text(f"interactions:\n{interactions}")
    (tmp_path / "domain-dependencies.yaml").write_text(f"decision: {{scope: cross-domain}}\n{internal}")
    return tmp_path


X1 = "  - {id: X1, target: customer, kind: query, summary: KYC, requirements: [FR-002], scenarios: [TS-002]}\n"


def test_domain_dependencies_links_interactions_to_the_specification(tmp_path):
    assert fs.trace_problems(_dependency_docs(tmp_path, X1)) == []
    unknown = X1.replace("[FR-002]", "[FR-002, FR-404]").replace("[TS-002]", "[TS-999]")
    found = " ".join(fs.trace_problems(_dependency_docs(tmp_path, unknown)))
    assert "FR-404 are not FR, BR or MSG ids" in found and "TS-999 are not in test-scenarios.md" in found
    # FR-002 involves another party: an interaction serves it, or it is ruled internal with the reason
    unlinked = X1.replace(", requirements: [FR-002], scenarios: [TS-002]", "")
    assert "FR-002 of functional-spec.md involve another party" in fs.trace_problems(
        _dependency_docs(tmp_path, unlinked))[0]
    internal = "considered_internal:\n  - {requirement: FR-002, reason: our own copy of the status}\n"
    assert fs.trace_problems(_dependency_docs(tmp_path, unlinked, internal)) == []


def test_an_older_cross_domain_file_without_the_links_stays_valid(tmp_path):
    from app import contracts_store

    (tmp_path / "cross-domain.yaml").write_text(
        "interactions:\n  - {id: X1, target: customer, kind: query, summary: KYC}\n")
    assert contracts_store.validate_outputs(tmp_path) == []
    assert fs.trace_problems(tmp_path) == []  # no specification in that run: nothing to link to


def test_analysis_is_sent_back_for_an_unmatched_id_and_finishes_once_fixed(ctx, monkeypatch):
    ctx, _, _ = ctx
    _as(ctx, "analysis", "Analysis")
    out = ".migration/loan/approve"
    bad_as_is = AS_IS.replace("| MSG-01 | error", "| MSG-02 | error")
    client = ScriptedClient([
        _reply(_tool("write_file", path=f"{out}/as-is.md", content=bad_as_is),
               _tool("write_file", path=f"{out}/functional-spec.md", content=SPEC),
               _tool("write_file", path=f"{out}/test-scenarios.md", content=SCENARIOS)),
        _reply(_text("Done."), stop="end_turn"),
        _reply(_tool("write_file", path=f"{out}/as-is.md", content=AS_IS)),
        _reply(_text("Fixed."), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    artifacts = llm.ClaudeAgent("analysis").run(ctx)
    assert {a.name for a in artifacts} == {"as-is.md", "functional-spec.md", "test-scenarios.md"}
    sent_back = str(client.requests[2]["messages"][-1]["content"])
    assert "MSG-02 in as-is.md but not in functional-spec.md" in sent_back


def test_documents_agents_can_edit_in_place_and_no_longer_count_as_code_agents(client):
    from app import agent_store

    for key in ("analysis", "dependency_analysis", "modulith_design"):
        assert "edit_file" in defaults.default_config(key)["tools"] and "run_command" not in \
            defaults.default_config(key)["tools"]
    with session_scope() as s:
        assert not agent_store._changes_code(s.get(Agent, (SHARED, "analysis")))
        assert agent_store._changes_code(s.get(Agent, (SHARED, "modular_monolith")))
        row = s.get(Agent, (SHARED, "deep_dive"))
        before = row.tools
        row.tools = [t for t in before if t != "edit_file"]
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == agent_store.EDIT_FILE_MARKER).delete()
    with session_scope() as s:
        agent_store._give_documents_agents_edit_file(s)
    with session_scope() as s:
        assert "edit_file" in s.get(Agent, (SHARED, "deep_dive")).tools


def test_the_new_agent_reaches_an_existing_database_with_the_analysis_agents_choices(client):
    from app import agent_store
    from tests.test_projects import new_project

    project = new_project(client)
    with session_scope() as s:
        shared = s.get(Agent, (SHARED, "analysis"))
        mode_before = shared.mode
        shared.mode, shared.model = "llm", "claude-opus-5-5"
        s.add(Agent(project=project, key="analysis", **{**agent_store.builtin_default("analysis"),
                                                        "mode": "llm", "effort": "max"}))
        existing = s.get(Agent, (SHARED, "dependency_analysis"))
        kept = {f: getattr(existing, f) for f in agent_store.FIELDS}
        s.delete(existing)
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == "_added_dependency_analysis").delete()
    try:
        with session_scope() as s:
            agent_store._add_later_builtins(s)
        with session_scope() as s:
            added = s.get(Agent, (SHARED, "dependency_analysis"))
            assert (added.mode, added.model) == ("llm", "claude-opus-5-5")  # not the stub, where Analysis runs the AI
            assert added.outputs == ["cross-domain.yaml", "domain-dependencies.yaml"]
            copy = s.get(Agent, (project, "dependency_analysis"))
            assert copy is not None and (copy.mode, copy.effort) == ("llm", "max")
    finally:
        with session_scope() as s:
            s.get(Agent, (SHARED, "analysis")).mode = mode_before
            row = s.get(Agent, (SHARED, "dependency_analysis"))
            for f, v in kept.items():
                setattr(row, f, v)
            for key in ("analysis", "dependency_analysis"):
                own = s.get(Agent, (project, key))
                if own is not None:
                    s.delete(own)


def _old_lean_steps():
    """The lean workflow as stored before Domain dependencies: Analysis wrote the dependencies itself."""
    steps = [st for st in workflow_store.steps_of(LEAN) if st["key"] != "dependency_analysis"]
    for st in steps:
        if st["key"] == "gate_analysis":
            st["reviews"], st["checklist"] = ["analysis"], []
        if st["key"] == "modulith_design":
            st["uses"] = ["analysis"]
    return steps


def test_stored_lean_workflows_get_the_domain_dependencies_step_once(client):
    from tests.test_projects import new_project

    project = new_project(client)
    own_checklist = ["Our own sign-off"]
    with session_scope() as s:
        shared = s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY))
        before = shared.steps
        shared.steps = _old_lean_steps()
        copy_steps = [{**st, "checklist": own_checklist} if st["key"] == "gate_analysis" else st
                      for st in _old_lean_steps()]
        s.add(WorkflowDef(project=project, key=workflow_store.LEAN_KEY, name="Lean migration", steps=copy_steps))
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == "_lean_dependency_step").delete()
    try:
        workflow_store.seed()
        workflow_store.seed()  # once only
        with session_scope() as s:
            for owner in (SHARED, project):
                steps = s.get(WorkflowDef, (owner, workflow_store.LEAN_KEY)).steps
                keys = [st["key"] for st in steps]
                assert keys[:3] == ["analysis", "dependency_analysis", "gate_analysis"], owner
                assert keys.count("dependency_analysis") == 1
                by = {st["key"]: st for st in steps}
                assert by["gate_analysis"]["reviews"] == ["analysis", "dependency_analysis"]
                assert by["modulith_design"]["uses"] == ["analysis", "dependency_analysis"]
                assert by["dependency_analysis"]["uses"] == ["analysis"]
            shared_gate = {st["key"]: st for st in s.get(WorkflowDef, (SHARED, "lean")).steps}["gate_analysis"]
            copy_gate = {st["key"]: st for st in s.get(WorkflowDef, (project, "lean")).steps}["gate_analysis"]
            assert shared_gate["checklist"] == list(LEAN_ANALYSIS_CHECKLIST)
            assert copy_gate["checklist"] == own_checklist  # a checklist of its own is kept
    finally:
        with session_scope() as s:
            s.get(WorkflowDef, (SHARED, workflow_store.LEAN_KEY)).steps = before
            s.delete(s.get(WorkflowDef, (project, workflow_store.LEAN_KEY)))


def test_analysis_in_a_run_without_the_new_step_still_writes_the_dependencies():
    from app.orchestrator import graph
    from app.orchestrator.stages import Workflow

    old = Workflow.from_dicts([{**st, "title": st["key"], "description": ""} for st in _old_lean_steps()])
    config = defaults.default_config("analysis")
    for_run = graph._for_this_run(old, old.by_key["analysis"], config)
    assert for_run["outputs"] == ["as-is.md", "functional-spec.md", "test-scenarios.md", "cross-domain.yaml",
                                  "domain-dependencies.yaml"]
    assert "This run has no Domain dependencies step" in for_run["instructions"]
    assert graph._for_this_run(LEAN, LEAN.by_key["analysis"], config) is config  # the current workflow: as it is


def test_a_later_agent_in_a_run_without_a_specification_takes_the_rules_from_as_is(ctx):
    from app.agents import common

    ctx, _, _ = ctx
    _as(ctx, "modernise_and_test", "Modernisation and service tests")
    assert "This run has no functional specification" in common.no_spec_brief(ctx)
    (ctx.output_dir / "functional-spec.md").write_text(SPEC)
    assert common.no_spec_brief(ctx) == ""


def test_the_skip_note_names_the_step_that_decided_the_scope():
    note = modulith_scope.skip_note("monolith_orchestrator", "Orchestrator in monolith",
                                    "Scope decision is domain-only: no other domain", "Domain dependencies")
    assert "**Decision (Domain dependencies):**" in note and "re-run the run from Domain dependencies" in note


def test_the_agents_after_analysis_name_the_specification_and_the_errors_table():
    for key in ("modulith_design", "independent_tests", "modernise_and_test", "monolith_integration",
                "modular_monolith"):
        assert "functional-spec.md" in defaults.INSTRUCTIONS[key], key
    for key in ("independent_tests", "modernise_and_test", "monolith_integration"):
        assert '"Errors" table' in defaults.INSTRUCTIONS[key], key
    assert "Functional allocation" in defaults.INSTRUCTIONS["modulith_design"]
    for key in fs.COVERAGE_STEPS:
        assert "Functional coverage" in defaults.INSTRUCTIONS[key], key
    analysis = defaults.INSTRUCTIONS["analysis"]
    assert "cross-domain.yaml" not in analysis and "Dependencies: the scope decision" not in analysis
    assert "Domain crossings" in analysis and "Rule enforcement" in analysis
    scenarios = defaults.TEST_SCENARIOS_FORMAT
    assert "never its text again" in scenarios and "Audience: test team" in scenarios
    assert "no class, method, table" in defaults.FUNCTIONAL_SPEC_FORMAT
    for name in ("functional_spec_format", "test_scenarios_format"):
        assert "{{" + name + "}}" in defaults.TEMPLATES["analysis"]


def _lean_run(client, operation=None):
    r = start_run(client, {"domain": "loan", "operation": operation or f"spec{next(_ids)}", "workflow": "lean"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_lean_stub_run_carries_the_specification_to_the_release_gate(client, monkeypatch):
    from app.agents import stub

    seen = {}
    original = stub.StubAgent.run

    def record(self, ctx):
        seen[self.stage] = sorted(d.path.split("/")[-1] for d in ctx.previous_artifacts)
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", record)
    run_id = _lean_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["functional_coverage"]["available"] and run["functional_coverage"]["analysis_problems"] == []
    assert run["functional_coverage"]["without_scenario"] == []
    assert seen["dependency_analysis"] == ["as-is.md", "functional-spec.md", "test-scenarios.md"]
    gates = []
    while run["status"] == "awaiting_approval":
        gates.append(run["pending_gate"]["gate"])
        assert approve(client, run_id).status_code == 200
        run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    assert run["status"] == "completed"
    assert gates == ["gate_analysis", "gate_design", "gate_tests", "gate_orchestrator", "gate_release"]
    assert {"as-is.md", "functional-spec.md", "test-scenarios.md", "cross-domain.yaml",
            "domain-dependencies.yaml"} <= set(seen["modulith_design"])
    coverage = run["functional_coverage"]
    assert coverage["gaps"] == [] and coverage["allocation_problems"] == []
    assert {r["step"] for r in coverage["reported_by"]} >= {"modular_monolith", "monolith_orchestrator",
                                                            "modernise_and_test", "monolith_integration"}
    with session_scope() as s:
        log = [e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id))]
    assert any(m.startswith("Functional coverage:") for m in log)


def test_amending_analysis_at_its_gate_makes_domain_dependencies_follow_when_its_inputs_changed(client, monkeypatch):
    run_id = _lean_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    r = client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "add BR-002",
                                                         "target_stage": "analysis", "scope": "amend"})
    assert r.status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    attempts = lambda key: [s for s in run["stages"] if s["stage"] == key]  # noqa: E731
    assert len(attempts("analysis")) == 2 and len(attempts("dependency_analysis")) == 2  # as-is.md changed: follows
    assert "Analysis was changed at the reviewer's request: add BR-002" in attempts("dependency_analysis")[-1]["feedback"]

    monkeypatch.setattr(workspace, "amendment_changed_inputs", lambda *a: False)  # e.g. only other wording fixed
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "reword the purpose",
                                                     "target_stage": "analysis", "scope": "amend"})
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert len(attempts("analysis")) == 3 and len(attempts("dependency_analysis")) == 2  # kept as it is
    with session_scope() as s:
        log = [e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id))]
    assert any("Domain dependencies kept as it is: the change to Analysis didn't touch" in m for m in log)


def test_a_rejection_aimed_at_domain_dependencies_re_runs_only_it(client):
    run_id = _lean_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "customer is hard",
                                                     "target_stage": "dependency_analysis", "scope": "amend"})
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert [s["stage"] for s in run["stages"] if not s["stage"].startswith("gate_")].count("analysis") == 1
    assert [s["stage"] for s in run["stages"]].count("dependency_analysis") == 2


def test_only_the_amended_parts_a_follower_reads_decide_whether_it_follows():
    before = {"as-is.md": AS_IS, "functional-spec.md": SPEC, "test-scenarios.md": SCENARIOS}
    reworded = {**before, "functional-spec.md": SPEC.replace("Approve loans.", "Approve loan applications.")}
    assert fs.follower_view("dependency_analysis", reworded) == fs.follower_view("dependency_analysis", before)
    side_effect = {**before, "functional-spec.md": SPEC.replace("## 10. Notifications, reports and records\nNone.",
                                                                "## 10. Notifications, reports and records\n"
                                                                "A letter to the customer.")}
    assert fs.follower_view("dependency_analysis", side_effect) != fs.follower_view("dependency_analysis", before)
    assert fs.follower_view("modulith_design", reworded) != fs.follower_view("modulith_design", before)


def test_the_standard_workflow_is_unchanged():
    assert "dependency_analysis" not in STANDARD.by_key
    assert STANDARD.by_key["gate_analysis"].checklist == ()


def test_the_documents_download_records_the_approvals_and_sign_offs(client):
    import io
    import zipfile

    run_id = _lean_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    assert approve(client, run_id, comments="Looks right").status_code == 200
    wait_for(client, run_id, {"awaiting_approval"})
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/documents.zip").content))
    assert any(n.endswith("/functional-spec.md") for n in z.namelist())
    record = z.read("approvals.md").decode()
    assert "| Approve analysis | Approved | Test Admin |" in record and "Looks right" in record
    for item in LEAN_ANALYSIS_CHECKLIST:
        assert f"| Approve analysis | {item} | Test Admin |" in record


def test_earlier_runs_error_paths_section_still_counts_and_confirmations_are_messages():
    old = AS_IS.replace("## Messages", "## Error paths")
    assert fs.analysis_problems(old, SPEC, SCENARIOS) == []
    spec = SPEC.replace("| MSG-01 | Over the limit |", "| MSG-02 | Approved | Loan approved. |\n| MSG-01 | Over the limit |")
    as_is = AS_IS + "| MSG-02 | confirmation | approve succeeds | LoanController | loan.approved |\n"
    assert fs.analysis_problems(as_is, spec, SCENARIOS) == []


def test_a_messages_trigger_mentioning_the_ui_doesnt_make_it_ui_only():
    report = fs.report(_docs(**{"functional-spec.md": SPEC, "as-is.md": AS_IS}), ".migration/loan/approve")
    assert {i["id"]: i["owner"] for i in report["ids"]}["MSG-01"] is None  # its trigger says "UI shows…"
    ui_rule = AS_IS.replace("| BR-001 | server |", "| BR-001 | UI |")
    report = fs.report(_docs(**{"functional-spec.md": SPEC, "as-is.md": ui_rule}), ".migration/loan/approve")
    assert {i["id"]: i["owner"] for i in report["ids"]}["BR-001"] == "ui unchanged"


def test_each_step_is_judged_only_on_the_kinds_of_id_it_covers():
    design = ("## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n| FR-001 | domain | a |\n"
              "| FR-002 | domain | b |\n| BR-001 | domain | a |\n| MSG-01 | domain | a |\n")
    folder = ".migration/loan/approve"
    docs = {**_docs(**{"functional-spec.md": SPEC, "domain-design.md": design}),
            f"{folder}/modular-monolith.md": _coverage("BR-001", "MSG-01")}  # it pins rules and messages only
    steps = [s.to_dict() for s in LEAN.stages]
    report = fs.report(docs, folder, steps)
    assert report["gaps"] == []  # the requirements are for the tests and modernisation steps, not yet run
    assert "not covered by any step yet" not in fs.step_line(docs[f"{folder}/modular-monolith.md"], report,
                                                             "modular_monolith")
    docs[f"{folder}/integration.md"] = _coverage("MSG-01", "FR-001")  # integration maps messages only
    report = fs.report(docs, folder, steps)
    fr = next(i for i in report["ids"] if i["id"] == "FR-001")
    assert fr["gap"] is False and [c["step"] for c in fr["covered_by"]] == ["monolith_integration"]
    docs[f"{folder}/modernisation.md"] = _coverage("FR-001", "BR-001", "MSG-01")
    docs[f"{folder}/test-report.md"] = _coverage("FR-002")  # the domain's acceptance suite proves FR-002 only
    report = fs.report(docs, folder, steps)
    assert report["gaps"] == ["FR-001"]  # a requirement counts only when its owner's acceptance suite proves it


def test_a_domain_only_feature_allocates_nothing_to_the_orchestrator():
    design = ("## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n| FR-001 | domain | a |\n"
              "| FR-002 | orchestrator | b |\n| BR-001 | domain | a |\n| MSG-01 | domain | a |\n")
    assert fs.allocation_problems(SPEC, design, "cross-domain") == []
    assert "gives FR-002 to the orchestrator, but the feature is domain-only" in \
        fs.allocation_problems(SPEC, design, "domain-only")[0]


def test_a_shared_text_a_release_adds_doesnt_make_earlier_steps_look_changed():
    from datetime import datetime, timedelta

    from app import shared_texts

    now = datetime(2026, 11, 2, 10, 0)
    added = {"updated_by": None, "created_at": now, "updated_at": now + timedelta(milliseconds=3)}
    assert not shared_texts.changed_since_added(added)
    assert shared_texts.changed_since_added({**added, "updated_at": now + timedelta(days=1)})  # reset or upgraded
    assert shared_texts.changed_since_added({**added, "updated_by": "Ada"})  # edited


def test_an_analysis_agent_that_writes_no_specification_is_reported(client, monkeypatch):
    from app import agent_store

    warned = []
    monkeypatch.setattr(agent_store.log, "warning", lambda msg, *args: warned.append(msg % args))

    with session_scope() as s:
        row = s.get(Agent, (SHARED, "analysis"))
        before = list(row.outputs)
        row.outputs = ["as-is.md", "test-scenarios.md", "cross-domain.yaml", "domain-dependencies.yaml"]
    try:
        with session_scope() as s:
            agent_store._warn_outdated_analysis(s)
        assert "writes no functional-spec.md" in warned[0] and "(shared)" in warned[0]
        run_id = _lean_run(client)
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert not run["functional_coverage"]["available"]
        assert "the Analysis agent doesn't write one" in run["functional_coverage"]["message"]
    finally:
        with session_scope() as s:
            s.get(Agent, (SHARED, "analysis")).outputs = before


def test_the_domain_download_has_each_features_approvals(client):
    import io
    import zipfile

    run_id = _lean_run(client, f"zipped{next(_ids)}")
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    wait_for(client, run_id, {"awaiting_approval"})
    operation = client.get(f"/api/runs/{run_id}").json()["operation"]
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/domains/loan/documents.zip").content))
    assert f"{operation}/approvals.md" in z.namelist()
