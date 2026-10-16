import itertools
from pathlib import Path

import pytest

from app import agent_store, boundary_check, contracts_store, workflow_store
from app.agents import llm
from app.db.models import SHARED, Agent, Setting, WorkflowDef
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_agents_domains import ScriptedClient, _reply, _text, _tool, ctx  # noqa: F401  (fixture)

_ids = itertools.count()


def _start(client, domain):
    r = start_run(client, {"domain": domain, "operation": f"xd{next(_ids)}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _approve(client, run_id):
    return client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})


def _to_design_gate(client, run_id):
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"
    assert _approve(client, run_id).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"] == {"gate": "gate_design", "reviews": [
        "domain_dependencies", "linked_domain_contracts", "domain_design", "orchestrator_design",
        "integration_design"]}
    return run


def _other(client, domain):
    """The domain the stub's sample decisions point at."""
    keys = sorted(d["key"] for d in client.get("/api/domains").json())
    return next(k for k in keys if k != domain)


# --- documents -----------------------------------------------------------------------------------------------------

def test_cross_domain_documents_are_validated(client):  # client: the app (and its database) is up
    good = "interactions:\n  - {id: X1, target: customer, kind: query, summary: Reads KYC}\n"
    assert contracts_store.parse_cross_domain(good)[0]["id"] == "X1"
    with pytest.raises(contracts_store.InvalidDocument, match="must be one of"):
        contracts_store.parse_cross_domain(good.replace("query", "telepathy"))
    with pytest.raises(contracts_store.InvalidDocument, match="not valid YAML"):
        contracts_store.parse_cross_domain("interactions: [")

    decisions = """decisions:
  - {interaction: X1, target: customer, kind: query, pattern: sync-api, contract: customer.kyc}
contracts:
  - {key: customer.kyc, provider: customer, kind: api, name: KYC}
"""
    assert contracts_store.parse_decisions(decisions)["contracts"][0]["key"] == "customer.kyc"
    with pytest.raises(contracts_store.InvalidDocument, match="neither listed"):
        contracts_store.parse_decisions(decisions.replace("contract: customer.kyc", "contract: customer.nope"))
    with pytest.raises(contracts_store.InvalidDocument, match="needs a contract"):
        contracts_store.parse_decisions(decisions.replace(", contract: customer.kyc", ""))
    assert contracts_store.parse_decisions(decisions.replace("sync-api, contract: customer.kyc", "deferred"))


# --- catalogue -----------------------------------------------------------------------------------------------------

def test_approved_design_fills_the_catalogue_and_is_reused_by_later_domains(client):
    loan_run = _start(client, "loan")
    _to_design_gate(client, loan_run)
    other = _other(client, "loan")
    assert _approve(client, loan_run).status_code == 200

    operation = client.get(f"/api/runs/{loan_run}").json()["operation"]
    query, event = f"{other}.get-{other}-summary", f"loan.{operation}-completed"
    contracts = {c["key"]: c for c in client.get("/api/contracts").json()}
    assert contracts[query]["provider"] == other and contracts[query]["consumers"] == ["loan"]
    assert contracts[query]["spec"].startswith("openapi") and contracts[event]["kind"] == "event"

    deps = client.get("/api/domains/loan/dependencies").json()
    mine = [d for d in deps if d["run_id"] == loan_run]
    assert {(d["source"], d["target"], d["pattern"]) for d in mine} == {
        ("loan", other, "sync-api"), (other, "loan", "event")}

    dep_map = client.get("/api/domains/dependency-map").json()
    edges = {(e["source"], e["target"]): e for e in dep_map["edges"]}
    assert edges[("loan", other)]["patterns"]["sync-api"] >= 1
    # loan calls `other` synchronously, so `other` has a synchronous dependant and comes after loan.
    order = [step["domain"] for step in dep_map["order"]]
    assert order.index("loan") < order.index(other)
    assert len(order) == len(set(order)) == len(client.get("/api/domains").json())
    reasons = {step["domain"]: step["reason"] for step in dep_map["order"]}
    assert "depend on it synchronously (loan" in reasons[other]

    # A second domain needing the same contract adds itself as a consumer instead of creating a duplicate.
    client.put(f"/api/contracts/{query}", json={"status": "provided-by-monolith"})
    pay_run = _start(client, "payments")
    _to_design_gate(client, pay_run)
    assert _approve(client, pay_run).status_code == 200
    reused = client.get(f"/api/contracts/{query}").json()
    assert reused["consumers"] == ["loan", "payments"]
    assert reused["status"] == "provided-by-monolith"  # re-approval never moves a contract back

    # Later stages of a run see the catalogue: specs are copied into the workspace.
    run = wait_for(client, pay_run, {"awaiting_approval"})
    assert (Path(run["workspace"]) / contracts_store.CATALOGUE_DIR / f"{query}.yaml").is_file()


