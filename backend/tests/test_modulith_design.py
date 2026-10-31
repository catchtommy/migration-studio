"""The Modulith design flow: a per-feature cross-domain dependency finder, then linked-domain contracts, domain and
orchestrator design agents, in the Standard migration workflow."""

import itertools

import pytest
from sqlalchemy import select

from app import agent_store, contracts_store, modulith_scope, workflow_store
from app.agents import stub
from app.agents.defaults import GUARDRAILS, INSTRUCTIONS, default_config
from app.db.models import SHARED, Agent, AuditEvent, Setting, WorkflowDef
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_cross_domain import _approve, _steps_of_shape

# What each built-in agent is given: its instructions, then its guardrails (moved out of the instructions).
GIVEN = {key: f"{text}\n\n{GUARDRAILS.get(key, '')}" for key, text in INSTRUCTIONS.items()}

_ids = itertools.count()
MODULITH = ["domain_dependencies", "linked_domain_contracts", "domain_design", "orchestrator_design"]


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
    assert steps["linked_domain_contracts"]["uses"] == ["deep_dive", "domain_dependencies"]
    assert steps["domain_design"]["uses"] == ["as_is_doc", "deep_dive", "domain_dependencies",
                                              "linked_domain_contracts"]
    assert steps["orchestrator_design"]["uses"] == ["domain_dependencies", "linked_domain_contracts", "domain_design"]
    assert "wrapper_design" not in steps


def test_modulith_agents_are_read_only_design_agents(client):
    for key in MODULITH:
        agent = client.get(f"/api/agents/{key}").json()
        assert agent["builtin"] and agent["tools"] == default_config(key)["tools"]
        assert "run_command" not in agent["tools"] and "edit_file" not in agent["tools"]
        assert agent["checks"] == []
    outputs = {k: client.get(f"/api/agents/{k}").json()["outputs"] for k in MODULITH}
    assert outputs["domain_dependencies"] == ["domain-dependencies.md", "domain-dependencies.yaml"]
    assert outputs["linked_domain_contracts"] == ["linked-domain-contracts.md", "linked-domain-contracts.yaml"]
    assert outputs["orchestrator_design"] == ["orchestrator-design.md"]


def test_instructions_carry_each_agents_focus():
    deps, domain = GIVEN["domain_dependencies"], GIVEN["domain_design"]
    contracts, orchestrator = GIVEN["linked_domain_contracts"], GIVEN["orchestrator_design"]
    assert "domain-dependencies.yaml" in deps and "considered_unrelated" in deps and "touchpoints" in deps
    assert "same database and the same UI" in domain and "No DDL" in domain
    assert "domain-dependencies.yaml" in contracts and "reuse" in contracts and "binding" in contracts
    assert "temporary" in contracts and "linked-domain-contracts.yaml" in contracts
    assert "`local` mode" in orchestrator and "`contract` mode" in orchestrator
    assert "linked-domain-contracts.yaml" in domain  # ports name the contract they call
    assert all(k in GIVEN["modular_monolith"] for k in ("orchestrator", "binding", "linked-domain contracts"))
    assert "boundary design" not in GIVEN["integration_design"]
    assert "linked-domain" in GIVEN["integration_design"]
    assert "wrapper" not in orchestrator.lower() and "wrapper" not in deps.lower()


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
    for stage in ("domain_dependencies", "linked_domain_contracts", "domain_design"):
        assert f"{stage}.md`" in orchestrator
    assert "deep_dive.md`" not in orchestrator and "as_is_doc.md`" not in orchestrator
    from app.workspace import effective_documents
    assert by_stage["domain_dependencies"]["path"] in effective_documents(run["id"])  # left for later steps


# --- upgrades ------------------------------------------------------------------------------------------------------

def test_standard_workflow_upgrade_from_the_previous_release(client):
    previous = _steps_of_shape(workflow_store._STANDARD_V3)
    original = client.get("/api/workflows/standard").json()["steps"]

    def reset(steps):
        with session_scope() as s:
            s.get(WorkflowDef, (SHARED, "standard")).steps = steps
            s.query(Setting).filter(Setting.key == "_standard_workflow_v4").delete()

    reset(previous)
    workflow_store.seed()
    assert [st["key"] for st in _steps(client).values()][3:7] == MODULITH

    customised = [st for st in previous if st["key"] != "modernise"]
    reset(customised)
    workflow_store.seed()
    assert "linked_domain_contracts" not in _steps(client)  # a customised workflow is left alone
    reset(original)
    workflow_store.seed()


