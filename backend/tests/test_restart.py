import itertools
from pathlib import Path

from app.db.models import StageExecution
from app.workspace import _effective
from tests.conftest import document, start_run, wait_for
from tests.test_auth import make_user, signed_in

GATES = ["gate_analysis", "gate_design", "gate_tests", "gate_release"]
_ids = itertools.count()


def new_run(client, domain="payments"):
    r = start_run(client, {"domain": domain, "operation": f"rs{next(_ids)}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def approve(client, run_id):
    assert client.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"}).status_code == 200


def attempts(run, stage):
    return [s for s in run["stages"] if s["stage"] == stage]


def doc(run, stage):
    return document(run, stage)


def completed_run(client):
    run_id = new_run(client)
    for _ in GATES:
        wait_for(client, run_id, {"awaiting_approval"})
        approve(client, run_id)
    return wait_for(client, run_id, {"completed"})


def test_restart_a_completed_run_from_a_middle_stage(client):
    run = completed_run(client)
    later_doc = doc(run, "modernise")
    assert later_doc.is_file()

    r = client.post(f"/api/runs/{run['id']}/restart", json={"stage": "deep_dive", "notes": "Cover the batch job too"})
    assert r.status_code == 200, r.text
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"  # the first gate after the stage asks again

    assert len(attempts(run, "as_is_doc")) == 1  # earlier stages don't re-run
    dive = attempts(run, "deep_dive")
    assert [a["attempt"] for a in dive] == [1, 2] and dive[1]["rerun"] is True
    assert dive[1]["feedback"] == "Cover the batch job too"
    text = doc(run, "deep_dive").read_text()
    assert "## Re-run" in text and "Cover the batch job too" in text
    assert later_doc.is_file()  # nothing was rolled back: later stages' documents are there to be updated

    # Carrying on re-runs every later stage in place too.
    for _ in GATES:
        wait_for(client, run["id"], {"awaiting_approval"})
        approve(client, run["id"])
    run = wait_for(client, run["id"], {"completed"})
    assert attempts(run, "monolith_integration")[-1]["rerun"] is True
    assert "## Re-run" in later_doc.read_text()


def test_restart_while_waiting_at_a_gate_and_from_the_first_stage(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"

    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "domain_design"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    assert len(attempts(run, "domain_design")) == 2 and len(attempts(run, "domain_dependencies")) == 1

    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"
    assert attempts(run, "as_is_doc")[-1]["rerun"] is True


def test_restart_is_refused_when_it_cannot_apply(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "gate_analysis"}).status_code == 422
    r = client.post(f"/api/runs/{run_id}/restart", json={"stage": "modernise"})
    assert r.status_code == 422 and "hasn't completed" in r.json()["detail"]
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "nope"}).status_code == 422


def test_restart_needs_the_run_migrations_permission(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    reviewer = signed_in(make_user(client, ["reviewer"])["username"])
    assert reviewer.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 403
    developer = signed_in(make_user(client, ["developer"])["username"])
    assert developer.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 200
    wait_for(client, run_id, {"awaiting_approval"})


def test_rebuild_chain_keeps_later_stages_after_an_in_place_rerun():
    defs = [{"key": k, "kind": "agent"} for k in ("a", "b", "c")]
    ex = lambda i, stage, status="completed", rerun=None: StageExecution(id=i, stage=stage, status=status, rerun=rerun)
    history = [ex(1, "a"), ex(2, "b"), ex(3, "c"), ex(4, "b", rerun=True), ex(5, "c", "failed", rerun=True)]
    assert [(e.stage, e.id) for e in _effective(history, defs, None)] == [("a", 1), ("c", 3), ("b", 4)]
    # A normal re-run (rejection or retry) still undoes the later stages.
    assert [(e.stage, e.id) for e in _effective([ex(1, "a"), ex(2, "b"), ex(3, "c"), ex(4, "b")], defs, None)] == \
        [("a", 1), ("b", 4)]


def test_fresh_restart_rolls_back_and_regenerates(client):
    run = completed_run(client)
    later_doc, dive_doc = doc(run, "modernise"), doc(run, "deep_dive")
    r = client.post(f"/api/runs/{run['id']}/restart", json={"stage": "deep_dive", "fresh": True})
    assert r.status_code == 200, r.text
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"
    dive = attempts(run, "deep_dive")
    assert len(dive) == 2 and not dive[1]["rerun"]  # an ordinary attempt, not an in-place update
    assert "## Re-run" not in dive_doc.read_text()
    assert not later_doc.exists()  # later steps were rolled back and will regenerate
    assert len(attempts(run, "as_is_doc")) == 1 and doc(run, "as_is_doc").is_file()
    assert len(attempts(run, "modernise")) == 1  # history kept


def test_fresh_restart_keeps_earlier_in_place_reruns(client):
    run = completed_run(client)
    # Update step 1 in place; it then waits at the first gate.
    assert client.post(f"/api/runs/{run['id']}/restart", json={"stage": "as_is_doc"}).status_code == 200
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert "## Re-run" in doc(run, "as_is_doc").read_text()
    # A fresh start from step 2 must not undo that update (its last ordinary start predates it).
    assert client.post(f"/api/runs/{run['id']}/restart", json={"stage": "deep_dive", "fresh": True}).status_code == 200
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert "## Re-run" in doc(run, "as_is_doc").read_text()
    assert "## Re-run" not in doc(run, "deep_dive").read_text()
    assert not doc(run, "modernise").exists()


def test_gate_waiting_when_the_run_restarts_asks_again_after_the_rerun(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    gate = attempts(run, "gate_analysis")
    assert len(gate) == 1 and gate[0]["status"] == "awaiting_approval"
    assert gate[0]["id"] > max(s["id"] for s in run["stages"] if s["stage"] != "gate_analysis")  # not superseded

    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert attempts(run, "gate_analysis")[-1]["status"] == "approved"


def test_restart_moves_the_run_to_the_stage_at_once(client):
    run = completed_run(client)
    r = client.post(f"/api/runs/{run['id']}/restart", json={"stage": "deep_dive"})
    assert r.status_code == 200 and r.json()["current_stage"] == "deep_dive"
