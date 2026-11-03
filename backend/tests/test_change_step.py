"""Change this step: an approved documents step (Analysis, Domain dependencies, Modulith design) makes one requested
change and the run fixes forward from it. The already-run steps a person ticks update in place, the others are kept
(a gate reviewing only kept steps keeps its approval). Asked for while the run works, the change waits for the working
step to stop at its next turn."""

import itertools
import threading

from sqlalchemy import select

from app.agents import common, stub
from app.db.models import AuditEvent, Run
from app.db.session import session_scope
from tests.conftest import approve, start_run, wait_for
from tests.test_auth import make_user, signed_in

_ids = itertools.count()


def _lean(client, operation=None):
    r = start_run(client, {"domain": "loan", "operation": operation or f"chg{next(_ids)}", "workflow": "lean"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _attempts(run, key):
    return [s for s in run["stages"] if s["stage"] == key]


def _executions(run_id):
    from app.db.models import StageExecution
    with session_scope() as s:
        rows = list(s.scalars(select(StageExecution).where(StageExecution.run_id == run_id)))
        s.expunge_all()
        return rows


def _stage_defs(run_id):
    with session_scope() as s:
        return s.get(Run, run_id).stage_defs


def _log(run_id):
    with session_scope() as s:
        return [e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id))]


def test_changing_analysis_at_the_design_gate_fixes_forward_and_asks_again(client):
    run_id = _lean(client)
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    assert set(run["changeable"]) == {"analysis", "dependency_analysis", "modulith_design"}
    r = client.post(f"/api/runs/{run_id}/change", json={"stage": "analysis", "notes": "The approval flow skips BR-002",
                                                         "follow": ["dependency_analysis", "modulith_design"]})
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"  # re-approved first
    assert _attempts(run, "analysis")[-1]["feedback"] == "The approval flow skips BR-002"
    assert len(_attempts(run, "dependency_analysis")) == 2  # as-is.md changed: it follows
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    design = _attempts(run, "modulith_design")
    assert len(design) == 2 and "Analysis was changed at the reviewer's request" in design[-1]["feedback"]


