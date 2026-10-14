"""The Modulith design flow: a per-feature cross-domain dependency finder, then domain, wrapper and orchestrator
design agents, in the Standard migration workflow."""

import itertools
from pathlib import Path

import pytest
from sqlalchemy import select

from app import agent_store, contracts_store, modulith_scope, workflow_store
from app.agents import stub
from app.agents.defaults import INSTRUCTIONS, default_config
from app.db.models import Agent, AuditEvent, Setting, WorkflowDef
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_cross_domain import _approve, _steps_of_shape

_ids = itertools.count()
MODULITH = ["domain_dependencies", "domain_design", "wrapper_design", "orchestrator_design"]


def _steps(client):
    return {st["key"]: st for st in client.get("/api/workflows/standard").json()["steps"]}


def test_standard_workflow_replaces_modulith_design_with_the_sub_flow(client):
    keys = [st["key"] for st in client.get("/api/workflows/standard").json()["steps"]]
    assert keys[:9] == ["as_is_doc", "deep_dive", "gate_analysis", *MODULITH, "integration_design", "gate_design"]
    assert "boundary_design" not in keys
    steps = _steps(client)
    assert steps["gate_design"]["reviews"] == [*MODULITH, "integration_design"]


def test_each_modulith_step_reads_only_what_it_builds_on(client):
    steps = _steps(client)
    assert steps["domain_dependencies"]["uses"] == ["as_is_doc", "deep_dive"]
    assert steps["domain_design"]["uses"] == ["as_is_doc", "deep_dive", "domain_dependencies"]
    assert steps["wrapper_design"]["uses"] == ["deep_dive", "domain_dependencies", "domain_design"]
    assert steps["orchestrator_design"]["uses"] == ["domain_dependencies", "domain_design", "wrapper_design"]


def test_modulith_agents_are_read_only_design_agents(client):
    for key in MODULITH:
        agent = client.get(f"/api/agents/{key}").json()
        assert agent["builtin"] and agent["tools"] == default_config(key)["tools"]
        assert "run_command" not in agent["tools"] and "edit_file" not in agent["tools"]
        assert agent["checks"] == []
    outputs = {k: client.get(f"/api/agents/{k}").json()["outputs"] for k in MODULITH}
    assert outputs["domain_dependencies"] == ["domain-dependencies.md", "domain-dependencies.yaml"]
    assert outputs["wrapper_design"] == ["wrapper-design.md", "wrapper-api.yaml"]
    assert outputs["orchestrator_design"] == ["orchestrator-design.md"]


def test_instructions_carry_each_agents_focus():
    deps, domain = INSTRUCTIONS["domain_dependencies"], INSTRUCTIONS["domain_design"]
    wrapper, orchestrator = INSTRUCTIONS["wrapper_design"], INSTRUCTIONS["orchestrator_design"]
    assert "domain-dependencies.yaml" in deps and "considered_unrelated" in deps and "touchpoints" in deps
    assert "same database and the same UI" in domain and "No DDL" in domain
    assert "/internal/api/v1/" in wrapper and "domain-dependencies.yaml" in wrapper
    assert "`local` mode" in orchestrator and "`wrapper` mode" in orchestrator
    assert all(k in INSTRUCTIONS["modular_monolith"] for k in ("orchestrator", "wrapper"))
    assert "boundary design" not in INSTRUCTIONS["integration_design"]


# --- domain-dependencies.yaml --------------------------------------------------------------------------------------

GOOD = """feature: create-loan
domain: loan
decision: {scope: cross-domain, reason: Calls customer for the KYC status}
dependencies:
  - domain: customer
    relation: calls
    direction: outbound
    strength: hard
    touchpoints:
      - {kind: service, ref: "com.acme.customer.CustomerService#getKycStatus", owner: customer}
"""


def test_domain_dependencies_are_validated(client):
    parsed = contracts_store.parse_domain_dependencies(GOOD)
    assert parsed[0]["domain"] == "customer"
    self_contained = "decision: {scope: domain-only, reason: Own tables only}\ndependencies: []\n"
    assert contracts_store.parse_domain_dependencies(self_contained) == []  # a feature can be self-contained

    def bad(text, match):
        with pytest.raises(contracts_store.InvalidDocument, match=match):
            contracts_store.parse_domain_dependencies(text)

    bad(GOOD.replace("calls", "telepathy"), "must be one of")
    bad(GOOD.replace("hard", "very"), "must be one of")
    bad(GOOD.replace("kind: service", "kind: magic"), "must be one of")
    bad(GOOD.replace("    touchpoints:\n      - {kind: service, ref: \"com.acme.customer.CustomerService#getKycStatus\", owner: customer}\n",
                     "    touchpoints: []\n"), "list the touchpoints")
    bad(GOOD.replace(', ref: "com.acme.customer.CustomerService#getKycStatus"', ""), "missing ref")
    bad(GOOD + GOOD.split("dependencies:\n")[1], "listed twice")
    bad("dependencies: nope\n", "must be a list")


