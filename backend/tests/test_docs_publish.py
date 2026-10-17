"""Publishing approved AS-IS documentation into the monolith's docs/<domain>/<feature>/ on its base branch."""

import subprocess
from pathlib import Path

from app import docs_publish, legacy
from app.agents.base_agent import AgentContext
from app.agents.common import documentation_brief
from tests.conftest import wait_for
from tests.test_delivery import git
from tests.test_projects import as_project, make_monolith, new_project


def setup(client, tmp_path):
    work = tmp_path / "work"
    commit = make_monolith(work)
    bare = tmp_path / "monolith.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(work), str(bare)], check=True)
    key = new_project(client, monolith_repo=str(bare))
    proj = as_project(client, key)
    proj.post("/api/domains", json={"key": "loan", "name": "Loan", "description": ""})
    for f in ("approve", "reject"):
        proj.post("/api/domains/loan/features", json={"key": f, "name": f})
    assert client.put("/api/account/git", json={"name": "Ada Admin", "email": "ada@example.com"}).status_code == 200
    return work, commit, bare, key, proj


def to_publish(proj, operation="approve", edit=None):
    run = proj.post("/api/runs", json={"domain": "loan", "operation": operation}).json()
    run = wait_for(proj, run["id"], {"awaiting_approval"}, timeout=60)
    assert run["pending_gate"]["gate"] == "gate_analysis"
    if edit:
        art = next(a for a in proj.get(f"/api/runs/{run['id']}/artifacts").json() if a["stage"] == "as_is_doc")
        assert proj.put(f"/api/artifacts/{art['id']}", json={"content": edit}).status_code == 200
    assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    return wait_for(proj, run["id"], {"awaiting_publish"}, timeout=60)


def test_approved_as_is_documents_are_published_to_the_base_branch(client, tmp_path):
    work, commit, bare, key, proj = setup(client, tmp_path)
    run = to_publish(proj, edit="# Reviewed AS-IS\n")
    files = run["pending_publish"]["files"]
    assert "docs/loan/approve/as_is_doc.md" in files and "docs/loan/approve/deep_dive.md" in files
    assert all(f.startswith("docs/loan/approve/") for f in files)
    assert not any("design" in f or "contracts" in f for f in files)  # only the AS-IS steps' documents

    preview = proj.get(f"/api/runs/{run['id']}/docs").json()
    assert preview["branch"] == "main" and {f["status"] for f in preview["files"]} == {"new"}
    r = proj.post(f"/api/runs/{run['id']}/docs/publish", json={
        "message": "Document loan approve (AS-IS)", "paths": [f["path"] for f in preview["files"]],
        "latest_sha": preview["latest_sha"]})
    assert r.status_code == 200, r.text

    # One commit on main, by the user, with the approved content (the reviewer's edit included).
    assert git(bare, "show", "main:docs/loan/approve/as_is_doc.md") == "# Reviewed AS-IS"
    assert git(bare, "log", "-1", "--format=%an|%s", "main") == "Ada Admin|Document loan approve (AS-IS)"
    assert git(bare, "rev-parse", "main^") == git(work, "rev-parse", "main")
    nxt = wait_for(proj, run["id"], {"awaiting_approval"}, timeout=60)  # the workflow went on
    assert nxt["pending_gate"]["gate"] != "gate_analysis"
    assert docs_publish.publish_needed(run["id"], "gate_analysis") is None  # nothing new to publish

    # The shared read-only copy is untouched until it is refreshed; then later agents are pointed to the docs.
    detail = proj.get(f"/api/runs/{run['id']}").json()
    assert not (legacy.copy_dir(key, detail["legacy"]["sha"]) / "docs").exists()
    fresh = legacy.refresh(key)
    copy = legacy.copy_dir(key, fresh["sha"])
    assert (copy / "docs/loan/approve/as_is_doc.md").read_text() == "# Reviewed AS-IS\n"
    from app.orchestrator.stages import STANDARD
    ctx = AgentContext(run_id="r", domain="loan", operation="approve", stage="as_is_doc", attempt=1,
                       workspace=tmp_path, feedback=None, settings={"monolith_docs_dir": "docs"},
                       log=lambda m: None, legacy_dir=copy, stage_def=STANDARD.by_key["as_is_doc"])
    brief = documentation_brief(ctx)
    assert "docs/loan/approve/" in brief and "update them" in brief


def test_publishing_is_refused_when_the_base_branch_moved_and_can_be_skipped(client, tmp_path):
    work, commit, bare, key, proj = setup(client, tmp_path)
    run = to_publish(proj)
    preview = proj.get(f"/api/runs/{run['id']}/docs").json()
    (work / "Other.java").write_text("moved on")
    commit("add", "-A")
    commit("commit", "-qm", "someone else")
    git(work, "push", "--quiet", str(bare), "main")
    r = proj.post(f"/api/runs/{run['id']}/docs/publish", json={
        "message": "docs", "paths": [f["path"] for f in preview["files"]], "latest_sha": preview["latest_sha"]})
    assert r.status_code == 409 and "new commits" in r.json()["detail"]
    before = git(bare, "rev-parse", "main")
    assert proj.post(f"/api/runs/{run['id']}/docs/skip").status_code == 204
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=60)
    assert git(bare, "rev-parse", "main") == before  # nothing committed


def test_steps_not_marked_to_publish_dont_pause(client, tmp_path):
    work, commit, bare, key, proj = setup(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    assert {st["key"] for st in wf["steps"] if st.get("publish_docs")} == {"as_is_doc", "deep_dive"}
    steps = [{**st, "publish_docs": False} for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=60)
    proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"})
    nxt = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=60)
    assert nxt["status"] == "awaiting_approval" and nxt["pending_gate"]["gate"] != "gate_analysis"


def test_the_documentation_folder_is_a_safe_relative_path(client):
    key = new_project(client)
    for bad in ("../outside", "/abs", "C:docs", "-x"):
        assert client.put(f"/api/projects/{key}/settings", json={"monolith_docs_dir": bad}).status_code == 422, bad
    assert client.put(f"/api/projects/{key}/settings", json={"monolith_docs_dir": "documentation/as-is"}).status_code == 200
