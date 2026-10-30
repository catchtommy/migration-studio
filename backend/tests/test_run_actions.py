"""The run page's actions end to end, as a user expects them: reject and re-run now, reject and amend first, re-run
from a step, retry a failed step (with notes), and each sent only once."""

import itertools

from app.agents import stub
from tests.conftest import start_run, wait_for
from tests.test_delivery import setup_project

_ids = itertools.count()


def new_run(client):
    r = start_run(client, {"domain": "customer", "operation": f"act{next(_ids)}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def ex(run, stage):
    return [s for s in run["stages"] if s["stage"] == stage]


def test_double_click_reject_now_sends_once(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    body = {"decision": "reject", "comments": "more", "rerun": "now"}
    first = client.post(f"/api/runs/{run_id}/decision", json=body)
    second = client.post(f"/api/runs/{run_id}/decision", json=body)
    assert first.status_code == 200
    assert second.status_code == 409, second.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert len([a for a in run["approvals"] if a["decision"] == "reject"]) == 1


def test_reject_now_amend_at_design_gate(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"})
    run = wait_for(client, run_id, {"awaiting_approval"})
    gate = run["pending_gate"]
    assert gate["gate"] == "gate_design"
    target = gate["reviews"][0]
    r = client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "fix X", "target_stage": target,
                                                         "scope": "amend"})
    assert r.status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    assert ex(run, target)[-1]["feedback"] == "fix X" and len(ex(run, target)) == 2
    for k in gate["reviews"][1:]:
        assert len(ex(run, k)) == 1, k  # kept as they are


def test_reject_later_then_change_target_and_scope_and_rerun(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    reviews = run["pending_gate"]["reviews"]
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "a", "target_stage": reviews[-1],
                                                     "rerun": "later", "scope": "amend"})
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "changes_requested"
    r = client.post(f"/api/runs/{run_id}/rerun", json={"comments": "b", "target_stage": reviews[0], "scope": "redo"})
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert ex(run, reviews[0])[-1]["feedback"] == "b"
    assert all(len(ex(run, k)) == 2 for k in reviews)  # redo: the steps after it re-run too


def test_restart_from_a_step_while_amendments_are_held(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    first = run["pending_gate"]["reviews"][0]
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "reject", "comments": "a", "rerun": "later"})
    r = client.post(f"/api/runs/{run_id}/restart", json={"stage": first})
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_rerun"] is None
    assert [a["decision"] for a in run["approvals"]] == []  # the held rejection never took effect


def _fail_once(monkeypatch, stage):
    original = stub.StubAgent.run
    seen = []

    def run(self, ctx):
        if self.stage == stage:
            seen.append(ctx.feedback)
            if len(seen) == 1:
                raise RuntimeError("boom")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)
    return seen


def drive(proj, run_id, stop):
    while True:
        run = wait_for(proj, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "awaiting_publish",
                                      "completed", "failed"}, timeout=60)
        if run["status"] in stop:
            return run
        if run["status"] == "awaiting_publish":
            proj.post(f"/api/runs/{run_id}/docs/skip")
        elif run["status"] == "awaiting_commit":
            assert proj.post(f"/api/runs/{run_id}/finish").status_code == 204
        elif run["status"] == "awaiting_branches":
            proj.post(f"/api/runs/{run_id}/branches", json={"feature_branch": run["pending_branches"]["feature_branch"]})
        elif run["status"] == "awaiting_approval":
            proj.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"})
        else:
            return run


def test_retry_with_notes_after_a_code_step_left_uncommitted_work(client, tmp_path, monkeypatch):
    """The step after a code step fails; retry with notes must re-run the failed step, not stop at the previous
    step's commit review."""
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    agents = [s["key"] for s in wf["steps"] if s["kind"] == "agent"]
    seen = _fail_once(monkeypatch, agents[1])
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = drive(proj, run_id, {"failed"})
    assert run["status"] == "failed" and run["current_stage"] == agents[1], (run["status"], run["current_stage"])
    assert proj.post(f"/api/runs/{run_id}/retry", json={"notes": "try harder"}).status_code == 200
    run = wait_for(proj, run_id, {"awaiting_approval", "awaiting_commit", "awaiting_branches", "failed"}, timeout=60)
    assert (run["status"], run["current_stage"]) != ("awaiting_commit", agents[0]), "retry stopped at the previous step's commit"
    assert seen[-1] == "try harder"


def test_restart_of_the_step_after_a_code_step_runs_that_step(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    agents = [s["key"] for s in wf["steps"] if s["kind"] == "agent"]
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = drive(proj, run_id, {"awaiting_approval"})
    before = len(ex(run, agents[1]))
    assert proj.post(f"/api/runs/{run_id}/restart", json={"stage": agents[1]}).status_code == 200
    run = wait_for(proj, run_id, {"awaiting_approval", "awaiting_commit", "awaiting_branches", "failed"}, timeout=60)
    assert (run["status"], run["current_stage"]) != ("awaiting_commit", agents[0])
    assert len(ex(run, agents[1])) == before + 1 and len(ex(run, agents[0])) == 1