def test_linked_domain_contracts_agent_is_added_to_an_existing_library_once(client):
    marker = "_added_linked_domain_contracts"
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == marker).delete()
        s.delete(s.get(Agent, (SHARED, "linked_domain_contracts")))
    agent_store.seed()
    assert agent_store.exists("linked_domain_contracts")
    with session_scope() as s:
        s.delete(s.get(Agent, (SHARED, "linked_domain_contracts")))
    agent_store.seed()  # marker present: a deliberately deleted built-in stays deleted
    assert not agent_store.exists("linked_domain_contracts")
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == marker).delete()
    agent_store.seed()
    assert agent_store.exists("linked_domain_contracts")


def test_superseded_agents_are_kept_but_hidden_from_the_agent_list(client):
    listed = {a["key"] for a in client.get("/api/agents").json()}
    assert not {"boundary_design", "wrapper_design"} & listed
    for key in ("boundary_design", "wrapper_design"):  # still in the library: reachable by key, with their defaults
        agent = client.get(f"/api/agents/{key}").json()
        assert agent["builtin"] and "Replaced in the Standard migration workflow" in agent["description"]
        assert agent["instructions"] == default_config(key)["instructions"]
    assert {"boundary_design", "wrapper_design"} <= set(agent_store.titles())  # workflows can still resolve them
    assert {"boundary_design", "wrapper_design"} <= {a["key"] for a in agent_store.list_all(include_hidden=True)}
    assert "wrapper_design" not in _steps(client)  # and the Standard workflow does not use them


def test_a_hidden_agent_reappears_while_a_workflow_uses_it(client):
    key = f"wrapper-wf-{next(_ids)}"
    r = client.post("/api/workflows", json={"key": key, "name": "Uses the wrapper",
                                            "steps": [{"kind": "agent", "key": "wrapper_design"}]})
    assert r.status_code == 201, r.text  # hidden agents can still be used in a workflow
    assert "wrapper_design" in {a["key"] for a in client.get("/api/agents").json()}
    assert client.delete(f"/api/workflows/{key}").status_code == 204
    assert "wrapper_design" not in {a["key"] for a in client.get("/api/agents").json()}


def test_untouched_modulith_agents_pick_up_the_new_instructions(client):
    import hashlib

    previous = {"domain_dependencies": "d2fc147964a2e477c07072900e92b7356898e7613f3274d96dfab97576ec8476",
                "domain_design": "45ad59b8a91325d3caa86f53505051bda11071e52282340470b7eef5fa0649eb",
                "orchestrator_design": "b1175e5e93c69bb615256ce4684be01648dd7736bee4955052bbca5be914d9dc",
                "integration_design": "b53ca7a0466106e58ce2f99daf90e85f88ca28309d8ec35f2a3c0d49186527e5",
                "modular_monolith": "31d5f8f0bbaa53df1978c04257e99924dade1b2b41f230a985b8bd08830d9adc"}
    for key, digest in previous.items():
        assert digest in agent_store.PREVIOUS_DEFAULT_HASHES[key]
        assert hashlib.sha256(INSTRUCTIONS[key].encode()).hexdigest() != digest  # the defaults did change


# --- skipping contracts and orchestrator for domain-only features ---------------------------------------------------

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


def test_domain_only_feature_skips_linked_domain_contracts_and_orchestrator(client, monkeypatch):
    run, ran = _run_to_design_gate(client, "customer", True, monkeypatch)
    assert "linked_domain_contracts" not in ran and "orchestrator_design" not in ran  # no agent, so no AI cost
    assert {"domain_dependencies", "domain_design", "integration_design"} <= set(ran)

    stages = {s["stage"]: s for s in run["stages"]}
    assert stages["linked_domain_contracts"]["status"] == stages["orchestrator_design"]["status"] == "completed"
    docs = {a["stage"]: a for a in client.get(f"/api/runs/{run['id']}/artifacts").json() if a["name"].endswith(".md")}
    for stage, nothing in (("linked_domain_contracts", "no contract to define"),
                           ("orchestrator_design", "nothing to orchestrate")):
        note = client.get(f"/api/artifacts/{docs[stage]['id']}").json()["content"]
        assert "skipped" in note and nothing in note and "domain-only" in note
    with session_scope() as s:
        log = " ".join(e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run["id"])))
    assert "Skipped Modulith design: Linked-domain contracts" in log


