import itertools
from pathlib import Path

from app.agents import stub
from app.tools import git_ops
from tests.conftest import start_run, wait_for

GATES = ["gate_analysis", "gate_design", "gate_tests", "gate_release"]
_ids = itertools.count()


def new_run(client):
    r = start_run(client, {"domain": "customer", "operation": f"op{next(_ids)}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def decide(client, run_id, **body):
    r = client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "tester", **body})
    assert r.status_code == 200, r.text


def executions(run, stage):
    return [s for s in run["stages"] if s["stage"] == stage]


def test_happy_path_runs_every_stage_and_gate(client):
    run_id = new_run(client)
    for gate in GATES:
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"]["gate"] == gate
        decide(client, run_id, decision="approve")
    run = wait_for(client, run_id, {"completed"})

    assert {s["stage"] for s in run["stages"]} == {
        "as_is_doc", "deep_dive", "gate_analysis", "boundary_design", "integration_design", "gate_design",
        "modular_monolith",
        "microservice_scaffold", "independent_tests", "gate_tests", "modernise", "monolith_integration",
        "gate_release"}
    assert all(s["status"] in ("completed", "approved") for s in run["stages"])
    names = [a["name"] for a in client.get(f"/api/runs/{run_id}/artifacts").json()]
    assert len([n for n in names if n.endswith(".md")]) == 9  # one document per agent stage
    assert {"cross-domain.yaml", "integration-decisions.yaml"} <= set(names)
    ws = Path(run["workspace"])
    assert git_ops.git(ws, "rev-parse", "--abbrev-ref", "HEAD") == run["branch"]


def test_reject_rolls_back_and_reruns_target_with_feedback(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="approve")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    design_start = executions(run, "boundary_design")[0]["start_sha"]

    decide(client, run_id, decision="reject", comments="Split payments tables out")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"

    attempts = executions(run, "boundary_design")
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert attempts[1]["start_sha"] == design_start  # workspace was rolled back before the re-run
    assert attempts[1]["feedback"] == "Split payments tables out"
    content = (Path(run["workspace"]) / ".migration/customer" / run["operation"] / "boundary_design.md").read_text()
    assert "Split payments tables out" in content
    assert [g["status"] for g in executions(run, "gate_design")] == ["rejected", "awaiting_approval"]


def test_reject_can_target_an_earlier_reviewed_stage(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="reject", comments="Missing batch job entry point", target_stage="as_is_doc")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert len(executions(run, "as_is_doc")) == 2
    assert len(executions(run, "deep_dive")) == 2  # everything after the target re-runs too


def test_invalid_decisions_are_refused(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    base = {"reviewer": "tester"}
    assert client.post(f"/api/runs/{run_id}/decision", json={**base, "decision": "reject"}).status_code == 422
    r = client.post(f"/api/runs/{run_id}/decision",
                    json={**base, "decision": "reject", "comments": "x", "target_stage": "modernise"})
    assert r.status_code == 422
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 409


def test_failed_stage_can_be_retried(client, monkeypatch):
    original = stub.StubAgent.run
    calls = {"n": 0}

    def flaky(self, ctx):
        if self.stage == "deep_dive" and calls["n"] == 0:
            calls["n"] += 1
            (ctx.output_dir / "partial.txt").write_text("half-written")
            raise RuntimeError("model timed out")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", flaky)
    run_id = new_run(client)
    run = wait_for(client, run_id, {"failed"})
    assert "model timed out" in run["error"]
    failed = executions(run, "deep_dive")[0]
    assert failed["status"] == "failed"
    assert not (Path(run["workspace"]) / ".migration/customer" / run["operation"] / "partial.txt").exists()

    assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert [e["status"] for e in executions(run, "deep_dive")] == ["failed", "completed"]
    assert len(executions(run, "as_is_doc")) == 1  # completed stages are not repeated


def test_manual_artifact_edit_is_committed(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    art = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["stage"] == "deep_dive")
    r = client.put(f"/api/artifacts/{art['id']}", json={"content": "# Edited by reviewer\n"})
    assert r.status_code == 200
    ws = Path(run["workspace"])
    assert (ws / art["path"]).read_text() == "# Edited by reviewer\n"
    assert git_ops.git(ws, "status", "--porcelain") == ""
    assert "[manual edit]" in git_ops.git(ws, "log", "-1", "--format=%s")


def test_duplicate_unfinished_run_is_refused(client):
    r = start_run(client, {"domain": "broker", "operation": "dup"})
    assert r.status_code == 201
    assert start_run(client, {"domain": "broker", "operation": "dup"}).status_code == 409
    wait_for(client, r.json()["id"], {"awaiting_approval"})


def test_rejection_can_be_held_for_amendments_before_the_rerun(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="reject", comments="Cover the batch job too", target_stage="as_is_doc",
           rerun="later")
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "changes_requested"
    assert run["pending_rerun"] == {"gate": "gate_analysis", "target_stage": "as_is_doc",
                                    "comments": "Cover the batch job too"}
    assert run["pending_gate"]["gate"] == "gate_analysis"
    assert len(executions(run, "as_is_doc")) == 1  # nothing re-ran yet
    assert client.post(f"/api/runs/{run_id}/decision",
                       json={"reviewer": "t", "decision": "approve"}).status_code == 409

    # Amend while held: the run's inputs and a document; then re-run with refined comments.
    r = client.put(f"/api/runs/{run_id}/inputs", json={"paths": ["BatchJob.java"], "notes": "Nightly batch"})
    assert r.status_code == 200 and r.json()["inputs"]["paths"] == ["BatchJob.java"]
    doc = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["stage"] == "deep_dive")
    assert client.put(f"/api/artifacts/{doc['id']}", json={"content": "# edited\n"}).status_code == 200
    r = client.post(f"/api/runs/{run_id}/rerun", json={"comments": "Cover BatchJob as well"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/runs/{run_id}/rerun", json={}).status_code == 409  # only once

    run = wait_for(client, run_id, {"awaiting_approval"})
    attempts = executions(run, "as_is_doc")
    assert len(attempts) == 2 and attempts[1]["feedback"] == "Cover BatchJob as well"
    assert run["approvals"][-1]["comments"] == "Cover BatchJob as well" and run["pending_rerun"] is None
    as_is = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json()
                 if a["stage"] == "as_is_doc" and a["attempt"] == 2)
    assert "BatchJob.java" in client.get(f"/api/artifacts/{as_is['id']}").json()["content"]  # new inputs used


def test_held_rejection_can_be_withdrawn(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="reject", comments="Hmm", rerun="later")
    assert client.post(f"/api/runs/{run_id}/back-to-review").status_code == 200
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "awaiting_approval" and run["approvals"] == []
    decide(client, run_id, decision="approve")
    assert wait_for(client, run_id, {"awaiting_approval"})["pending_gate"]["gate"] == "gate_design"