def test_only_the_ticked_steps_update_and_a_gate_of_kept_steps_keeps_its_approval(client):
    run_id = _lean(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    while run["status"] == "awaiting_approval":
        approve(client, run_id)
        run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    before = {s["stage"]: len(_attempts(run, s["stage"])) for s in run["stages"]}
    r = client.post(f"/api/runs/{run_id}/change", json={
        "stage": "modulith_design", "notes": "The orchestrator retries the KYC call once",
        "follow": ["orchestrator_service", "orchestrator_tests"]})
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_orchestrator"  # the domain gate kept its approval
    for kept in ("modular_monolith", "monolith_orchestrator", "microservice_scaffold", "independent_tests"):
        assert len(_attempts(run, kept)) == before[kept], kept  # no new attempt
    assert len(_attempts(run, "orchestrator_service")) == before["orchestrator_service"] + 1
    domain_gate = _attempts(run, "gate_tests")[-1]
    assert domain_gate["status"] == "approved" and "approved earlier by" in domain_gate["feedback"]
    from app import workspace  # kept steps' work is still in effect: their documents count, nothing was undone
    in_effect = {e.stage for e in workspace._effective(_executions(run_id), _stage_defs(run_id), None)}
    assert {"modular_monolith", "microservice_scaffold", "independent_tests"} <= in_effect
    approve(client, run_id)
    done = wait_for(client, run_id, {"completed"})  # the release gate's steps were kept too: approval kept
    assert _attempts(done, "gate_release")[-1]["feedback"].startswith("kept:")
    assert len(_attempts(done, "modernise_and_test")) == before["modernise_and_test"]


def test_a_change_asked_while_a_step_works_waits_for_it_to_stop(client, monkeypatch):
    original, started, first = stub.StubAgent.run, threading.Event(), {"modular_monolith": True}

    def run(self, ctx):
        if first.get(self.stage):  # works until it is asked to stop, like an AI agent between turns
            first[self.stage] = False
            started.set()
            for _ in range(400):
                common.raise_if_stopped(ctx)
                threading.Event().wait(0.05)
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run_id = _lean(client)
    for _ in range(2):  # analysis, then design
        wait_for(client, run_id, {"awaiting_approval"})
        approve(client, run_id)
    assert started.wait(15)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"
    r = client.post(f"/api/runs/{run_id}/change", json={"stage": "analysis", "notes": "Missed the refund flow",
                                                         "follow": ["dependency_analysis", "modulith_design"]})
    assert r.status_code == 200, r.text
    again = client.post(f"/api/runs/{run_id}/change", json={"stage": "analysis", "notes": "x"})
    assert again.status_code == 409 or again.json().get("pending_change") is None  # one change at a time
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis" and run["pending_change"] is None
    assert _attempts(run, "analysis")[-1]["feedback"] == "Missed the refund flow"
    stopped = _attempts(run, "modular_monolith")[-1]
    assert stopped["status"] == "failed" and "asked to change Analysis" in stopped["error"]
    assert any("asked to change Analysis: the working step stops" in m for m in _log(run_id))
    with session_scope() as s:
        assert s.get(Run, run_id).pending_change is None
    # it goes on, and the stopped step runs again from scratch
    for _ in range(2):
        approve(client, run_id)
        run = wait_for(client, run_id, {"awaiting_approval"})
    assert _attempts(run, "modular_monolith")[-1]["status"] == "completed"


def test_what_can_be_changed_and_by_whom(client):
    run_id = _lean(client)
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    wait_for(client, run_id, {"awaiting_approval"})
    post = lambda c, body: c.post(f"/api/runs/{run_id}/change", json=body)  # noqa: E731
    r = post(client, {"stage": "modular_monolith", "notes": "x"})
    assert r.status_code == 422 and "Re-run from here" in r.json()["detail"]  # not run yet / a code step
    assert post(client, {"stage": "analysis", "notes": ""}).status_code == 422
    r = post(client, {"stage": "analysis", "notes": "x", "follow": ["modular_monolith"]})
    assert r.status_code == 422 and "already ran" in r.json()["detail"]
    viewer = make_user(client, ["viewer"])
    assert post(signed_in(viewer["username"]), {"stage": "analysis", "notes": "x"}).status_code == 403
    reviewer = make_user(client, ["reviewer"])
    r = post(signed_in(reviewer["username"]), {"stage": "modulith_design", "notes": "Name the port KycCheck"})
    assert r.status_code == 200, r.text  # a reviewer may change what they approve
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"


def test_a_step_whose_work_was_undone_runs_again_even_when_not_ticked(client, monkeypatch):
    run_id = _lean(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    while run["status"] == "awaiting_approval":
        approve(client, run_id)
        run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    original, fail = stub.StubAgent.run, {"microservice_scaffold": True}

    def run_(self, ctx):
        if fail.get(self.stage):
            fail[self.stage] = False
            raise RuntimeError("Build broke")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run_)
    r = client.post(f"/api/runs/{run_id}/restart", json={"stage": "modular_monolith", "fresh": True})
    assert r.status_code == 200, r.text
    wait_for(client, run_id, {"awaiting_approval", "failed"})
    run = client.get(f"/api/runs/{run_id}").json()
    while run["status"] == "awaiting_approval":  # e.g. a gate on the way
        approve(client, run_id)
        run = wait_for(client, run_id, {"awaiting_approval", "failed"})
    assert run["status"] == "failed"  # Spring Boot microservice failed starting over: the steps after it are undone
    before = {s["stage"]: len(_attempts(run, s["stage"])) for s in run["stages"]}
    r = client.post(f"/api/runs/{run_id}/change", json={"stage": "modulith_design", "notes": "Name the port KycCheck",
                                                         "follow": []})  # nothing ticked
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)  # the design, approved again
    run = wait_for(client, run_id, {"awaiting_approval", "completed"})
    assert len(_attempts(run, "modular_monolith")) == before["modular_monolith"]  # in effect: kept
    assert len(_attempts(run, "microservice_scaffold")) == before["microservice_scaffold"] + 1  # undone: runs again
    assert _attempts(run, "microservice_scaffold")[-1]["status"] == "completed"
    assert any("run again too: their earlier work is no longer in effect" in m for m in _log(run_id))