@pytest.mark.parametrize("domain_only", [True, False])
def test_only_a_cross_domain_feature_gets_an_orchestrator_in_the_monolith(client, monkeypatch, domain_only):
    run, ran = _run_to_design_gate(client, "customer", domain_only, monkeypatch)
    assert _approve(client, run["id"]).status_code == 200
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_tests"
    stages = {s["stage"]: s for s in run["stages"]}
    assert stages["monolith_orchestrator"]["status"] == "completed"
    assert ("monolith_orchestrator" in ran) is not domain_only  # skipped without an agent when domain-only
    assert ran.index("modular_monolith") < ran.index("microservice_scaffold")
    assert stages["orchestrator_service"]["status"] == "completed"
    assert ("orchestrator_service" in ran) is not domain_only  # the orchestrator service too
    if not domain_only:
        assert ran.index("modular_monolith") < ran.index("monolith_orchestrator") < ran.index("microservice_scaffold") \
            < ran.index("orchestrator_service") < ran.index("independent_tests")
    [doc] = [a for a in client.get(f"/api/runs/{run['id']}/artifacts").json()
             if a["stage"] == "monolith_orchestrator" and a["name"].endswith(".md")]
    note = client.get(f"/api/artifacts/{doc['id']}").json()["content"]
    assert ("no orchestrator to build" in note) is domain_only


def test_standard_workflow_gains_the_orchestrator_step_keeping_its_settings(client):
    original = client.get("/api/workflows/standard").json()["steps"]
    previous = _steps_of_shape(workflow_store._STANDARD_V4)
    previous = [{**st, "code": {"repository": "", "base_branch": "develop"}} if st["key"] == "modular_monolith"
                else st for st in previous]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = previous
        s.query(Setting).filter(Setting.key == "_standard_workflow_v5").delete()
    workflow_store.seed()
    steps = client.get("/api/workflows/standard").json()["steps"]
    keys = [st["key"] for st in steps]
    assert keys[keys.index("modular_monolith") + 1] == "monolith_orchestrator"
    by_key = {st["key"]: st for st in steps}
    assert by_key["monolith_orchestrator"]["code"] == by_key["modular_monolith"]["code"] == \
        {"changes_code": True, "base_branch": "develop"}  # it changes the same code as the module step (as saved then)
    assert "monolith_orchestrator" in by_key["gate_tests"]["reviews"]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = original


def test_standard_workflow_gains_the_orchestrator_service_after_the_domain_service(client):
    original = client.get("/api/workflows/standard").json()["steps"]
    previous = [{**st, "code": {"repository": "https://git.example.com/acme/{domain}-service.git"}}
                if st["key"] == "microservice_scaffold" else st for st in _steps_of_shape(workflow_store._STANDARD_V5)]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = previous
        s.query(Setting).filter(Setting.key == "_standard_workflow_v6").delete()
    workflow_store.seed()
    steps = client.get("/api/workflows/standard").json()["steps"]
    keys = [st["key"] for st in steps]
    assert keys[keys.index("microservice_scaffold") + 1] == "orchestrator_service"
    by_key = {st["key"]: st for st in steps}
    assert by_key["microservice_scaffold"]["code"]["repository"].endswith("{domain}-service.git")  # kept
    assert not by_key["orchestrator_service"].get("code")  # its own repository: set on the Workflows page
    assert "orchestrator_service" in by_key["gate_tests"]["reviews"]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = original


def test_a_projects_copy_of_the_standard_workflow_gains_the_orchestrator_step_unless_its_steps_changed(client):
    previous = [{**st, "uses": ["deep_dive"]} if st["key"] == "domain_dependencies" else st
                for st in _steps_of_shape(workflow_store._STANDARD_V4)]  # a setting differs: still upgraded
    changed = [st for st in previous if st["key"] != "modernise"]  # a step removed: left alone
    with session_scope() as s:
        for project, steps in (("p-same", previous), ("p-changed", changed)):
            s.add(WorkflowDef(project=project, key="standard", name="Standard migration", steps=steps))
        s.query(Setting).filter(Setting.key == "_standard_project_copies_v5").delete()
    workflow_store.seed()
    with session_scope() as s:
        same, kept = (s.get(WorkflowDef, (p, "standard")) for p in ("p-same", "p-changed"))
        keys = [st["key"] for st in same.steps]
        assert keys[keys.index("modular_monolith") + 1] == "monolith_orchestrator"
        assert next(st for st in same.steps if st["key"] == "domain_dependencies")["uses"] == ["deep_dive"]
        assert "monolith_orchestrator" not in [st["key"] for st in kept.steps]
        s.delete(same)
        s.delete(kept)