def test_the_scope_decision_is_required_and_consistent_with_the_dependencies(client):
    assert contracts_store.load_domain_dependencies(GOOD)["decision"]["scope"] == "cross-domain"
    domain_only = "decision: {scope: domain-only, reason: Uses only loan tables}\ndependencies: []\n"
    assert contracts_store.load_domain_dependencies(domain_only)["dependencies"] == []

    def bad(text, match):
        with pytest.raises(contracts_store.InvalidDocument, match=match):
            contracts_store.load_domain_dependencies(text)

    bad("dependencies: []\n", "add a decision")
    bad(domain_only.replace("domain-only", "everything"), "must be one of")
    bad(domain_only.replace(", reason: Uses only loan tables", ""), "missing reason")
    bad(GOOD.replace("cross-domain", "domain-only"), "outbound dependencies on customer")
    bad(domain_only.replace("domain-only", "cross-domain"), "no outbound dependency is listed")
    # Callers from other domains do not make a feature cross-domain.
    inbound_only = domain_only.replace("dependencies: []", """dependencies:
  - domain: reports
    relation: called-by
    direction: inbound
    touchpoints: [{kind: service, ref: "com.acme.reports.Export#loans"}]""")
    assert contracts_store.load_domain_dependencies(inbound_only)["decision"]["scope"] == "domain-only"


def test_an_invalid_dependencies_file_blocks_the_agent_from_finishing(tmp_path):
    (tmp_path / "domain-dependencies.yaml").write_text(GOOD.replace("calls", "telepathy"))
    assert any("must be one of" in p for p in contracts_store.validate_outputs(tmp_path))
    (tmp_path / "domain-dependencies.yaml").write_text(GOOD)
    assert contracts_store.validate_outputs(tmp_path) == []


# --- a full run ----------------------------------------------------------------------------------------------------

def test_a_run_goes_through_the_sub_flow_and_leaves_the_documents_for_later_steps(client):
    op = f"mod{next(_ids)}"
    run_id = start_run(client, {"domain": "customer", "operation": op}).json()["id"]
    assert wait_for(client, run_id, {"awaiting_approval"})["pending_gate"]["gate"] == "gate_analysis"
    assert _approve(client, run_id).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"

    order = [s["stage"] for s in run["stages"] if s["stage"] in (*MODULITH, "integration_design")]
    assert order == [*MODULITH, "integration_design"]

    artifacts = client.get(f"/api/runs/{run_id}/artifacts").json()
    by_stage = {a["stage"]: a for a in artifacts if a["name"].endswith(".md")}
    assert set(MODULITH) <= set(by_stage)
    deps = next(a for a in artifacts if a["name"] == "domain-dependencies.yaml")
    assert deps["stage"] == "domain_dependencies" and deps["kind"] == "yaml"
    content = client.get(f"/api/artifacts/{deps['id']}").json()["content"]
    assert contracts_store.parse_domain_dependencies(content)  # the sample is valid

    # The orchestrator step is told about exactly the documents it builds on.
    orchestrator = client.get(f"/api/artifacts/{by_stage['orchestrator_design']['id']}").json()["content"]
    for stage in ("domain_dependencies", "domain_design", "wrapper_design"):
        assert f"{stage}.md`" in orchestrator
    assert "deep_dive.md`" not in orchestrator and "as_is_doc.md`" not in orchestrator
    ws = Path(run["workspace"])
    assert (ws / by_stage["domain_dependencies"]["path"]).is_file()


# --- upgrades ------------------------------------------------------------------------------------------------------

def test_standard_workflow_upgrade_from_the_previous_release(client):
    previous = _steps_of_shape(workflow_store._STANDARD_V2)
    original = client.get("/api/workflows/standard").json()["steps"]

    def reset(steps):
        with session_scope() as s:
            s.get(WorkflowDef, "standard").steps = steps
            s.query(Setting).filter(Setting.key == "_standard_workflow_v3").delete()

    reset(previous)
    workflow_store.seed()
    assert [st["key"] for st in _steps(client).values()][3:7] == MODULITH

    customised = [st for st in previous if st["key"] != "modernise"]
    reset(customised)
    workflow_store.seed()
    assert "domain_design" not in _steps(client)  # a customised workflow is left alone
    reset(original)
    workflow_store.seed()


