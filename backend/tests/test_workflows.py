import itertools

from tests.conftest import wait_for

_ids = itertools.count()


def _key(prefix):
    return f"{prefix}{next(_ids)}"


def _two_step(gate_reviews=("as_is_doc",)):
    return [{"kind": "agent", "key": "as_is_doc"},
            {"kind": "gate", "title": "Check docs", "reviews": list(gate_reviews)},
            {"kind": "agent", "key": "deep_dive"}]


def test_standard_workflow_is_seeded(client):
    standard = client.get("/api/workflows/standard").json()
    assert [s["key"] for s in standard["stages"]][:3] == ["as_is_doc", "deep_dive", "gate_analysis"]
    assert standard["stages"][0]["title"] == "AS-IS documentation"  # agent titles come from the library


def test_custom_workflow_runs_its_own_steps_and_gates(client):
    key = _key("wf")
    r = client.post("/api/workflows", json={"key": key, "name": "Docs only", "steps": _two_step()})
    assert r.status_code == 201, r.text
    gate = r.json()["steps"][1]
    assert gate["key"] == "gate_check_docs"  # generated from the title

    run = client.post("/api/runs", json={"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    assert [s["key"] for s in run["workflow_stages"]] == ["as_is_doc", "gate_check_docs", "deep_dive"]
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"] == {"gate": "gate_check_docs", "reviews": ["as_is_doc"]}

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "reject", "comments": "More"})
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert [s["status"] for s in run["stages"] if s["stage"] == "as_is_doc"] == ["completed", "completed"]

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    run = wait_for(client, run["id"], {"completed"})
    assert [s["stage"] for s in run["stages"]] == [
        "as_is_doc", "gate_check_docs", "as_is_doc", "gate_check_docs", "deep_dive"]


def test_running_runs_keep_the_workflow_they_started_with(client):
    key = _key("wf")
    client.post("/api/workflows", json={"key": key, "name": "Snapshot", "steps": _two_step()})
    run = client.post("/api/runs", json={"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    wait_for(client, run["id"], {"awaiting_approval"})

    # Remove the gate and the second agent, then delete the workflow altogether.
    r = client.put(f"/api/workflows/{key}", json={"steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert r.status_code == 200
    assert client.delete(f"/api/workflows/{key}").status_code == 204

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    done = wait_for(client, run["id"], {"completed"})
    assert [s["stage"] for s in done["stages"]] == ["as_is_doc", "gate_check_docs", "deep_dive"]


def test_invalid_workflows_are_refused(client):
    def create(steps):
        return client.post("/api/workflows", json={"key": _key("bad"), "name": "Bad", "steps": steps})

    assert "at least one agent" in create([]).json()["detail"]
    assert "unknown agent" in create([{"kind": "agent", "key": "nope"}]).json()["detail"]
    assert "already in this workflow" in create([{"kind": "agent", "key": "as_is_doc"}] * 2).json()["detail"]
    r = create([{"kind": "gate", "title": "Too early", "reviews": ["as_is_doc"]}, {"kind": "agent", "key": "as_is_doc"}])
    assert "before it" in r.json()["detail"]
    r = create([{"kind": "agent", "key": "as_is_doc"}, {"kind": "gate", "title": "Empty", "reviews": []}])
    assert "at least one" in r.json()["detail"]
    assert client.post("/api/workflows", json={"key": "standard", "name": "Dup", "copy_from": "standard"}).status_code == 422


def test_custom_agent_in_a_copied_workflow(client):
    agent = _key("security_review")
    r = client.post("/api/agents", json={"key": agent, "title": "Security review", "description": "OWASP pass"})
    assert r.status_code == 201 and r.json()["builtin"] is False and r.json()["outputs"] == [f"{agent}.md"]
    assert client.post(f"/api/agents/{agent}/reset").status_code == 409  # no default for custom agents

    wf = _key("wf")
    copied = client.post("/api/workflows", json={"key": wf, "name": "With security", "copy_from": "standard"}).json()
    steps = copied["steps"][:2] + [{"kind": "agent", "key": agent},
                                   {"kind": "gate", "title": "Approve security", "reviews": [agent, "deep_dive"]}]
    assert client.put(f"/api/workflows/{wf}", json={"steps": steps}).status_code == 200

    run = client.post("/api/runs", json={"domain": "payments", "operation": _key("op"), "workflow": wf}).json()
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"]["reviews"] == [agent, "deep_dive"]
    artifacts = client.get(f"/api/runs/{run['id']}/artifacts").json()
    assert {a["stage"] for a in artifacts} == {"as_is_doc", "deep_dive", agent}

    r = client.delete(f"/api/agents/{agent}")
    assert r.status_code == 409 and "With security" in r.json()["detail"]
    client.put(f"/api/workflows/{wf}", json={"steps": copied["steps"][:2]})
    assert client.delete(f"/api/agents/{agent}").status_code == 409  # the unfinished run still uses it
    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    wait_for(client, run["id"], {"completed"})
    assert client.delete(f"/api/agents/{agent}").status_code == 204


def test_domain_default_workflow_is_used_and_cleared_on_delete(client):
    wf = _key("wf")
    client.post("/api/workflows", json={"key": wf, "name": "Short", "steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert client.put("/api/domains/reports", json={"default_workflow": "missing"}).status_code == 422
    assert client.put("/api/domains/reports", json={"default_workflow": wf}).json()["default_workflow"] == wf

    run = client.post("/api/runs", json={"domain": "reports", "operation": _key("op")}).json()
    assert run["workflow"] == wf
    wait_for(client, run["id"], {"completed"})

    client.delete(f"/api/workflows/{wf}")
    assert client.get("/api/domains/reports").json()["default_workflow"] is None
    assert client.post("/api/runs", json={"domain": "reports", "operation": _key("op")}).json()["workflow"] == "standard"



def _doc(run, stage):
    from pathlib import Path
    return (Path(run["workspace"]) / ".migration" / run["domain"] / run["operation"] / f"{stage}.md").read_text()


def test_steps_receive_only_the_outputs_they_use(client):
    key = _key("wf")
    steps = [{"kind": "agent", "key": "as_is_doc"},
             {"kind": "agent", "key": "boundary_design", "uses": []},
             {"kind": "agent", "key": "deep_dive", "uses": ["as_is_doc"], "include_content": True},
             {"kind": "agent", "key": "modernise"}]  # default: every earlier step
    r = client.post("/api/workflows", json={"key": key, "name": "Uses", "steps": steps})
    assert r.status_code == 201, r.text
    assert r.json()["stages"][2]["uses"] == ["as_is_doc"] and r.json()["stages"][3]["uses"] is None

    run = client.post("/api/runs", json={"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    run = wait_for(client, run["id"], {"completed"})
    assert "Inputs from earlier steps" not in _doc(run, "boundary_design")
    deep = _doc(run, "deep_dive")
    assert "AS-IS documentation: `" in deep and "as_is_doc.md` (contents included)" in deep
    assert "boundary_design" not in deep
    modern = _doc(run, "modernise")
    assert all(f"{s}.md`" in modern for s in ("as_is_doc", "boundary_design", "deep_dive"))
    assert "contents included" not in modern


def test_a_step_can_only_use_earlier_outputs(client):
    steps = [{"kind": "agent", "key": "as_is_doc", "uses": ["deep_dive"]}, {"kind": "agent", "key": "deep_dive"}]
    r = client.post("/api/workflows", json={"key": _key("bad"), "name": "Bad", "steps": steps})
    assert r.status_code == 422 and "before it" in r.json()["detail"]