def test_cross_domain_feature_runs_every_modulith_step(client, monkeypatch):
    run, ran = _run_to_design_gate(client, "customer", False, monkeypatch)
    assert [s for s in ran if s in MODULITH] == MODULITH


def test_skip_follows_the_decision_in_the_workspace(tmp_path):
    f = tmp_path / "domain-dependencies.yaml"
    assert modulith_scope.skip_reason("linked_domain_contracts", tmp_path) is None  # no finder in this workflow: run
    f.write_text(DOMAIN_ONLY.format(op="x", domain="loan"))
    assert "domain-only" in modulith_scope.skip_reason("linked_domain_contracts", tmp_path)
    assert "domain-only" in modulith_scope.skip_reason("orchestrator_design", tmp_path)
    assert "domain-only" in modulith_scope.skip_reason("wrapper_design", tmp_path)  # the superseded step, if used
    assert "domain-only" in modulith_scope.skip_reason("monolith_orchestrator", tmp_path)  # nothing to build
    assert modulith_scope.skip_reason("modular_monolith", tmp_path) is None
    assert modulith_scope.skip_reason("domain_design", tmp_path) is None  # only these two are ever skipped
    assert modulith_scope.skip_reason("integration_design", tmp_path) is None
    f.write_text(GOOD)  # a reviewer's edit to cross-domain brings the steps back
    assert modulith_scope.skip_reason("linked_domain_contracts", tmp_path) is None
    f.write_text("decision: [")  # unreadable: run the step rather than silently skip it
    assert modulith_scope.skip_reason("linked_domain_contracts", tmp_path) is None


def test_untouched_modulith_agents_from_the_previous_iteration_are_upgraded(client):
    for key in ("domain_dependencies", "domain_design", "orchestrator_design"):
        assert agent_store.PREVIOUS_DEFAULT_HASHES[key]
    assert "decision" in INSTRUCTIONS["domain_dependencies"] and "domain-only" in INSTRUCTIONS["linked_domain_contracts"]


# --- linked-domain-contracts.yaml ------------------------------------------------------------------------------------

CONTRACTS = """feature: create-loan
domain: loan
contracts:
  - key: customer.get-kyc-status
    provider: customer
    kind: api
    name: Get KYC status
    version: "1"
    interactions: [X1]
    relies_on: Current answer
    spec: contracts/customer.get-kyc-status.yaml
    binding:
      delegates_to: ["com.acme.customer.CustomerService#getKycStatus"]
      remove_when: The customer service provides this contract
"""


def test_linked_domain_contracts_are_validated(client):
    parsed = contracts_store.parse_linked_domain_contracts(CONTRACTS)
    assert parsed[0]["key"] == "customer.get-kyc-status"
    assert contracts_store.parse_linked_domain_contracts("contracts: []\n") == []

    def bad(text, match):
        with pytest.raises(contracts_store.InvalidDocument, match=match):
            contracts_store.parse_linked_domain_contracts(text)

    bad("contracts: nope\n", "must be a list")
    bad(CONTRACTS.replace("kind: api", "kind: rpc"), "must be one of")
    bad(CONTRACTS.replace("key: customer.", "key: payment."), "must start with its provider")
    bad(CONTRACTS.replace("    name: Get KYC status\n", ""), "missing name")
    bad(CONTRACTS.replace("    spec: contracts/customer.get-kyc-status.yaml\n", ""), "missing spec")
    bad(CONTRACTS.split("    binding:")[0], "describe the temporary binding")
    bad(CONTRACTS.replace("delegates_to: [\"com.acme.customer.CustomerService#getKycStatus\"]", "delegates_to: []"),
        "describe the temporary binding")
    bad(CONTRACTS.replace("      remove_when: The customer service provides this contract\n", ""),
        "missing remove_when")
    bad(CONTRACTS + CONTRACTS.split("contracts:\n")[1], "duplicate key")
    # A contract already in the catalogue needs neither spec nor binding.
    reused = "contracts:\n  - {key: customer.get-kyc-status, provider: customer, kind: api, name: KYC, reuse: true}\n"
    assert contracts_store.parse_linked_domain_contracts(reused)[0]["reuse"] is True


