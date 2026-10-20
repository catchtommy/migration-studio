import itertools
from pathlib import Path

from app.agents import stub
from app.tools import git_ops
from tests.conftest import document, start_run, wait_for

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
        "as_is_doc", "deep_dive", "gate_analysis", "domain_dependencies", "linked_domain_contracts",
        "domain_design", "orchestrator_design", "integration_design", "gate_design", "modular_monolith",
        "monolith_orchestrator", "microservice_scaffold", "orchestrator_service", "independent_tests", "gate_tests", "modernise", "service_tests", "monolith_integration",
        "gate_release"}
    assert all(s["status"] in ("completed", "approved") for s in run["stages"])
    names = [a["name"] for a in client.get(f"/api/runs/{run_id}/artifacts").json()]
    assert len([n for n in names if n.endswith(".md")]) == 15  # one document per agent stage
    assert {"cross-domain.yaml", "domain-dependencies.yaml", "integration-decisions.yaml"} <= set(names)
    assert not Path(run["workspace"]).exists()  # a completed run's workspace is removed from the server


def test_reject_rolls_back_and_reruns_target_with_feedback(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="approve")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    design_start = executions(run, "domain_dependencies")[0]["start_sha"]  # the gate's first reviewed stage

    decide(client, run_id, decision="reject", comments="Split payments tables out")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"

    attempts = executions(run, "domain_dependencies")
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert [a["attempt"] for a in executions(run, "orchestrator_design")] == [1, 2]  # later steps re-run too
    assert attempts[1]["start_sha"] == design_start  # workspace was rolled back before the re-run
    assert attempts[1]["feedback"] == "Split payments tables out"
    content = document(run, "domain_dependencies").read_text()
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
    assert not document(run, "deep_dive", "partial.txt").exists()

    assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert [e["status"] for e in executions(run, "deep_dive")] == ["failed", "completed"]
    assert len(executions(run, "as_is_doc")) == 1  # completed stages are not repeated


def test_manual_artifact_edit_is_what_later_stages_get(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    art = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["stage"] == "deep_dive")
    r = client.put(f"/api/artifacts/{art['id']}", json={"content": "# Edited by reviewer\n"})
    assert r.status_code == 200
    assert document(run, "deep_dive").read_text() == "# Edited by reviewer\n"
    ws = Path(run["workspace"])
    assert not (ws / ".migration").exists()  # documents live in the database, not on the server's disk
    assert git_ops.git(ws, "status", "--porcelain") == ""


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


def test_run_that_failed_before_its_first_step_can_be_retried(client, monkeypatch):
    original = git_ops.init_workspace
    calls = {"n": 0}

    def checkout_fails_once(path, *args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            path.mkdir(parents=True, exist_ok=True)
            (path / "half-checked-out.txt").write_text("left by the failed checkout")
            raise git_ops.GitError("unable to create file: Filename too long")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(git_ops, "init_workspace", checkout_fails_once)
    run_id = new_run(client)
    run = wait_for(client, run_id, {"failed"})
    assert "Filename too long" in run["error"] and run["stages"] == []

    assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert [e["status"] for e in executions(run, "as_is_doc")] == ["completed"]
    assert not (Path(run["workspace"]) / "half-checked-out.txt").exists()


def test_code_changes_of_a_step_that_writes_documents_only_are_undone(client, monkeypatch):
    original = stub.StubAgent.run

    def sneaky(self, ctx):
        if self.stage == "as_is_doc":  # as if through a command, past the tools' checks
            (ctx.workspace / "src").mkdir(exist_ok=True)
            (ctx.workspace / "src" / "Sneaky.java").write_text("class Sneaky {}")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", sneaky)
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    ws = Path(run["workspace"])
    assert not (ws / "src" / "Sneaky.java").exists()
    assert document(run, "as_is_doc").is_file()  # its documents are kept
    from app.db.models import AuditEvent
    from app.db.session import session_scope
    with session_scope() as s:
        messages = [e.message for e in s.query(AuditEvent).filter(AuditEvent.run_id == run_id)]
    assert any("documents only" in m and "src/Sneaky.java" in m for m in messages)


def test_a_waiting_run_can_be_updated_to_its_workflows_current_steps(client):
    """A run keeps the steps it started with; updating it picks up steps added to its workflow since, after the point
    it has reached, and the run then goes on through them."""
    wf = client.get("/api/workflows/standard").json()
    without = [{**st, "reviews": [r for r in st.get("reviews") or [] if r != "monolith_orchestrator"]}
               if st["kind"] == "gate" else st for st in wf["steps"] if st["key"] != "monolith_orchestrator"]
    assert client.put("/api/workflows/standard?library=project", json={"steps": without}).status_code == 200
    try:
        run_id = new_run(client)
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"]["gate"] == "gate_analysis" and run["workflow_update"] is None
        assert "monolith_orchestrator" not in [s["key"] for s in run["workflow_stages"]]
    finally:
        assert client.post("/api/workflows/standard/revert").status_code == 200  # back to the shared workflow

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["workflow_update"]["added"] == ["Orchestrator in monolith"]
    assert run["workflow_update"]["changed"] == ["Approve tests"] and not run["workflow_update"]["missed"]
    assert "monolith_orchestrator" not in [s["key"] for s in run["workflow_stages"]]  # nothing changes on its own

    r = client.post(f"/api/runs/{run_id}/update-workflow")
    assert r.status_code == 200, r.text
    keys = [s["key"] for s in r.json()["workflow_stages"]]
    assert keys[keys.index("modular_monolith") + 1] == "monolith_orchestrator"
    assert client.get(f"/api/runs/{run_id}").json()["workflow_update"] is None
    assert client.post(f"/api/runs/{run_id}/update-workflow").status_code == 422  # already up to date

    for gate in GATES:
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"]["gate"] == gate
        decide(client, run_id, decision="approve")
    run = wait_for(client, run_id, {"completed"})
    assert [e["status"] for e in executions(run, "monolith_orchestrator")] == ["completed"]
    assert [e["status"] for e in executions(run, "as_is_doc")] == ["completed"]  # reached steps did not run again


def test_a_run_cannot_follow_a_workflow_that_dropped_the_step_it_reached(client):
    from app import run_workflow
    from app.db.models import Run
    from app.db.session import session_scope

    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.workflow_stages = [{**d, "key": "gate_old"} if d["key"] == "gate_analysis" else d for d in run.stage_defs]
        run.current_stage = "gate_old"
        update = run_workflow.plan(run)
    assert "no longer has" in update["blocked"]
    assert client.post(f"/api/runs/{run_id}/update-workflow").status_code == 409


def test_after_a_rerun_from_an_earlier_step_the_steps_after_it_are_upcoming(client):
    """A run re-run from an earlier step goes through the later steps again, so a step the workflow gained among them
    is added by an update, not missed."""
    from app import run_workflow
    from app.db.models import Run
    from app.db.session import session_scope

    run_id = new_run(client)
    for gate in GATES[:2]:  # past the design gate: the later steps ran before
        wait_for(client, run_id, {"awaiting_approval"})
        decide(client, run_id, decision="approve")
    wait_for(client, run_id, {"awaiting_approval"})
    with session_scope() as s:
        run = s.get(Run, run_id)
        keys = [d["key"] for d in run.stage_defs]
        assert keys.index("orchestrator_service") > keys.index("modular_monolith")
        run.workflow_stages = [d for d in run.stage_defs if d["key"] != "orchestrator_service"]
        run.current_stage = "modular_monolith"  # as after a re-run from it
        update = run_workflow.plan(run)
    assert update["added"] == ["Orchestrator microservice"] and not update["missed"]
