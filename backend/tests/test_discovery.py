import itertools

import pytest

from app import discovery_store
from app.agents import llm
from app.agents.stub import SAMPLE_CONTEXT
from app.contracts_store import InvalidDocument
from app.db.models import Run
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_agents_domains import ScriptedClient, _reply, _text, _tool, ctx  # noqa: F401  (fixture)

_ids = itertools.count()


def _approve(client, run_id):
    return client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})


def _finish(client, run_id):
    """Wait for the run's gate, approve it and wait for the run to complete."""
    wait_for(client, run_id, {"awaiting_approval"})
    r = _approve(client, run_id)
    assert r.status_code == 200, r.text
    return wait_for(client, run_id, {"completed"})


def _artifact(client, run_id, name):
    return next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["name"] == name)


def _domain(client, label):
    key = f"{label}{next(_ids)}"
    assert client.post("/api/domains", json={"key": key, "name": key.capitalize()}).status_code == 201
    return key


# --- documents -----------------------------------------------------------------------------------------------------

def test_discovery_documents_are_validated(client):  # client: the app (and its database) is up
    good = """contexts:
  - {key: loan, name: Loan, tables: [LOAN], packages: [com.acme.loan], depends_on: [customer]}
  - {key: fees, name: Fees, tables: [FEE], depends_on: [loan]}
"""
    assert [c["key"] for c in discovery_store.parse_bounded_contexts(good)] == ["loan", "fees"]
    with pytest.raises(InvalidDocument, match="owned by both"):
        discovery_store.parse_bounded_contexts(good.replace("[FEE]", "[loan]"))
    with pytest.raises(InvalidDocument, match="neither a context"):
        discovery_store.parse_bounded_contexts(good.replace("depends_on: [loan]", "depends_on: [nowhere]"))
    with pytest.raises(InvalidDocument, match="lower-case"):
        discovery_store.parse_bounded_contexts(good.replace("key: fees", "key: Fees"))

    features = "features:\n  - {key: create-loan, name: Create loan, calls: [customer]}\n"
    assert discovery_store.parse_features(features)[0]["calls"] == ["customer"]
    with pytest.raises(InvalidDocument, match="not a known domain"):
        discovery_store.parse_features(features.replace("[customer]", "[martians]"))
    with pytest.raises(InvalidDocument, match="duplicate key"):
        discovery_store.parse_features(features + "  - {key: create-loan, name: Again}\n")
    with pytest.raises(InvalidDocument, match="at least one feature"):
        discovery_store.parse_features("features: []\n")


# --- bounded contexts ----------------------------------------------------------------------------------------------

def test_bounded_contexts_update_the_domains(client):
    client.put("/api/domains/loan", json={"description": "Kept as written", "owned_tables": ["LOAN_OLD"]})
    r = client.post("/api/discovery/bounded-contexts")
    assert r.status_code == 201, r.text
    run = r.json()
    assert (run["scope"], run["domain"], run["workflow"]) == ("monolith", "_monolith", "discover_bounded_contexts")
    assert client.post("/api/discovery/bounded-contexts").status_code == 409  # one at a time

    wait_for(client, run["id"], {"awaiting_approval"})
    assert _artifact(client, run["id"], "bounded-contexts.yaml")
    _finish(client, run["id"])
    lonely = _domain(client, "lonely")  # added since, so not among the approved contexts

    loan = client.get("/api/domains/loan").json()
    assert loan["description"] == "Kept as written"  # filled only when empty
    assert loan["owned_tables"] == ["LOAN_OLD", "LOAN"] and "com.acme.loan" in loan["owned_packages"]
    new = client.get(f"/api/domains/{SAMPLE_CONTEXT}").json()
    assert new["owned_tables"] == ["DOCUMENTS"]
    assert new["agent_inputs"]["domain_features"]["paths"]  # entry points guide the feature listing

    overview = client.get("/api/discovery").json()
    assert overview["contexts_approved"] and overview["bounded_contexts"]["status"] == "completed"
    keys = [d["key"] for d in overview["domains"]]
    assert lonely in keys and lonely in overview["not_found"]  # flagged, never deleted
    assert "loan" not in overview["not_found"]