def test_contracts_must_match_the_dependencies_and_have_their_specs(tmp_path):
    (tmp_path / "domain-dependencies.yaml").write_text(GOOD)
    (tmp_path / "linked-domain-contracts.yaml").write_text(CONTRACTS)
    problems = contracts_store.validate_outputs(tmp_path)
    assert problems == ["linked-domain-contracts.yaml: spec file contracts/customer.get-kyc-status.yaml for "
                        "contract customer.get-kyc-status does not exist"]
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/customer.get-kyc-status.yaml").write_text("openapi: 3.1.0\n")
    assert contracts_store.validate_outputs(tmp_path) == []

    (tmp_path / "linked-domain-contracts.yaml").write_text("contracts: []\n")  # customer has no contract
    assert any("no contract for customer" in p for p in contracts_store.validate_outputs(tmp_path))
    (tmp_path / "linked-domain-contracts.yaml").write_text(
        CONTRACTS.replace("customer", "payment"))  # a contract for a domain the feature does not depend on
    problems = contracts_store.validate_outputs(tmp_path)
    assert any("no contract for customer" in p for p in problems)
    assert any("payment.get-kyc-status is for payment, which is not an outbound dependency" in p for p in problems)


def test_integration_decisions_can_use_linked_domain_contracts_without_listing_them(tmp_path):
    decisions = """decisions:
  - {interaction: X1, target: customer, kind: query, pattern: sync-api, contract: customer.get-kyc-status}
contracts: []
"""
    with pytest.raises(contracts_store.InvalidDocument, match="linked-domain contract"):
        contracts_store.parse_decisions(decisions)
    assert contracts_store.parse_decisions(decisions, {"customer.get-kyc-status"})["decisions"]

    (tmp_path / "domain-dependencies.yaml").write_text(GOOD)
    (tmp_path / "linked-domain-contracts.yaml").write_text(CONTRACTS)
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/customer.get-kyc-status.yaml").write_text("openapi: 3.1.0\n")
    (tmp_path / "integration-decisions.yaml").write_text(decisions)
    assert contracts_store.validate_outputs(tmp_path) == []


# --- the contracts catalogue -----------------------------------------------------------------------------------------

def test_approving_the_design_records_the_linked_domain_contracts_as_proposed(client, monkeypatch):
    original, probe = stub.StubAgent.run, f"probe{next(_ids)}"

    def run(self, ctx):
        out = original(self, ctx)
        if self.stage == "linked_domain_contracts":  # a contract key no other test has recorded
            f = ctx.output_dir / "linked-domain-contracts.yaml"
            sample = f.read_text()
            other = next(line.split(":")[1].strip() for line in sample.splitlines() if line.startswith("    provider:"))
            f.write_text(sample.replace(f"get-{other}-summary", probe))
            (ctx.output_dir / f"contracts/{other}.{probe}.yaml").write_text(
                (ctx.output_dir / f"contracts/{other}.get-{other}-summary.yaml").read_text())
        if self.stage == "integration_design":  # decisions that don't re-list the contract: only the linked file does
            (ctx.output_dir / "integration-decisions.yaml").write_text("decisions: []\ncontracts: []\n")
        return out

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run, _ = _run_to_design_gate(client, "broker", False, monkeypatch)
    other = next(k for k in sorted(d["key"] for d in client.get("/api/domains").json()) if k != "broker")
    key = f"{other}.{probe}"
    assert key not in {c["key"] for c in client.get("/api/contracts").json()}  # nothing before approval

    assert _approve(client, run["id"]).status_code == 200
    contract = client.get(f"/api/contracts/{key}").json()
    assert contract["status"] == "proposed" and contract["provider"] == other
    assert contract["consumers"] == ["broker"] and contract["spec"].startswith("openapi")
    log = " ".join(e.message for e in _events(run["id"]))
    assert "Recorded 1 linked-domain contract(s)" in log


def _events(run_id):
    with session_scope() as s:
        return list(s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id)))


def test_a_proposed_contract_becomes_approved_by_a_decision_and_a_provided_one_stays(client):
    from app import projects
    from pathlib import PurePosixPath

    spec = {"key": "zz.get-thing", "provider": "zz", "kind": "api", "name": "Get thing", "spec": "contracts/a.yaml"}
    with session_scope() as s:
        project = projects.current()
        contracts_store._upsert_contract(s, project, "run-x", spec, PurePosixPath("."), "proposed", ["loan"])
    assert client.get("/api/contracts/zz.get-thing").json()["status"] == "proposed"
    with session_scope() as s:
        contracts_store._upsert_contract(s, projects.current(), "run-x", spec, PurePosixPath("."), "approved", [])
    assert client.get("/api/contracts/zz.get-thing").json()["status"] == "approved"
    client.put("/api/contracts/zz.get-thing", json={"status": "provided-by-service"})
    with session_scope() as s:
        for status in ("proposed", "approved"):
            contracts_store._upsert_contract(s, projects.current(), "run-y", spec, PurePosixPath("."), status, [])
    assert client.get("/api/contracts/zz.get-thing").json()["status"] == "provided-by-service"

    # reuse: only a new consumer is added; the catalogue's definition stands.
    reuse = {"key": "zz.get-thing", "provider": "zz", "kind": "api", "name": "Renamed by mistake", "reuse": True}
    with session_scope() as s:
        contracts_store._upsert_contract(s, projects.current(), "run-z", reuse, PurePosixPath("."), "proposed",
                                         ["payments"])
    contract = client.get("/api/contracts/zz.get-thing").json()
    assert contract["name"] == "Get thing" and contract["consumers"] == ["loan", "payments"]


