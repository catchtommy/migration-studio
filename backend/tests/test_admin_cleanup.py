"""What only an admin can do, because it can't be undone: delete a project with its runs, and start a feature over
with another workflow (its runs, in progress or not, are deleted with everything recorded for them)."""

import itertools
import time
from pathlib import Path

from sqlalchemy import func, select

from app.agents import common, stub
from app.db.models import Artifact, AuditEvent, Run, StageExecution
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_auth import make_user, signed_in
from tests.test_projects import as_project, new_project

_ids = itertools.count()


def _rows(model, run_id: str) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.run_id == run_id))


def test_only_an_admin_can_delete_a_project_or_start_a_feature_over(client):
    architect = signed_in(make_user(client, ["architect"])["username"])
    run_id = start_run(client, {"domain": "customer", "operation": f"admin{next(_ids)}"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    r = architect.post(f"/api/runs/{run_id}/start-over", json={"workflow": "lean"})
    assert r.status_code == 403 and "Only an admin" in r.json()["detail"]
    key = new_project(client)
    r = architect.delete(f"/api/projects/{key}")
    assert r.status_code == 403 and "Only an admin" in r.json()["detail"]
    assert client.delete(f"/api/projects/{key}").status_code == 204  # the admin can


def test_an_admin_deletes_a_project_with_its_runs_and_everything_recorded(client):
    key = new_project(client)
    proj = as_project(client, key)
    assert proj.post("/api/domains", json={"key": "loan", "name": "Loan", "description": ""}).status_code == 201
    run_id = start_run(proj, {"domain": "loan", "operation": "approve"}).json()["id"]
    run = wait_for(proj, run_id, {"awaiting_approval"})
    ws = Path(run["workspace"])
    assert ws.exists() and _rows(Artifact, run_id) and _rows(StageExecution, run_id)

    assert client.delete(f"/api/projects/{key}").status_code == 204
    assert key not in {p["key"] for p in client.get("/api/projects").json()}
    with session_scope() as s:
        assert s.get(Run, run_id) is None
    for model in (Artifact, StageExecution, AuditEvent):
        assert _rows(model, run_id) == 0
    assert not ws.exists()


def test_an_admin_starts_a_waiting_feature_over_with_another_workflow(client):
    operation = f"over{next(_ids)}"
    run_id = start_run(client, {"domain": "customer", "operation": operation}).json()["id"]
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["workflow"] == "standard" and run["pending_gate"]["gate"] == "gate_analysis"

    r = client.post(f"/api/runs/{run_id}/start-over", json={"workflow": "lean"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != run_id and new["workflow"] == "lean" and new["operation"] == operation
    assert client.get(f"/api/runs/{run_id}").status_code == 404  # the old run is gone, with its data
    assert _rows(Artifact, run_id) == 0 and _rows(StageExecution, run_id) == 0
    run = wait_for(client, new["id"], {"awaiting_approval"})
    assert run["pending_gate"] == {"gate": "gate_analysis", "reviews": ["analysis"], "checklist": []}
    with session_scope() as s:
        logged = [e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == new["id"]))]
    assert any("over with the lean workflow" in m and "1 earlier run(s)" in m for m in logged)

    assert client.post(f"/api/runs/{new['id']}/start-over", json={"workflow": "nope"}).status_code == 422


def test_a_running_feature_is_stopped_before_it_starts_over(client, monkeypatch):
    original = stub.StubAgent.run

    def slow(self, ctx):
        if self.stage == "as_is_doc" and ctx.operation.startswith("busy"):
            while True:  # an agent in the middle of its work, until it is stopped
                common.raise_if_stopped(ctx)
                time.sleep(0.05)
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", slow)
    run_id = start_run(client, {"domain": "customer", "operation": f"busy{next(_ids)}"}).json()["id"]
    deadline = time.time() + 10
    while client.get(f"/api/runs/{run_id}").json()["current_stage"] != "as_is_doc" and time.time() < deadline:
        time.sleep(0.05)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"  # its agent is at work

    r = client.post(f"/api/runs/{run_id}/start-over", json={"workflow": "lean"})
    assert r.status_code == 201, r.text
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert not common.stop_requested(run_id)
    wait_for(client, r.json()["id"], {"awaiting_approval"})


def _migration(client, domain: str, feature: str):
    return next(f for f in client.get(f"/api/domains/{domain}/features").json() if f["key"] == feature)["migration"]


def test_an_admin_resets_a_started_feature_to_not_started(client):
    operation = f"reset{next(_ids)}"
    run_id = start_run(client, {"domain": "customer", "operation": operation}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    assert _migration(client, "customer", operation)["id"] == run_id
    before = len(client.get("/api/runs").json())

    developer = signed_in(make_user(client, ["developer"])["username"])
    r = developer.post(f"/api/domains/customer/features/{operation}/reset")
    assert r.status_code == 403 and "Only an admin" in r.json()["detail"]
    assert client.post("/api/domains/customer/features/nope/reset").status_code == 404

    r = client.post(f"/api/domains/customer/features/{operation}/reset")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert _migration(client, "customer", operation) is None  # not started: offered to migrate again
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert _rows(Artifact, run_id) == 0 and _rows(StageExecution, run_id) == 0
    assert len(client.get("/api/runs").json()) == before - 1  # what the dashboard counts
    assert client.post(f"/api/domains/customer/features/{operation}/reset").json() == {"deleted": 0}


def test_a_running_feature_is_stopped_before_it_is_reset(client, monkeypatch):
    original = stub.StubAgent.run

    def slow(self, ctx):
        if self.stage == "as_is_doc" and ctx.operation.startswith("busyreset"):
            while True:
                common.raise_if_stopped(ctx)
                time.sleep(0.05)
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", slow)
    operation = f"busyreset{next(_ids)}"
    run_id = start_run(client, {"domain": "customer", "operation": operation}).json()["id"]
    deadline = time.time() + 10
    while client.get(f"/api/runs/{run_id}").json()["current_stage"] != "as_is_doc" and time.time() < deadline:
        time.sleep(0.05)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"

    assert client.post(f"/api/domains/customer/features/{operation}/reset").json() == {"deleted": 1}
    assert _migration(client, "customer", operation) is None
