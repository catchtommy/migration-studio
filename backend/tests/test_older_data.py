"""Runs recorded by earlier releases lack what later ones added (their columns are empty): the pages and the actions
on them still work. Tests otherwise only see rows the current code writes, which is how a reset that ignored runs
without a scope went unnoticed."""

import itertools

from sqlalchemy import update

from app.db.models import Run, StageExecution
from app.db.session import session_scope
from tests.conftest import start_run, wait_for

_ids = itertools.count()

# What a run of an early release has empty: columns added later (see app/db/migrations/versions)
OLDER_RUN = {"scope": None, "workflow": None, "workflow_stages": None, "repository": None, "base_branch": None,
             "repositories": None, "legacy": None, "delivery": None, "agents_kept_at": None, "pending_change": None}
OLDER_ATTEMPT = {"rerun": None, "checklist": None, "usage": None, "inputs": None, "prompt": None, "repos": None}


def older_run(client) -> tuple[str, str]:
    """A migration waiting at its first gate, then made to look as an early release recorded it."""
    operation = f"older{next(_ids)}"
    run_id = start_run(client, {"domain": "customer", "operation": operation}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    with session_scope() as s:
        s.execute(update(Run).where(Run.id == run_id).values(**OLDER_RUN))
        s.execute(update(StageExecution).where(StageExecution.run_id == run_id).values(**OLDER_ATTEMPT))
    return run_id, operation


def test_the_pages_show_an_older_run(client):
    run_id, operation = older_run(client)

    listed = next(r for r in client.get("/api/runs").json() if r["id"] == run_id)  # the dashboard
    assert listed["scope"] == "operation" and listed["workflow_stages"]  # the standard workflow it ran
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "awaiting_approval" and run["pending_gate"]["gate"] == "gate_analysis"
    assert run["stages"] and run["changeable"] is not None
    feature = next(f for f in client.get("/api/domains/customer/features").json() if f["key"] == operation)
    assert feature["migration"]["id"] == run_id  # the feature shows as started
    domain = next(d for d in client.get("/api/domains").json() if d["key"] == "customer")
    assert domain["runs"] >= 1
    docs = client.get("/api/domains/customer/docs").json()
    assert any(d["run"]["id"] == run_id for d in docs)
    for path in ("/api/discovery", "/api/workflows", "/api/domains/customer", f"/api/runs/{run_id}/artifacts",
                 f"/api/runs/{run_id}/questions"):
        assert client.get(path).status_code == 200, path


def test_an_older_run_goes_on_after_its_gate(client):
    run_id, _operation = older_run(client)
    assert client.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "failed"}, timeout=60)
    assert run["status"] != "failed", run.get("error")


def test_an_older_runs_feature_can_be_reset_and_started_again(client):
    run_id, operation = older_run(client)
    r = client.post(f"/api/domains/customer/features/{operation}/reset")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    again = client.post("/api/runs", json={"domain": "customer", "operation": operation})
    assert again.status_code == 201, again.text  # no unfinished run is left in the way