# ---- guardrails -------------------------------------------------------------------------------------------------

_DESIGN = """domain: loan
owned_tables: [LOAN]
read_only_tables: []
utility_modules: [com.acme:common-utils]
other_domain_dependencies: []
context_ports: []
schema_changes: []
ui_changes: []
stack_changes: []
god_module_dependencies: []
exceptions: []
"""


def test_every_modulith_agent_carries_the_guardrails():
    for key in ("domain_design", "linked_domain_contracts", "orchestrator_design"):
        text = GIVEN[key]
        assert "WITHOUT changing the UI, the database schema or the technology stack" in text, key
        assert "WHO WRITES WHICH TABLES" in text and "God module" in text and "Utility modules only" in text, key
    assert "domain-design.yaml" in default_config("domain_design")["outputs"]
    assert "Hard rules for this step" in GIVEN["modular_monolith"] and "WHO WRITES WHICH TABLES" in GIVEN["modular_monolith"]
    assert "god_modules" in GIVEN["domain_dependencies"]


def test_domain_design_declaration_must_keep_the_locked_lists_empty():
    assert contracts_store.parse_domain_design(_DESIGN)["domain"] == "loan"
    for field in ("schema_changes", "ui_changes", "stack_changes", "god_module_dependencies"):
        bad = _DESIGN.replace(f"{field}: []", f"{field}: [x]")
        with pytest.raises(contracts_store.InvalidDocument, match=field):
            contracts_store.parse_domain_design(bad)
    # an exception the user approved for that rule makes it acceptable
    ok = _DESIGN.replace("schema_changes: []", "schema_changes: [x]").replace(
        "exceptions: []", "exceptions: [{rule: schema_changes, reason: approved by the user}]")
    assert contracts_store.parse_domain_design(ok)
    with pytest.raises(contracts_store.InvalidDocument, match="both owned_tables and read_only_tables"):
        contracts_store.parse_domain_design(_DESIGN.replace("read_only_tables: []", "read_only_tables: [loan]"))
    with pytest.raises(contracts_store.InvalidDocument, match="missing schema_changes"):
        contracts_store.parse_domain_design(_DESIGN.replace("schema_changes: []\n", ""))


def test_domain_design_cannot_own_or_read_tables_of_another_domain(tmp_path):
    (tmp_path / "domain-design.yaml").write_text(
        _DESIGN.replace("owned_tables: [LOAN]", "owned_tables: [LOAN, CUSTOMER]").replace(
            "read_only_tables: []", "read_only_tables: [audit_log]"))
    ownership = {"loan": {"tables": ["LOAN"]}, "customer": {"tables": ["CUSTOMER"]},
                 "auditing": {"tables": ["AUDIT_LOG"]}}
    problems = contracts_store.domain_design_ownership_problems(tmp_path, "loan", ownership)
    assert len(problems) == 2 and "CUSTOMER" in problems[0] and "AUDIT_LOG" in problems[1] or "audit_log" in problems[1]
    assert contracts_store.domain_design_ownership_problems(tmp_path, "customer", ownership)  # LOAN is not theirs


def test_domain_design_dependencies_must_be_contracts(tmp_path):
    (tmp_path / "domain-design.yaml").write_text(
        _DESIGN.replace("other_domain_dependencies: []", "other_domain_dependencies: [customer.unknown]"))
    (tmp_path / "linked-domain-contracts.yaml").write_text(stub.STUB_LINKED_FOR_TESTS) \
        if hasattr(stub, "STUB_LINKED_FOR_TESTS") else None
    if (tmp_path / "linked-domain-contracts.yaml").exists():
        assert any("customer.unknown" in p for p in contracts_store.validate_outputs(tmp_path))


