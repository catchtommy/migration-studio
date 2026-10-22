"""An admin stops a run whose agent is going round in circles; the step fails keeping what it did, and a retry with
notes (or changed inputs) tells the agent what to do differently."""

import itertools
import time

from app.agents import common, stub
from tests.conftest import start_run, wait_for
from tests.test_auth import make_user, signed_in

_ids = itertools.count()


def _looping(monkeypatch, seen: list):
    """The AS-IS agent of a `loop…` feature works until it is stopped, the first time; it records its context."""
    original = stub.StubAgent.run

    def run(self, ctx):
        if self.stage == "as_is_doc" and ctx.operation.startswith("loop"):
            seen.append(ctx)
            if len(seen) == 1:
                (ctx.output_dir / "notes.md").write_text("# Going round in circles\n")
                while True:
                    common.raise_if_stopped(ctx)
                    time.sleep(0.05)
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)


def _working(client, run_id):
    deadline = time.time() + 10
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] == "running" and run["current_stage"] == "as_is_doc":
            return run
        time.sleep(0.05)
    raise AssertionError("the run never started its first step")


def test_an_admin_stops_a_looping_agent_and_retries_with_notes(client, monkeypatch):
    seen: list = []
    _looping(monkeypatch, seen)
    run_id = start_run(client, {"domain": "customer", "operation": f"loop{next(_ids)}"}).json()["id"]
    _working(client, run_id)

    developer = signed_in(make_user(client, ["developer"])["username"])
    r = developer.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 403 and "Only an admin" in r.json()["detail"]

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 202
    run = wait_for(client, run_id, {"failed"})
    assert "Stopped by an admin" in run["error"] and not run["stopping"]
    stopped = [e for e in run["stages"] if e["stage"] == "as_is_doc"][-1]
    assert stopped["status"] == "failed" and stopped["end_sha"]  # what it did is kept for the retry
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 409  # nothing working to stop

    notes = "Only document the create flow; ignore the batch jobs."
    assert client.post(f"/api/runs/{run_id}/retry", json={"notes": notes}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    ctx = seen[1]
    assert ctx.feedback == notes  # the agent gets the notes with the step
    assert ctx.resume and any(p.endswith("notes.md") for p in ctx.resume["documents"])  # and continues from its work
    assert [e["status"] for e in run["stages"] if e["stage"] == "as_is_doc"] == ["failed", "completed"]
    assert len([e for e in run["stages"] if e["stage"] == "deep_dive"]) == 1  # the run went on normally