def test_modulith_agents_are_added_to_an_existing_library_once(client):
    marker = "_added_modulith_design_agents"
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == marker).delete()
        for key in MODULITH:
            s.delete(s.get(Agent, key))
    agent_store.seed()
    assert all(agent_store.exists(k) for k in MODULITH)
    with session_scope() as s:
        s.delete(s.get(Agent, "wrapper_design"))
    agent_store.seed()  # marker present: a deliberately deleted built-in stays deleted
    assert not agent_store.exists("wrapper_design")
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == marker).delete()
    agent_store.seed()
    assert agent_store.exists("wrapper_design")


def test_untouched_integration_and_module_agents_pick_up_the_new_instructions(client):
    import hashlib

    previous = {"integration_design": "22b0c4621eb6a97503c878794caed382350840e41c8ee9dd174b6338d7674bf7",
                "modular_monolith": "a2faa355ff82f2ded46ea8c43c9f551c16ff2e09a9f5ae0af29931bfac559025"}
    for key, digest in previous.items():
        assert digest in agent_store.PREVIOUS_DEFAULT_HASHES[key]
        assert hashlib.sha256(INSTRUCTIONS[key].encode()).hexdigest() != digest  # the defaults did change


# --- skipping wrapper and orchestrator for domain-only features -----------------------------------------------------

DOMAIN_ONLY = """feature: {op}
domain: {domain}
decision: {{scope: domain-only, reason: "Only uses {domain} tables and classes"}}
dependencies: []
"""


def _run_to_design_gate(client, domain, domain_only, monkeypatch):
    """Run a feature to the design gate with the stub agents; returns (run, stages whose agent actually ran)."""
    original, ran = stub.StubAgent.run, []

    def run(self, ctx):
        ran.append(self.stage)
        out = original(self, ctx)
        if domain_only and self.stage == "domain_dependencies":
            (ctx.output_dir / "domain-dependencies.yaml").write_text(
                DOMAIN_ONLY.format(op=ctx.operation, domain=ctx.domain))
        return out

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run_id = start_run(client, {"domain": domain, "operation": f"scope{next(_ids)}"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    assert _approve(client, run_id).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    return run, ran


def test_domain_only_feature_skips_wrapper_and_orchestrator(client, monkeypatch):
    run, ran = _run_to_design_gate(client, "customer", True, monkeypatch)
    assert "wrapper_design" not in ran and "orchestrator_design" not in ran  # no agent, so no AI cost
    assert {"domain_dependencies", "domain_design", "integration_design"} <= set(ran)

    stages = {s["stage"]: s for s in run["stages"]}
    assert stages["wrapper_design"]["status"] == stages["orchestrator_design"]["status"] == "completed"
    docs = {a["stage"]: a for a in client.get(f"/api/runs/{run['id']}/artifacts").json() if a["name"].endswith(".md")}
    for stage, nothing in (("wrapper_design", "nothing to wrap"), ("orchestrator_design", "nothing to orchestrate")):
        note = client.get(f"/api/artifacts/{docs[stage]['id']}").json()["content"]
        assert "skipped" in note and nothing in note and "domain-only" in note
    with session_scope() as s:
        log = " ".join(e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run["id"])))
    assert "Skipped Modulith design: Wrapper" in log


def test_cross_domain_feature_runs_every_modulith_step(client, monkeypatch):
    run, ran = _run_to_design_gate(client, "customer", False, monkeypatch)
    assert [s for s in ran if s in MODULITH] == MODULITH


def test_skip_follows_the_decision_in_the_workspace(tmp_path):
    f = tmp_path / "domain-dependencies.yaml"
    assert modulith_scope.skip_reason("wrapper_design", tmp_path) is None  # no finder in this workflow: run
    f.write_text(DOMAIN_ONLY.format(op="x", domain="loan"))
    assert "domain-only" in modulith_scope.skip_reason("wrapper_design", tmp_path)
    assert "domain-only" in modulith_scope.skip_reason("orchestrator_design", tmp_path)
    assert modulith_scope.skip_reason("domain_design", tmp_path) is None  # only these two are ever skipped
    assert modulith_scope.skip_reason("integration_design", tmp_path) is None
    f.write_text(GOOD)  # a reviewer's edit to cross-domain brings the steps back
    assert modulith_scope.skip_reason("wrapper_design", tmp_path) is None
    f.write_text("decision: [")  # unreadable: run the step rather than silently skip it
    assert modulith_scope.skip_reason("wrapper_design", tmp_path) is None


def test_untouched_modulith_agents_from_the_previous_iteration_are_upgraded(client):
    for key in MODULITH:
        assert agent_store.PREVIOUS_DEFAULT_HASHES[key]
    assert "decision" in INSTRUCTIONS["domain_dependencies"] and "domain-only" in INSTRUCTIONS["wrapper_design"]