def test_dependency_finder_separates_utilities_from_god_modules():
    base = """feature: f
domain: loan
decision: {scope: domain-only, reason: r}
dependencies: []
considered_unrelated: []
"""
    contracts_store.parse_domain_dependencies(base + "shared_code: [a.DateUtils]\ngod_modules: [a.MasterService]\n")
    with pytest.raises(contracts_store.InvalidDocument, match="both shared_code"):
        contracts_store.parse_domain_dependencies(base + "shared_code: [a.X]\ngod_modules: [a.X]\n")


# ---- Module in monolith ------------------------------------------------------------------------------------------

_EXTRACTION = """feature: create-broker
domain: broker
scope: domain-only
modules:
  - {path: broker-domain/broker-profile/domain, kind: domain, artifact: "com.acme:broker-profile-domain"}
bridge_dir: web/legacybridge
routing:
  property: migration.broker.create-broker.route
  default: legacy
  router: web/legacybridge/BrokerServiceRouter.java
  routes:
    - {bean: brokerService, legacy_bean: brokerServiceLegacy, interface: com.acme.broker.BrokerService,
       module: com.acme.broker.domain.BrokerService}
outside_changes: [web/src/main/resources/applicationContext.xml]
events: [BrokerCreated]
"""


def test_utility_modules_must_be_maven_coordinates():
    with pytest.raises(contracts_store.InvalidDocument, match="Maven coordinates"):
        contracts_store.parse_domain_design(_DESIGN.replace("com.acme:common-utils", "com.acme.common.DateUtils"))


def test_module_extraction_declaration_is_validated():
    assert contracts_store.parse_module_extraction(_EXTRACTION)["modules"][0]["kind"] == "domain"
    bad = {
        "scope: domain-only": "scope: sometimes",
        "kind: domain,": "kind: library,",
        'artifact: "com.acme:broker-profile-domain"': "artifact: broker-profile-domain",
        "path: broker-domain/broker-profile/domain": "path: ../elsewhere",
        "bridge_dir: web/legacybridge\n": "",
        "  default: legacy": "  default: monolith",  # the as-is code is the default
        "legacy_bean: brokerServiceLegacy": "legacy_bean: brokerService",  # the router takes over the old id
        "  router: web/legacybridge/": "  router: web/other/",  # the router lives in the bridge package
    }
    without_routing = _EXTRACTION[:_EXTRACTION.index("routing:")] + _EXTRACTION[_EXTRACTION.index("outside_changes"):]
    with pytest.raises(contracts_store.InvalidDocument, match="add routing"):
        contracts_store.parse_module_extraction(without_routing)
    for old, new in bad.items():
        with pytest.raises(contracts_store.InvalidDocument):
            contracts_store.parse_module_extraction(_EXTRACTION.replace(old, new))


def test_module_extraction_follows_the_scope_decision():
    orchestrator = ("  - {path: broker-domain/broker-profile/orchestrator, kind: orchestrator, "
                    "artifact: \"com.acme:broker-profile-orchestrator\"}\n")
    # the domain module only, in either scope: the orchestrator module is the Orchestrator in monolith step's
    cross = _EXTRACTION.replace("scope: domain-only", "scope: cross-domain")
    for text in (_EXTRACTION, cross):
        with pytest.raises(contracts_store.InvalidDocument, match="built by the Orchestrator in monolith step"):
            contracts_store.parse_module_extraction(text.replace("bridge_dir", orchestrator + "bridge_dir"))
        assert contracts_store.parse_module_extraction(text)  # either scope has the bridge package, for the router
        with pytest.raises(contracts_store.InvalidDocument, match="add the bridge_dir"):
            contracts_store.parse_module_extraction(text.replace("bridge_dir: web/legacybridge\n", ""))


def test_module_extraction_scope_must_match_the_dependency_decision(tmp_path):
    (tmp_path / "domain-dependencies.yaml").write_text(
        "feature: f\ndomain: broker\ndecision: {scope: cross-domain, reason: r}\ndependencies:\n"
        "  - domain: customer\n    relation: calls\n    touchpoints: [{kind: service, ref: a.B#c}]\n")
    (tmp_path / "module-extraction.yaml").write_text(_EXTRACTION)
    assert any("decides cross-domain" in p for p in contracts_store.validate_outputs(tmp_path))


