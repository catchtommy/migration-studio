"""A failed step keeps what it did, and a retry continues from there instead of paying for the same work again; a
retry from scratch, or a re-run of the workflow, starts it over."""

import itertools
from pathlib import Path

from app.agents import stub
from app.agents.common import brief
from app.tools import git_ops
from tests.conftest import start_run, wait_for
from tests.test_delivery import setup_project

_ids = itertools.count()


def _failing_once(monkeypatch, stage: str, seen: list):
    """The stub agent of `stage` writes a document and some code, then fails, the first time; every time, the context
    it got and the documents it found in its folder are recorded in `seen`."""
    original = stub.StubAgent.run

    def run(self, ctx):
        if self.stage != stage:
            return original(self, ctx)
        found = {f.name: f.read_text() for f in ctx.output_dir.glob("*") if f.is_file()} if ctx.output_dir.is_dir() else {}
        seen.append((ctx, found))
        if len(seen) == 1:
            (ctx.output_dir / "half-done.md").write_text("# The first half\n")
            if ctx.stage_def.code:
                (ctx.workspace / "Half.java").write_text("class Half {}\n")
                git_ops.commit_all(ctx.workspace, "step 1: half")  # an agent commits each step
                (ctx.workspace / "Uncommitted.java").write_text("class Uncommitted {}\n")
            raise RuntimeError("Claude API overloaded")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)


def _until_failed(proj, run_id) -> dict:
    while True:
        run = wait_for(proj, run_id, {"awaiting_branches", "awaiting_commit", "failed", "awaiting_approval"}, timeout=30)
        if run["status"] == "awaiting_commit" and run["current_stage"]:
            assert proj.post(f"/api/runs/{run_id}/finish").status_code == 204  # go on without committing
            continue
        if run["status"] != "awaiting_branches":
            return run
        body = {"feature_branch": run["pending_branches"]["feature_branch"]}
        assert proj.post(f"/api/runs/{run_id}/branches", json=body).status_code == 200


def test_a_retry_continues_from_the_documents_and_code_the_failed_attempt_kept(client, tmp_path, monkeypatch):
    seen: list = []
    _failing_once(monkeypatch, "as_is_doc", seen)
    _upstream, _key, proj = setup_project(client, tmp_path)  # its first step, AS-IS, changes the monolith's code
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = _until_failed(proj, run_id)
    assert run["status"] == "failed" and "overloaded" in run["error"]
    ws = Path(run["workspace"])
    failed = run["stages"][-1]
    assert failed["status"] == "failed" and failed["end_sha"]  # its work is kept, committed on the branch
    assert (ws / "Half.java").exists() and (ws / "Uncommitted.java").exists()
    assert git_ops.git(ws, "status", "--porcelain") == ""

    assert proj.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = _until_failed(proj, run_id)
    assert run["status"] == "awaiting_approval"
    ctx, found = seen[1]
    assert found["half-done.md"] == "# The first half\n"  # put back where the agent writes
    assert ctx.resume["attempt"] == 1 and ctx.resume["code_kept"] and "overloaded" in ctx.resume["error"]
    assert {"Half.java", "Uncommitted.java"} <= set(ctx.resume["files"])
    assert any(p.endswith("half-done.md") for p in ctx.resume["documents"])
    assert ctx.base_sha == failed["start_sha"]  # the checks count the kept commits as this step's
    assert "Continue where the last attempt stopped" in brief(ctx)

    done = [e for e in run["stages"] if e["stage"] == "as_is_doc"]
    assert [e["status"] for e in done] == ["failed", "completed"]
    assert done[1]["start_sha"] == failed["start_sha"]  # one step, from where it first started
    docs = {a["name"] for a in proj.get(f"/api/runs/{run_id}/artifacts").json()
            if a["stage"] == "as_is_doc" and a["attempt"] == 2}
    assert "half-done.md" in docs  # what the first attempt wrote is part of the step's documents


def test_a_retry_from_scratch_starts_the_step_over(client, tmp_path, monkeypatch):
    seen: list = []
    _failing_once(monkeypatch, "as_is_doc", seen)
    _upstream, _key, proj = setup_project(client, tmp_path)
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = _until_failed(proj, run_id)
    ws, start = Path(run["workspace"]), run["stages"][-1]["start_sha"]

    assert proj.post(f"/api/runs/{run_id}/retry", json={"fresh": True}).status_code == 200
    run = _until_failed(proj, run_id)
    assert run["status"] == "awaiting_approval"
    ctx, found = seen[1]
    assert ctx.resume is None and "half-done.md" not in found
    assert not (ws / "Half.java").exists() and git_ops.is_ancestor(ws, start, "HEAD")


def test_a_documents_only_step_continues_from_its_documents(client, monkeypatch):
    seen: list = []
    _failing_once(monkeypatch, "deep_dive", seen)
    r = start_run(client, {"domain": "customer", "operation": f"resume{next(_ids)}"})
    run_id = r.json()["id"]
    assert wait_for(client, run_id, {"failed"})["stages"][-1]["end_sha"]
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    ctx, found = seen[1]
    assert ctx.resume["documents"] and found["half-done.md"] == "# The first half\n"
    assert "redo them" not in brief(ctx)  # no code to redo
    assert [e["status"] for e in run["stages"] if e["stage"] == "deep_dive"] == ["failed", "completed"]


def test_a_re_run_of_the_workflow_does_not_continue_a_stopped_attempt(client, monkeypatch):
    seen: list = []
    _failing_once(monkeypatch, "deep_dive", seen)
    run_id = start_run(client, {"domain": "customer", "operation": f"resume{next(_ids)}"}).json()["id"]
    wait_for(client, run_id, {"failed"})
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 200
    wait_for(client, run_id, {"awaiting_approval"})
    ctx, found = seen[1]
    assert ctx.resume is None and "half-done.md" not in found  # the steps start over; nothing stopped is picked up


def test_a_retry_keeps_the_code_when_a_commit_came_on_top_of_it(client, tmp_path, monkeypatch):
    """Confirming the branches before the retry brings in the latest of the feature branch from git (the step's work
    committed and pushed through the review, say): the stopped attempt's commits are still there, under it, so the
    retry continues on them instead of telling the agent its code is gone."""
    seen: list = []
    _failing_once(monkeypatch, "as_is_doc", seen)
    _upstream, _key, proj = setup_project(client, tmp_path)
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = _until_failed(proj, run_id)
    ws, failed = Path(run["workspace"]), run["stages"][-1]
    (ws / "FromGit.java").write_text("class FromGit {}\n")  # on top of the stopped attempt's commits
    git_ops.commit_all(ws, "Merge the latest of the feature branch")

    assert proj.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = _until_failed(proj, run_id)
    assert run["status"] == "awaiting_approval"
    ctx, _found = seen[1]
    assert ctx.resume["code_kept"], "the retry thought the stopped attempt's code was gone"
    assert {"Half.java", "Uncommitted.java"} <= set(ctx.resume["files"])
    assert ctx.base_sha == failed["start_sha"]  # the checks still count from where the step first started
    assert "redo them" not in brief(ctx)