def test_discovery_runs_get_paths_and_notes(client):
    inputs = {"paths": ["docs/architecture/*.md", " "], "notes": "Docs may be out of date; the code wins."}
    run_id = client.post("/api/discovery/bounded-contexts", json={"inputs": inputs}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    doc = _artifact(client, run_id, "bounded_contexts.md")
    content = client.get(f"/api/artifacts/{doc['id']}").json()["content"]
    assert "Entry points and files for this discovery run" in content and "docs/architecture/*.md" in content
    assert "the code wins" in content
    assert client.get("/api/discovery").json()["contexts_inputs"] == {"paths": ["docs/architecture/*.md"],
                                                                      "notes": inputs["notes"]}
    _finish(client, run_id)


def test_invalid_bounded_contexts_block_approval(client):
    run_id = client.post("/api/discovery/bounded-contexts").json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    doc = _artifact(client, run_id, "bounded-contexts.yaml")
    client.put(f"/api/artifacts/{doc['id']}", json={"content": "contexts: [{key: Bad Key, name: x}]\n"})
    r = _approve(client, run_id)
    assert r.status_code == 422 and "can't be recorded" in r.json()["detail"]
    client.put(f"/api/artifacts/{doc['id']}", json={"content": "contexts: [{key: loan, name: Loan}]\n"})
    _finish(client, run_id)


def test_agent_writing_invalid_discovery_yaml_is_sent_back(ctx, monkeypatch):  # noqa: F811
    ctx, logs, _ = ctx
    ctx.config.update({"outputs": ["features.yaml"]})
    out = f"{ctx.rel(ctx.output_dir)}/features.yaml"
    client = ScriptedClient([
        _reply(_tool("write_file", path=out, content="features: [{key: 1bad, name: X}]\n")),
        _reply(_text("Done"), stop="end_turn"),
        _reply(_tool("write_file", path=out, content="features: [{key: view-loan, name: View loan}]\n")),
        _reply(_text("Fixed"), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    llm.ClaudeAgent("domain_features").run(ctx)
    assert "features.yaml feature 1: key '1bad'" in client.requests[2]["messages"][-1]["content"]
    assert len(client.requests) == 4


# --- features ------------------------------------------------------------------------------------------------------

def test_migrations_need_a_listed_feature(client):
    key = _domain(client, "fx")
    r = client.post("/api/runs", json={"domain": key, "operation": "anything"})
    assert r.status_code == 422 and "features first" in r.json()["detail"]

    run = client.post(f"/api/domains/{key}/discover-features").json()
    assert (run["scope"], run["operation"], run["workflow"]) == ("domain", "_features", "list_features")
    _finish(client, run["id"])
    features = client.get(f"/api/domains/{key}/features").json()
    assert [f["key"] for f in features] == [f"view-{key}", f"create-{key}", f"close-{key}"]
    assert all(f["run_id"] == run["id"] and f["migration"] is None for f in features)

    r = client.post("/api/runs", json={"domain": key, "operation": "anything"})
    assert r.status_code == 422 and "not in" in r.json()["detail"]
    r = client.post("/api/runs", json={"domain": key, "operation": f"view-{key}"})
    assert r.status_code == 201, r.text
    migration = client.get(f"/api/runs/{r.json()['id']}").json()
    assert migration["scope"] == "operation"
    assert migration["inputs"]["paths"] == features[0]["entry_points"]  # the feature's entry points by default
    assert client.get(f"/api/domains/{key}/features").json()[0]["migration"]["id"] == migration["id"]

    # Discovery runs are not counted as migrations.
    summary = next(d for d in client.get("/api/domains").json() if d["key"] == key)
    assert (summary["runs"], summary["features"]) == (1, 3)
    wait_for(client, migration["id"], {"awaiting_approval"})

    # Re-listing replaces the list, but keeps features that are already being migrated.
    rerun = client.post(f"/api/domains/{key}/discover-features").json()
    wait_for(client, rerun["id"], {"awaiting_approval"})
    doc = _artifact(client, rerun["id"], "features.yaml")
    client.put(f"/api/artifacts/{doc['id']}", json={"content": "features:\n  - {key: brand-new, name: Brand new}\n"})
    _finish(client, rerun["id"])
    after = {f["key"]: f for f in client.get(f"/api/domains/{key}/features").json()}
    assert set(after) == {f"view-{key}", "brand-new"}
    assert after[f"view-{key}"]["missing_from_latest"] and not after["brand-new"]["missing_from_latest"]


def test_features_can_be_edited_by_hand(client):
    key = _domain(client, "hand")
    r = client.post(f"/api/domains/{key}/features", json={"key": "pay-fee", "name": "Pay fee",
                                                          "entry_points": [" FeeController.pay ", ""]})
    assert r.status_code == 201 and r.json()["entry_points"] == ["FeeController.pay"] and r.json()["run_id"] is None
    assert client.post(f"/api/domains/{key}/features", json={"key": "pay-fee", "name": "x"}).status_code == 409
    assert client.put(f"/api/domains/{key}/features/pay-fee", json={"tables": ["FEE"]}).json()["tables"] == ["FEE"]
    run = start_run(client, {"domain": key, "operation": "pay-fee"}).json()
    assert client.delete(f"/api/domains/{key}/features/pay-fee").status_code == 409  # it has a migration
    wait_for(client, run["id"], {"awaiting_approval"})
    client.post(f"/api/domains/{key}/features", json={"key": "spare", "name": "Spare"})
    assert client.delete(f"/api/domains/{key}/features/spare").status_code == 204
    assert client.put(f"/api/domains/{key}/features/spare", json={"name": "x"}).status_code == 404


def test_list_features_for_all_domains(client):
    listed = _domain(client, "listed")
    client.post(f"/api/domains/{listed}/features", json={"key": "one", "name": "One"})
    fresh = _domain(client, "fresh")
    runs = client.post("/api/discovery/features").json()
    domains = {r["domain"] for r in runs}
    assert fresh in domains and listed not in domains  # only domains without a feature list
    again = client.post("/api/discovery/features").json()
    assert fresh not in {r["domain"] for r in again}  # already running
    for r in runs + again:
        wait_for(client, r["id"], {"awaiting_approval"})
        _approve(client, r["id"])
    with session_scope() as s:
        assert s.get(Run, runs[0]["id"]).scope == "domain"


def test_workflow_scopes(client):
    workflows = {w["key"]: w for w in client.get("/api/workflows").json()}
    assert workflows["standard"]["scope"] == "operation"
    assert workflows["discover_bounded_contexts"]["scope"] == "monolith"
    copy = client.post("/api/workflows", json={"key": f"copy{next(_ids)}", "name": "Copy",
                                               "copy_from": "list_features"}).json()
    assert copy["scope"] == "domain"
    assert client.put("/api/domains/loan", json={"default_workflow": copy["key"]}).status_code == 422
    r = start_run(client, {"domain": "loan", "operation": f"s{next(_ids)}", "workflow": copy["key"]})
    assert r.status_code == 422 and "not a migration workflow" in r.json()["detail"]
    assert client.delete(f"/api/workflows/{copy['key']}").status_code == 204
    assert client.delete("/api/workflows/discover_bounded_contexts?library=shared").status_code == 409  # the last