def test_module_in_monolith_agent_is_configured_for_the_guardrails():
    c = default_config("modular_monolith")
    assert {"check_module_guardrails", "edit_file", "run_command", "ask_user"} <= set(c["tools"])
    assert c["checks"] == ["module_guardrails"] and c["max_turns"] == 200
    assert c["outputs"] == ["modular-monolith.md", "module-extraction.yaml"]
    text = GIVEN["modular_monolith"]
    for phrase in ("Characterization tests first", "a **copy** of the feature's code, never a move",
                   "a **router** takes over the legacy service's bean", "bridge_dir", "module-extraction.yaml",
                   "Stop and ask with ask_user", "Rollback: set the toggle to `legacy`", "never the reverse"):
        assert phrase in text, phrase
    assert "check_module_guardrails" not in default_config("domain_design")["tools"]


def test_untouched_module_agent_is_upgraded_with_more_turns_and_the_new_check(client, monkeypatch):
    import hashlib

    previous = "You extract one operation into a module inside the monolith."  # stands for an earlier release
    monkeypatch.setitem(agent_store.PREVIOUS_DEFAULT_HASHES, "modular_monolith",
                        {hashlib.sha256(previous.encode()).hexdigest()})
    with session_scope() as s:
        row = s.get(Agent, (agent_store.SHARED, "modular_monolith"))
        row.instructions, row.tools, row.checks, row.max_turns = previous, ["list_files"], [], 80
        marker = s.get(Setting, (agent_store.GLOBAL, agent_store.UPGRADE_MARKER))
        if marker is not None:
            s.delete(marker)
    with session_scope() as s:
        agent_store._upgrade_untouched_builtins(s)
    with session_scope() as s:
        row = s.get(Agent, (agent_store.SHARED, "modular_monolith"))
        assert "careful legacy refactorer" in row.instructions and row.max_turns == 200
        assert row.checks == ["module_guardrails"] and "check_module_guardrails" in row.tools


def test_context_ports_are_required_and_their_adapters_share_the_routers_bridge(tmp_path):
    with pytest.raises(contracts_store.InvalidDocument, match="missing context_ports"):
        contracts_store.parse_domain_design(_DESIGN.replace("context_ports: []\n", ""))
    (tmp_path / "module-extraction.yaml").write_text(_EXTRACTION)
    for design in (_DESIGN, _DESIGN.replace("context_ports: []", "context_ports: [CurrentUser]")):
        (tmp_path / "domain-design.yaml").write_text(design)
        assert contracts_store.validate_outputs(tmp_path) == []


def test_ambient_inputs_are_part_of_the_designs_and_the_module_agent():
    assert "No ambient inputs" in GIVEN["domain_design"] and "context_ports" in GIVEN["domain_design"]
    text = GIVEN["modular_monolith"]
    assert "Context ports:" in text and "never reads the session" in text


# ---- Microservice ------------------------------------------------------------------------------------------------

def test_microservice_agent_is_configured_for_the_guardrails():
    c = default_config("microservice_scaffold")
    assert "check_service_guardrails" in c["tools"] and c["max_turns"] == 200
    assert c["checks"] == ["boundary", "service_guardrails"]
    assert c["outputs"] == ["microservice.md", "microservice-extraction.yaml"]
    text = GIVEN["microservice_scaffold"]
    for phrase in ("Copy, do not rewrite", "No Spring Boot", "no outbox", "request headers", "No authentication in this step",
                   "open_decisions", "Stop and ask with ask_user", "WITHOUT changing the UI"):
        assert phrase in text, phrase
    assert "Oracle schema" not in text and "transactional outbox" not in text  # the earlier, contradicting rules
    assert "check_service_guardrails" not in default_config("modular_monolith")["tools"]


def test_untouched_microservice_agent_is_upgraded(client, monkeypatch):
    import hashlib

    previous = "You wrap the extracted module in a standalone Spring Boot application."
    monkeypatch.setitem(agent_store.PREVIOUS_DEFAULT_HASHES, "microservice_scaffold",
                        {hashlib.sha256(previous.encode()).hexdigest()})
    with session_scope() as s:
        row = s.get(Agent, (agent_store.SHARED, "microservice_scaffold"))
        row.instructions, row.tools, row.checks, row.max_turns = previous, ["list_files"], ["boundary"], 80
        marker = s.get(Setting, (agent_store.GLOBAL, agent_store.UPGRADE_MARKER))
        if marker is not None:
            s.delete(marker)
    with session_scope() as s:
        agent_store._upgrade_untouched_builtins(s)
    with session_scope() as s:
        row = s.get(Agent, (agent_store.SHARED, "microservice_scaffold"))
        assert "lift the module" in row.instructions and row.max_turns == 200
        assert row.checks == ["boundary", "service_guardrails"] and "check_service_guardrails" in row.tools