def test_rejected_design_records_nothing(client):
    run_id = _start(client, "broker")
    _to_design_gate(client, run_id)
    r = client.post(f"/api/runs/{run_id}/decision",
                    json={"reviewer": "t", "decision": "reject", "comments": "Use events",
                          "target_stage": "integration_design"})
    assert r.status_code == 200
    assert not [d for d in client.get("/api/domains/broker/dependencies").json() if d["run_id"] == run_id]


def test_invalid_decisions_block_approval(client):
    run_id = _start(client, "reports")
    _to_design_gate(client, run_id)
    decisions = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json()
                     if a["name"] == "integration-decisions.yaml")
    client.put(f"/api/artifacts/{decisions['id']}", json={"content": "decisions: [{pattern: telepathy}]\n"})
    r = _approve(client, run_id)
    assert r.status_code == 422 and "can't be recorded" in r.json()["detail"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"


# --- boundary check ------------------------------------------------------------------------------------------------

def _service(tmp_path, files):
    for rel, text in files.items():
        f = tmp_path / "services/loan-service" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return tmp_path


OWNERSHIP = {"loan": {"tables": ["LOAN"], "packages": ["com.acme.loan"]},
             "customer": {"tables": ["CUSTOMER", "CUST_KYC"], "packages": ["com.acme.customer"]}}


def test_boundary_check_finds_other_domains_packages_and_tables(tmp_path):
    ws = _service(tmp_path, {
        "src/main/java/com/acme/loan/LoanService.java":
            "package com.acme.loan;\nimport com.acme.customer.CustomerDao;\nclass LoanService {}\n",
        "src/main/resources/Loan.xml": "<select>SELECT * FROM loan l JOIN cust_kyc k ON k.id = l.cid</select>\n",
        "src/main/java/com/acme/loan/Ok.java": "package com.acme.loan;\n// CUSTOMER_ID is a column, not the table\n",
        "target/generated/Old.java": "import com.acme.customer.X;\n",  # build output is skipped
    })
    found = boundary_check.check(ws, "loan", "services/loan-service", OWNERSHIP)
    assert [(Path(v.path).name, v.line, v.rule) for v in found] == [
        ("LoanService.java", 2, "package"), ("Loan.xml", 1, "table")]
    assert "CUST_KYC" in boundary_check.report(found)
    assert boundary_check.check(ws, "loan", "services/loan-service", {"loan": OWNERSHIP["loan"]}) == []
    assert boundary_check.check(ws, "loan", "services/missing", OWNERSHIP)[0].rule == "missing-service"


def test_agent_with_boundary_check_is_sent_back_until_clean(ctx, monkeypatch):  # noqa: F811
    ctx, logs, _ = ctx
    ctx.config.update({"outputs": [], "checks": ["boundary"], "tools": [*ctx.config["tools"], "edit_file"]})
    ctx.ownership, ctx.service_dir = OWNERSHIP, "services/loan-service"
    svc = "services/loan-service/src/LoanService.java"
    client = ScriptedClient([
        _reply(_tool("write_file", path=svc, content="import com.acme.customer.CustomerDao;\n")),
        _reply(_text("Done"), stop="end_turn"),
        _reply(_tool("write_file", path=svc, content="import com.acme.loan.port.CustomerDirectory;\n")),
        _reply(_text("Fixed"), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    llm.ClaudeAgent("microservice_scaffold").run(ctx)
    feedback = client.requests[2]["messages"][-1]["content"]
    assert "Boundary:" in feedback and "com.acme.customer" in feedback
    assert len(client.requests) == 4


# --- upgrades ------------------------------------------------------------------------------------------------------

def _steps_of_shape(shape):
    return [{"kind": k, "key": key, "uses": None, "include_content": False} if k == "agent" else
            {"kind": k, "key": key, "title": key, "description": "", "reviews": list(reviews)}
            for k, key, reviews in shape]


def test_standard_workflow_upgrade_only_touches_an_unmodified_workflow(client):
    v1 = _steps_of_shape(workflow_store._STANDARD_V1)

    def reset(steps):
        with session_scope() as s:
            s.get(WorkflowDef, (SHARED, "standard")).steps = steps
            s.query(Setting).filter(Setting.key == "_standard_workflow_v2").delete()

    original = client.get("/api/workflows/standard").json()["steps"]
    reset(v1)
    workflow_store.seed()
    assert "integration_design" in [st["key"] for st in client.get("/api/workflows/standard").json()["steps"]]

    customised = [st for st in v1 if st["key"] != "modernise"]
    reset(customised)
    workflow_store.seed()
    assert "integration_design" not in [st["key"] for st in client.get("/api/workflows/standard").json()["steps"]]
    reset(original)
    workflow_store.seed()


def test_new_builtin_agent_is_added_once(client):
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == "_added_integration_design").delete()
        s.delete(s.get(Agent, (SHARED, "integration_design")))
    agent_store.seed()
    assert agent_store.exists("integration_design")
    with session_scope() as s:
        s.delete(s.get(Agent, (SHARED, "integration_design")))
    agent_store.seed()  # marker present: a deliberately deleted built-in stays deleted
    assert not agent_store.exists("integration_design")
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == "_added_integration_design").delete()
    agent_store.seed()
    assert agent_store.exists("integration_design")


def test_untouched_builtin_agents_are_brought_up_to_date_once(client, monkeypatch):
    import hashlib
    from app.agents.defaults import default_config

    old = "Old default instructions from an earlier release."
    monkeypatch.setattr(agent_store, "PREVIOUS_DEFAULT_HASHES", {
        "deep_dive": {hashlib.sha256(old.encode()).hexdigest()},
        "modernise": {hashlib.sha256(old.encode()).hexdigest()},
    })
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == agent_store.UPGRADE_MARKER).delete()
        deep = s.get(Agent, (SHARED, "deep_dive"))
        deep.instructions, deep.outputs, deep.mode, deep.model = old, ["deep-dive.md"], "llm", "gpt-5"
        s.get(Agent, (SHARED, "modernise")).instructions = "Edited by the team."  # not a known default: left alone
    agent_store.seed()

    deep = client.get("/api/agents/deep_dive").json()
    assert deep["instructions"] == default_config("deep_dive")["instructions"]
    assert "cross-domain.yaml" in deep["outputs"]
    assert (deep["mode"], deep["model"]) == ("llm", "gpt-5")  # kept
    assert client.get("/api/agents/modernise").json()["instructions"] == "Edited by the team."

    with session_scope() as s:  # runs once: a later old-looking text is not touched
        s.get(Agent, (SHARED, "deep_dive")).instructions = old
    agent_store.seed()
    assert client.get("/api/agents/deep_dive").json()["instructions"] == old
    for key in ("deep_dive", "modernise"):
        client.post(f"/api/agents/{key}/reset")
