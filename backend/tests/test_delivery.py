"""Committing a run's code changes to git: feature branches, the review, conflicts, per-user credentials."""

import os
import subprocess
from pathlib import Path

import pytest

from sqlalchemy import select

from app import git_auth
from app.db.models import GitCredential, Run
from app.db.session import session_scope
from tests.conftest import wait_for
from tests.test_auth import make_user
from tests.test_projects import as_project, make_monolith, new_project, signed_in


def git(repo, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def domain_repo(proj, url, agent, domain="loan", base_branch="", also: dict | None = None) -> str:
    """Set the repository (and base branch) the domain gives an agent in its Agent inputs (and, in the same save, the
    agents in `also`: agent -> url); returns the step's repository value, "" (a step only says it changes code)."""
    inputs = dict(proj.get(f"/api/domains/{domain}").json()["agent_inputs"])
    for key, value, base in ((agent, url, base_branch), *((k, v, "") for k, v in (also or {}).items())):
        inputs[key] = {**(inputs.get(key) or {"paths": [], "notes": ""}), "repository": str(value), "base_branch": base}
    r = proj.put(f"/api/domains/{domain}", json={"agent_inputs": inputs})
    assert r.status_code == 200, r.text
    return ""


def finish_workflow(client, run_id, confirm=None) -> dict:
    """Approve every gate, confirm the proposed branches (or what `confirm(pending)` returns) and go on without
    committing right after code steps, until the workflow is done."""
    while True:
        run = wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "awaiting_publish",
                                        "completed", "failed"}, timeout=60)
        if run["status"] == "awaiting_publish":
            assert client.post(f"/api/runs/{run_id}/docs/skip").status_code == 204  # (tests/test_docs_publish.py)
            continue
        if run["status"] == "awaiting_commit" and run["current_stage"]:
            assert client.post(f"/api/runs/{run_id}/finish").status_code == 204  # go on; offered again at the end
            continue
        if run["status"] == "awaiting_branches":
            pending = run["pending_branches"]
            body = (confirm or (lambda p: {"feature_branch": p["feature_branch"]}))(pending)
            r = client.post(f"/api/runs/{run_id}/branches", json=body)
            assert r.status_code == 200, r.text
            continue
        if run["status"] != "awaiting_approval":
            return run
        assert client.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"}).status_code == 200


def setup_project(client, tmp_path, monolith_step=True):
    """A project with a monolith; its standard workflow's first agent step changes the monolith's code (unless not)."""
    upstream = tmp_path / "monolith"
    make_monolith(upstream)
    key = new_project(client, monolith_repo=str(upstream))
    proj = as_project(client, key)
    proj.post("/api/domains", json={"key": "loan", "name": "Loan", "description": ""})
    proj.post("/api/domains/loan/features", json={"key": "approve", "name": "approve"})
    assert client.put("/api/account/git", json={"name": "Ada Admin", "email": "ada@example.com"}).status_code == 200
    if monolith_step:
        wf = proj.get("/api/workflows/standard").json()
        first = next(st["key"] for st in wf["steps"] if st["kind"] == "agent")
        domain_repo(proj, upstream, first)  # the monolith, set on the domain for the step's agent
        steps = [{**st, "code": {"repository": ""}} if st.get("key") == first else st for st in wf["steps"]]
        assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    return upstream, key, proj


def test_a_run_cuts_its_feature_branch_and_the_review_commits_and_pushes_it(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    plan = proj.get("/api/runs-git-plan", params={"domain": "loan", "feature": "approve"}).json()
    assert plan["repositories"][0]["url"] == str(upstream) and plan["feature_branch"] == "migration/loan/approve"
    assert plan["commit"] is True

    bad = proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "feature_branch": "main"})
    assert bad.status_code == 422  # the base branch itself
    assert proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "base_branch": "nope"}).status_code == 422
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "base_branch": "main",
                                       "feature_branch": "feature/loan-approve"}).json()
    assert run["branch"] == "feature/loan-approve" and run["base_branch"] == "main"
    run = finish_workflow(proj, run["id"])
    assert run["status"] == "awaiting_commit"

    # An agent changed code; meanwhile the base branch moved: one file both changed, one only upstream.
    ws = Path(proj.get(f"/api/runs/{run['id']}").json()["workspace"])
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == "feature/loan-approve"
    (ws / "App.java").write_text("main\nloan approval service\n")
    (ws / "LoanService.java").write_text("class LoanService {}\n")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "agent work")
    (upstream / "App.java").write_text("main\nhotfix on main\n")
    (upstream / "Other.java").write_text("other\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "hotfix")

    assert [r["key"] for r in proj.get(f"/api/runs/{run['id']}/repositories").json()] == ["monolith"]
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    assert changes["behind"] == 1 and changes["conflicts"] == 1
    files = {f["path"]: f for f in changes["files"]}
    assert files["App.java"]["conflict"] == "content" and files["LoanService.java"]["status"] == "A"
    assert not [f for f in changes["files"] if f["document"]]  # documents stay in the studio's database
    detail = proj.get(f"/api/runs/{run['id']}/changes/file",
                      params={"path": "App.java", "latest": changes["latest_sha"]}).json()
    assert "<<<<<<< this run" in detail["merged"] and detail["theirs"] == "main\nhotfix on main\n"

    code_files = [f["path"] for f in changes["files"] if not f["document"]]
    assert {"App.java", "LoanService.java"} <= set(code_files)
    body = {"message": "Migrate loan approve", "branch": "feature/loan-approve",
            "files": code_files, "latest_sha": changes["latest_sha"],
            "head_sha": changes["head_sha"]}
    r = proj.post(f"/api/runs/{run['id']}/commit", json=body)
    assert r.status_code == 409 and "Resolve the conflict in App.java" in r.json()["detail"]
    marked = {"App.java": {"content": detail["merged"]}}
    assert "conflict markers" in proj.post(f"/api/runs/{run['id']}/commit",
                                           json={**body, "resolutions": marked}).json()["detail"]
    resolved = {"App.java": {"content": "main\nhotfix on main\nloan approval service\n"}}
    r = proj.post(f"/api/runs/{run['id']}/commit", json={**body, "resolutions": resolved})
    assert r.status_code == 200, r.text
    pushed = r.json()
    assert pushed["status"] == "pushed" and pushed["branch"] == "feature/loan-approve"

    # The remote branch: one commit on top of the latest main, by the user, with only the chosen files.
    branch = "feature/loan-approve"
    assert git(upstream, "show", f"{branch}:App.java") == "main\nhotfix on main\nloan approval service"
    assert git(upstream, "show", f"{branch}:Other.java") == "other"  # main's latest is in
    assert git(upstream, "rev-parse", f"{branch}^") == git(upstream, "rev-parse", "main")
    assert git(upstream, "log", "-1", "--format=%an <%ae>", branch) == "Ada Admin <ada@example.com>"
    assert not any(p.startswith(".migration") for p in git(upstream, "ls-tree", "-r", "--name-only", branch).split())
    done = proj.get(f"/api/runs/{run['id']}").json()
    assert done["status"] == "completed" and done["delivery"]["repos"]["monolith"]["sha"] == pushed["sha"]

    # A new run may start on a branch the repository already has: its code step says so, and continues on it.
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    r = proj.post("/api/runs", json={"domain": "loan", "operation": "reject", "feature_branch": branch})
    assert r.status_code == 201, r.text
    again = wait_for(proj, r.json()["id"], {"awaiting_branches"}, timeout=30)
    assert "already exists" in again["pending_branches"]["existing_branch"]["message"]


def test_restarting_after_a_commit_adds_a_commit_on_top(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = finish_workflow(proj, run["id"])
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    paths = [f["path"] for f in changes["files"]]
    body = {"message": "first", "branch": run["branch"], "files": paths, "latest_sha": changes["latest_sha"],
            "head_sha": changes["head_sha"]}
    first = proj.post(f"/api/runs/{run['id']}/commit", json=body).json()["sha"]

    first_agent = run["workflow_stages"][0]["key"]
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first_agent}).status_code == 200
    run = finish_workflow(proj, run["id"])
    assert run["status"] == "awaiting_commit"
    ws = Path(proj.get(f"/api/runs/{run['id']}").json()["workspace"])
    (ws / "Extra.java").write_text("extra\n")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "more")
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    assert changes["previous"]["sha"] == first
    r = proj.post(f"/api/runs/{run['id']}/commit", json={**body, "message": "second", "files": [f["path"] for f in changes["files"]],
                                                        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "rev-parse", f"{run['branch']}^") == first  # no force push: it builds on the first commit
    assert git(upstream, "show", f"{run['branch']}:Extra.java") == "extra"


def test_finishing_without_committing_and_who_may_commit(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    run = finish_workflow(proj, proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"])
    viewer = make_user(client, ["viewer"])
    client.put(f"/api/projects/{key}/members/{viewer['id']}", json={"role": "developer"})
    dev = signed_in(viewer["username"], key)
    assert dev.get(f"/api/runs/{run['id']}/changes").status_code == 200  # members can look
    assert dev.post(f"/api/runs/{run['id']}/finish").status_code == 403  # committing needs Approve workflow runs
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204
    done = proj.get(f"/api/runs/{run['id']}").json()
    assert done["status"] == "completed" and done["delivery"]["repos"]["monolith"]["status"] == "skipped"
    assert "migration/loan/approve" not in git(upstream, "branch", "--list")


def test_each_code_step_works_in_its_own_repository_on_the_same_feature_branch(client, tmp_path):
    """The monolith step changes the monolith; the service step changes the domain's own repository ({domain}); the
    orchestrator step another repository; each is reviewed and pushed on its own."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    for name in ("loan-service", "orchestrator"):
        make_monolith(tmp_path / name)
    wf = proj.get("/api/workflows").json()[0]
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    code = {agents[0]: {"repository": domain_repo(proj, upstream, agents[0])},  # the monolith
            agents[1]: {"repository": domain_repo(proj, tmp_path / "loan-service", agents[1], base_branch="release/2.4")},
            agents[2]: {"repository": domain_repo(proj, tmp_path / "orchestrator", agents[2])}}
    steps = [{**st, "code": code.get(st.get("key"))} if st["kind"] == "agent" else st for st in wf["steps"]]
    r = proj.put(f"/api/workflows/{wf['key']}?library=project",
                 json={"steps": steps, "git": {"commit": True, "branch_pattern": "mig/{domain}-{feature}"}})
    assert r.status_code == 200, r.text
    plan = proj.get("/api/runs-git-plan", params={"domain": "loan", "feature": "approve", "workflow": wf["key"]}).json()
    assert plan["feature_branch"] == "mig/loan-approve"
    assert [(x["key"], x["base_branch"]) for x in plan["repositories"]] == [
        ("monolith", ""), ("loan-service", "release/2.4"), ("orchestrator", "")]

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "workflow": wf["key"]}).json()
    # The run stops before the first step that changes code, to confirm the branches.
    first = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert first["current_stage"] == agents[0] and first["pending_branches"]["repository"]["key"] == "monolith"
    bad = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": "main"})
    assert bad.status_code == 422
    asked = []

    def confirm(pending):
        asked.append(pending["repository"]["key"])
        if pending["repository"]["key"] == "monolith":
            return {"feature_branch": "mig/renamed"}  # renamed before any code changes
        assert pending["feature_branch"] == "mig/renamed" and not pending["repository"]["checked_out"]
        return {"feature_branch": "mig/renamed", "base_branch": "release/2.4"}

    run = finish_workflow(proj, run["id"], confirm)
    assert asked == ["monolith", "loan-service", "orchestrator"]  # once per repository
    assert run["status"] == "awaiting_commit" and run["branch"] == "mig/renamed"
    detail = proj.get(f"/api/runs/{run['id']}").json()
    ws = Path(detail["workspace"])
    service = ws / ".studio/repos/loan-service"
    assert (service / "App.java").read_text() == "release"  # cut from release/2.4
    assert git(service, "rev-parse", "--abbrev-ref", "HEAD") == "mig/renamed"
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == "mig/renamed"
    assert (service / "studio-stub" / f"{agents[1]}.txt").exists()  # the service step changed the service
    assert (ws / "studio-stub" / f"{agents[0]}.txt").exists()  # the monolith step the monolith
    assert not (ws / "studio-stub" / f"{agents[1]}.txt").exists()
    assert ".studio" not in git(ws, "ls-files")  # other repositories stay out of the monolith's

    repos = {x["key"]: x for x in proj.get(f"/api/runs/{run['id']}/repositories").json()}
    assert set(repos) == {"monolith", "loan-service", "orchestrator"} and all(x["changed"] for x in repos.values())
    for name, remote in (("loan-service", tmp_path / "loan-service"), ("monolith", upstream)):
        changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": name}).json()
        files = [f["path"] for f in changes["files"] if not f["document"]]
        r = proj.post(f"/api/runs/{run['id']}/commit", json={
            "repo": name, "message": f"Migrate {name}", "branch": "mig/renamed", "files": files,
            "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
        assert r.status_code == 200, r.text
        assert git(remote, "show", f"mig/renamed:studio-stub/{agents[1 if name == 'loan-service' else 0]}.txt")
    assert proj.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_commit"  # the orchestrator is left
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204
    done = proj.get(f"/api/runs/{run['id']}").json()
    assert done["status"] == "completed"
    assert {k: v["status"] for k, v in done["delivery"]["repos"].items()} == {
        "loan-service": "pushed", "monolith": "pushed", "orchestrator": "skipped"}

    # The finished run's workspace is gone from the server; re-running a step checks every repository out again:
    # pushed ones from their feature branch, the one left out from the database.
    assert not ws.exists()
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": agents[1]}).status_code == 200
    run = finish_workflow(proj, run["id"])
    assert (service / "studio-stub" / f"{agents[1]}.txt").exists() and (ws / "studio-stub").exists()


def test_workflow_git_settings_are_validated(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows").json()[0]
    assert proj.put(f"/api/workflows/{wf['key']}?library=project", json={"git": {"branch_pattern": "x/{oops}"}}).status_code == 422
    off = proj.put(f"/api/workflows/{wf['key']}?library=project", json={"git": {"commit": False}}).json()
    assert off["git"]["commit"] is False
    agent = next(st for st in wf["steps"] if st["kind"] == "agent")
    for bad in ({"repository": "x/{nope}"}, {"repository": "--upload-pack=x"}, {"repository": "r", "base_branch": "a..b"}):
        steps = [{**st, "code": bad} if st is agent else st for st in wf["steps"]]
        assert proj.put(f"/api/workflows/{wf['key']}?library=project", json={"steps": steps}).status_code == 422, bad


def test_git_tokens_are_per_user_encrypted_and_reach_git_through_askpass(client, monkeypatch):
    r = client.put("/api/account/git/credentials", json={"host": "https://GitHub.com/acme/repo.git",
                                                         "username": "ada", "token": "ghp_secret"})
    assert r.status_code == 200
    out = r.json()
    assert out["credentials"][0]["host"] == "github.com" and "token" not in out["credentials"][0]
    with session_scope() as s:
        row = s.scalar(select(GitCredential).where(GitCredential.host == "github.com"))
        assert "ghp_secret" not in row.token
        user_id = row.user_id
    env = git_auth.env_for(user_id, "https://github.com/acme/repo.git")
    last = int(env["GIT_CONFIG_COUNT"]) - 1
    assert env[f"GIT_CONFIG_KEY_{last}"] == "credential.helper" and env[f"GIT_CONFIG_VALUE_{last}"] == ""  # no other

    # The server's own config entries (core.longpaths on Windows) are kept, not overwritten
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.longpaths")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    merged = {**os.environ, **git_auth.env_for(user_id, "https://github.com/acme/repo.git")}
    assert merged["GIT_CONFIG_COUNT"] == "2"
    assert (merged["GIT_CONFIG_KEY_0"], merged["GIT_CONFIG_VALUE_0"]) == ("core.longpaths", "true")
    assert (merged["GIT_CONFIG_KEY_1"], merged["GIT_CONFIG_VALUE_1"]) == ("credential.helper", "")
    proc = subprocess.run(["git", "config", "--get", "core.longpaths"], env=merged, capture_output=True, text=True)
    assert proc.stdout.strip() == "true"
    script = env["GIT_ASKPASS"]
    ask = lambda prompt: subprocess.run([script, prompt], env=env, capture_output=True, text=True).stdout  # noqa: E731
    assert ask("Username for 'https://github.com': ") == "ada"
    assert ask("Password for 'https://ada@github.com': ") == "ghp_secret"
    assert git_auth.env_for(user_id, "git@github.com:acme/repo.git") is None  # SSH: the server's keys
    assert git_auth.env_for(user_id, "https://gitlab.com/acme/repo.git") is None  # no token for that host
    assert client.delete(f"/api/account/git/credentials/{out['credentials'][0]['id']}").json()["credentials"] == []


def test_restarting_an_older_run_picks_up_the_steps_code_settings(client, tmp_path):
    """A run started before its workflow's steps changed code: restarting a step brings in the current settings, and
    the step asks for its branches before changing another repository."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    make_monolith(tmp_path / "loan-service")
    wf = proj.get("/api/workflows").json()[0]
    run = finish_workflow(proj, proj.post("/api/runs", json={"domain": "loan", "operation": "approve",
                                                             "workflow": wf["key"]}).json()["id"])
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    steps = [{**st, "code": {"repository": domain_repo(proj, tmp_path / "loan-service", agents[1])}} if st.get("key") == agents[1]
             else st for st in wf["steps"]]
    assert proj.put(f"/api/workflows/{wf['key']}?library=project", json={"steps": steps}).status_code == 200
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": agents[1]}).status_code == 200
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert paused["pending_branches"]["repository"]["url"] == str(tmp_path / "loan-service")
    run = finish_workflow(proj, run["id"])
    repos = {r["key"]: r for r in proj.get(f"/api/runs/{run['id']}/repositories").json()}
    assert repos["loan-service"]["changed"]


def test_an_older_run_approved_at_a_gate_asks_for_branches_before_a_later_code_step(client, tmp_path):
    """Runs from before workflows were saved (no workflow recorded) use the standard workflow's current code settings
    for the steps still to run, when they go on after a gate."""
    from app.db.models import Run

    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    make_monolith(tmp_path / "loan-service")
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "workflow": "standard"}).json()
    run = wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)
    with session_scope() as s:  # make it look like a run from before workflows and git support
        r = s.get(Run, run["id"])
        r.workflow, r.repositories, r.delivery = None, None, None
    wf = proj.get("/api/workflows/standard").json()
    later = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][2]  # the first step after the first gate
    steps = [{**st, "code": {"repository": domain_repo(proj, tmp_path / "loan-service", later)}} if st.get("key") == later
             else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    wait_for(proj, run["id"], {"awaiting_publish"}, timeout=60)  # the approved AS-IS documents come first
    assert proj.post(f"/api/runs/{run['id']}/docs/skip").status_code == 204
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=60)
    assert paused["current_stage"] == later


def test_a_step_that_changed_code_is_reviewed_and_committed_before_the_workflow_goes_on(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    paused = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    first = paused["current_stage"]
    assert paused["pending_commit"]["stage"] == first and paused["pending_commit"]["repository"]["key"] == "monolith"
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    assert [f["path"] for f in changes["files"] if not f["document"]] == [f"studio-stub/{first}.txt"]
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "Monolith changes from the first step", "branch": run["branch"],
        "files": [f"studio-stub/{first}.txt"], "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "log", "-1", "--format=%s", run["branch"]) == "Monolith changes from the first step"
    # The workflow went on; later steps only wrote documents, so nothing is left to commit at the end.
    done = finish_workflow(proj, run["id"])
    assert done["status"] == "completed"


def test_a_finished_runs_workspace_is_removed_and_a_rerun_starts_from_the_pushed_feature_branch(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = finish_workflow(proj, run["id"])
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    paths = [f["path"] for f in changes["files"]]
    r = proj.post(f"/api/runs/{run['id']}/commit", json={"message": "first", "branch": run["branch"], "files": paths,
                                                         "latest_sha": changes["latest_sha"],
                                                         "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    ws = Path(proj.get(f"/api/runs/{run['id']}").json()["workspace"])
    assert proj.get(f"/api/runs/{run['id']}").json()["status"] == "completed" and not ws.exists()

    # A teammate adds to the feature branch.
    mate = tmp_path / "mate"
    subprocess.run(["git", "clone", "--quiet", "--branch", run["branch"], str(upstream), str(mate)], check=True)
    (mate / "Teammate.java").write_text("by a teammate\n")
    git(mate, "add", "-A")
    git(mate, "commit", "-qm", "teammate's fix")
    git(mate, "push", "--quiet", "origin", run["branch"])
    teammate = git(mate, "rev-parse", "HEAD")

    # Re-running a step checks the repository out from the feature branch, teammate's commit included.
    first_agent = run["workflow_stages"][0]["key"]
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first_agent}).status_code == 200
    run = finish_workflow(proj, run["id"])
    assert (ws / "Teammate.java").read_text() == "by a teammate\n"
    assert run["status"] == "awaiting_commit"  # the step changed its file again (attempt 2)
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "message": "second", "branch": run["branch"], "files": [f["path"] for f in changes["files"]],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "rev-parse", f"{run['branch']}^") == teammate  # built on the teammate's commit
    assert git(upstream, "show", f"{run['branch']}:Teammate.java") == "by a teammate"


def test_a_run_waiting_for_branches_follows_the_workflows_current_code_settings(client, tmp_path):
    """The domain's repository, or the workflow's base branch for a step, changes while a run waits to confirm that
    step's branches: the question shows the new settings at once, a repository no step uses any more is dropped, and
    confirming works on the new one."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    service = tmp_path / "loan-service"
    make_monolith(service)
    base = git(service, "rev-parse", "--abbrev-ref", "HEAD")
    git(service, "branch", "develop")
    wf = proj.get("/api/workflows/standard").json()
    first = next(st["key"] for st in wf["steps"] if st["kind"] == "agent")

    def code(repository, base_branch=""):
        domain_repo(proj, repository, first, base_branch=base_branch)  # the domain's inputs for the step's agent
        steps = [{**st, "code": {"repository": ""}} if st.get("key") == first else st
                 for st in wf["steps"]]
        r = proj.put("/api/workflows/standard?library=project", json={"steps": steps})
        assert r.status_code == 200, r.text

    code(str(tmp_path / "old-service"))
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert paused["pending_branches"]["repository"]["url"] == str(tmp_path / "old-service")

    code(str(service), "develop")  # fixed on the domain (and the branch in the workflow) while the run waits
    paused = proj.get(f"/api/runs/{run['id']}").json()
    assert paused["pending_branches"]["repository"]["url"] == str(service)
    assert paused["pending_branches"]["repository"]["base_branch"] == "develop"
    assert [r["url"] for r in paused["repositories"]] == [str(service)]  # the old one is gone

    code(str(service), base)  # and the branch alone, too
    assert proj.get(f"/api/runs/{run['id']}").json()["pending_branches"]["repository"]["base_branch"] == base

    run = finish_workflow(proj, run["id"])
    assert run["status"] == "awaiting_commit"  # the end of a run that changed code: its commit is offered
    repos = {r["url"]: r for r in proj.get(f"/api/runs/{run['id']}/repositories").json()}
    assert repos[str(service)]["changed"]


def test_a_workflow_step_only_says_it_changes_code_and_each_domain_sets_the_repository(client):
    """A workflow serves every domain: a step says it changes code; each domain sets the repository and base branch
    of the step's agent in its Agent inputs (any URL), and can't save them without one while a step changes code."""
    client = as_project(client, new_project(client, monolith_repo="https://github.com/acme/mono.git"))
    wf = client.get("/api/workflows/standard").json()
    first = next(st["key"] for st in wf["steps"] if st["kind"] == "agent")
    for code in ({"repository": "https://github.com/acme/broker-service.git"}, {"repository": "@service"},
                 {"base_branch": "release/2.4"}):
        steps = [{**st, "code": code} if st.get("key") == first else st for st in wf["steps"]]
        r = client.put("/api/workflows/standard?library=project", json={"steps": steps})
        assert r.status_code == 422 and "set in each domain's Agent inputs" in r.text, code
    steps = [{**st, "code": {"changes_code": True}} if st.get("key") == first else st for st in wf["steps"]]
    assert client.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    saved = client.get("/api/workflows/standard").json()
    code = next(st for st in saved["steps"] if st.get("key") == first)["code"]
    assert code["changes_code"] and not code.get("repository") and not code.get("base_branch")

    client.post("/api/domains", json={"key": "repo", "name": "Repo", "description": ""})
    r = client.put("/api/domains/repo", json={"agent_inputs": {first: {"paths": [], "notes": "x"}}})
    assert r.status_code == 422 and "Set the repository of" in r.text  # mandatory for a code-changing agent
    for bad in ("https://", "https://github.com", "https://github.com/"):
        r = client.put("/api/domains/repo", json={"agent_inputs": {first: {"repository": bad}}})
        assert r.status_code == 422 and "isn't a complete repository URL" in r.text, bad
    r = client.put("/api/domains/repo", json={"agent_inputs": {first: {"repository": "https://github.com/acme/any.git",
                                                                       "base_branch": "develop"}}})
    assert r.status_code == 200 and r.json()["agent_inputs"][first]["repository"].endswith("any.git")
    steps = [{**st, "code": None} for st in wf["steps"]]  # no step changes code: the repository is optional again
    assert client.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    assert client.put("/api/domains/repo", json={"agent_inputs": {first: {"notes": "x"}}}).status_code == 200

def test_a_run_started_before_any_step_changed_code_reviews_its_commits_once_one_does(client, tmp_path):
    """A run created while no step changed code had nothing to commit. When a step changes code later (the workflow's
    code settings, picked up as the run goes on), the step's change is offered for review and commit."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    service = tmp_path / "loan-service"
    make_monolith(service)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)
    with session_scope() as s:
        from app.db.models import Run
        r = s.get(Run, run["id"])
        r.repository, r.legacy, r.base_sha, r.delivery = "", None, None, {"commit": False}  # as such a run was made
    wf = proj.get("/api/workflows/standard").json()
    later = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][2]  # the first step after the first gate
    steps = [{**st, "code": {"repository": domain_repo(proj, service, later)}} if st.get("key") == later else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    reviewed = []
    while True:
        run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_branches", "awaiting_commit", "awaiting_publish",
                                         "completed"}, timeout=60)
        if run["status"] == "awaiting_publish":
            assert proj.post(f"/api/runs/{run['id']}/docs/skip").status_code == 204
        elif run["status"] == "awaiting_branches":
            proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
        elif run["status"] == "awaiting_commit" and run["current_stage"]:
            reviewed.append(run["pending_commit"]["stage"])
            changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "loan-service"}).json()
            assert f"studio-stub/{later}.txt" in [f["path"] for f in changes["files"]]  # the diff to review
            break
        elif run["status"] == "awaiting_approval":
            assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
        else:
            break
    assert reviewed == [later]


def test_a_later_step_cuts_the_feature_branch_in_its_own_repository_after_an_earlier_push(client, tmp_path):
    """The run pushed its feature branch from an earlier step (to the monolith); a later step that changes another
    repository cuts the same branch there. The pushed branch is the run's own, so it is no reason to refuse."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    service = tmp_path / "loan-service"
    make_monolith(service)
    wf = proj.get("/api/workflows/standard").json()
    later = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][2]  # after the first gate
    steps = [{**st, "code": {"repository": domain_repo(proj, service, later)}} if st.get("key") == later else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    paused = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    first = paused["current_stage"]
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "First step", "branch": run["branch"], "files": [f"studio-stub/{first}.txt"],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "rev-parse", "--verify", run["branch"])  # pushed to the monolith

    while True:  # on to the later step, which asks for its own repository's branches
        run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_branches", "awaiting_publish"}, timeout=60)
        if run["status"] == "awaiting_branches":
            break
        if run["status"] == "awaiting_publish":
            assert proj.post(f"/api/runs/{run['id']}/docs/skip").status_code == 204
        else:
            assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    assert run["pending_branches"]["repository"]["url"] == str(service)
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    assert r.status_code == 200, r.text  # was refused: "<monolith> already has a branch ..."
    paused = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert paused["current_stage"] == later
    repos = {r["url"]: r for r in proj.get(f"/api/runs/{run['id']}/repositories").json()}
    assert repos[str(service)]["changed"]


def test_every_code_step_asks_for_its_branches_and_says_when_the_branch_exists(client, tmp_path):
    """Each step that changes code asks before it starts, even in a repository the run already works on; then the
    feature branch exists there, which the question says. Renaming it to a name taken there is still refused."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    git(upstream, "branch", "taken")
    wf = proj.get("/api/workflows/standard").json()
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    for agent in agents[:2]:
        domain_repo(proj, upstream, agent)  # both change the monolith
    steps = [{**st, "code": {"repository": ""}} if st.get("key") in agents[:2] else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert run["current_stage"] == agents[0] and run["pending_branches"]["existing_branch"] is None
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204

    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)  # the next code step asks again
    assert run["current_stage"] == agents[1]
    existing = run["pending_branches"]["existing_branch"]
    assert existing["in_run"] and f"Branch {run['branch']} already exists" in existing["message"]
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": "taken"})
    assert r.status_code == 422 and "already has a branch taken" in r.text
    assert proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]}).status_code == 200
    assert wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"] == agents[1]


def test_an_existing_branch_in_a_repository_not_checked_out_yet_is_continued_on(client, tmp_path):
    """The feature branch already exists in the repository a step is about to check out: the question says so, and
    the step continues on that branch, from its latest commit, instead of being refused."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    service = tmp_path / "loan-service"
    make_monolith(service)
    wf = proj.get("/api/workflows/standard").json()
    first = next(st["key"] for st in wf["steps"] if st["kind"] == "agent")
    steps = [{**st, "code": {"repository": domain_repo(proj, service, first)}} if st.get("key") == first else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    git(service, "checkout", "-q", "-b", "migration/loan/approve")
    (service / "EARLIER.md").write_text("work from an earlier attempt\n")
    git(service, "add", "-A")
    git(service, "commit", "-q", "-m", "earlier work")
    tip = git(service, "rev-parse", "HEAD")
    git(service, "checkout", "-q", "main")

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert run["branch"] == "migration/loan/approve"
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    assert r.status_code == 200, r.text  # was refused: "already has a branch"
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    with session_scope() as s:
        from app.db.models import AuditEvent, Run
        started = {r["url"]: r["base_sha"] for r in s.get(Run, run["id"]).repositories}
        log = " ".join(e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run["id"])))
    assert started[str(service)] == tip  # on the existing branch, not cut afresh from main
    assert "the existing branch migration/loan/approve" in log



def test_a_step_set_to_change_code_while_the_run_waits_at_a_commit_review_uses_its_repository(client, tmp_path):
    """The workflow's next step gets its repository while the run waits to commit the previous step's code: going on
    after the review picks it up, so the step asks for that repository's branches instead of running without it."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    service = tmp_path / "loan-orchestrator"
    make_monolith(service)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)

    wf = proj.get("/api/workflows/standard").json()
    following = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][1]  # the very next step
    steps = [{**st, "code": {"repository": domain_repo(proj, service, following)}} if st.get("key") == following else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204  # go on without committing yet
    paused = wait_for(proj, run["id"], {"awaiting_branches", "awaiting_commit", "awaiting_approval"}, timeout=30)
    assert paused["status"] == "awaiting_branches" and paused["current_stage"] == following
    assert paused["pending_branches"]["repository"]["url"] == str(service)


def test_a_step_never_falls_back_to_the_monolith_for_a_repository_the_run_has_not_checked_out():
    from app.orchestrator.graph import _code_dir
    from app.orchestrator.stages import StageDef

    stage = StageDef("orchestrator_service", "Orchestrator microservice", "agent", "",
                     code={"repository": "https://git.example.com/acme/{domain}-orchestrator.git"})
    checked_out = [{"url": "https://git.example.com/acme/loan-orchestrator.git", "path": ".studio/repos/orch"}]
    assert _code_dir(stage, checked_out, "loan") == ".studio/repos/orch"
    assert _code_dir(stage, [], "loan") is None  # not the monolith (".")
    assert _code_dir(StageDef("modular_monolith", "Module", "agent", "", code={"repository": ""}), [], "loan") == "."
    flagged = StageDef("orchestrator_service", "Orchestrator microservice", "agent", "", code={"changes_code": True})
    monolith = "https://git.example.com/acme/mono.git"
    assert _code_dir(flagged, [], "loan", "default", monolith) is None  # the domain hasn't set it: not the monolith


def test_an_existing_branch_in_the_monolith_is_shown_and_continued_on(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    git(upstream, "checkout", "-q", "-b", "migration/loan/approve")
    (upstream / "EARLIER.md").write_text("work from an earlier attempt\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "earlier work")
    tip = git(upstream, "rev-parse", "HEAD")
    git(upstream, "checkout", "-q", "main")

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    existing = run["pending_branches"]["existing_branch"]
    assert existing and not existing["in_run"] and "continues on it, from its latest commit" in existing["message"]
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert run["base_sha"] == tip and (Path(run["workspace"]) / "EARLIER.md").is_file()  # on the existing branch


def test_once_the_branch_is_pushed_the_review_compares_with_the_branch_in_git(client, tmp_path):
    """A step's change was committed and pushed; the step runs again and changes the same file. The review shows the
    file as it is on the feature branch in git next to the run's version, not an empty 'where the run started'."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    first = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    path = f"studio-stub/{first}.txt"

    def review():
        changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
        detail = proj.get(f"/api/runs/{run['id']}/changes/file",
                          params={"repo": "monolith", "path": path, "latest": changes["latest_sha"]}).json()
        return changes, detail

    changes, detail = review()
    assert not detail["in_git"] and detail["pushed"] is None and detail["original"] is None  # nothing pushed yet
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "First attempt", "branch": run["branch"], "files": [path],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text

    wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)  # the run went on and paused
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first}).status_code == 200
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)  # every code step asks
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    changes, detail = review()
    assert detail["in_git"] and "attempt 1" in detail["pushed"]  # the branch in git
    assert "attempt 2" in detail["ours"] and detail["original"] is None  # the run's version; new since the start


def test_a_step_changing_another_repository_reads_the_latest_of_the_monolith_branch(client, tmp_path):
    """The monolith step pushed its feature branch, and someone changed that branch outside the studio. A later step
    that changes another repository (the microservice lifting the module) starts from the monolith's latest too."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    service = tmp_path / "loan-service"
    make_monolith(service)
    wf = proj.get("/api/workflows/standard").json()
    later = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][2]
    steps = [{**st, "code": {"repository": domain_repo(proj, service, later)}} if st.get("key") == later else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    first = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "First step", "branch": run["branch"], "files": [f"studio-stub/{first}.txt"],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    _push_as_someone_else(upstream, tmp_path, run["branch"], {"module/Fix.java": "fixed outside the studio\n"})

    while True:
        run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_branches", "awaiting_publish"}, timeout=60)
        if run["status"] == "awaiting_branches":
            break
        if run["status"] == "awaiting_publish":
            assert proj.post(f"/api/runs/{run['id']}/docs/skip").status_code == 204
        else:
            assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    ws = Path(run["workspace"])
    assert not (ws / "module/Fix.java").exists()  # not before confirming
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    paused = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert paused["current_stage"] == later
    assert (ws / "module/Fix.java").read_text() == "fixed outside the studio\n"
    git(ws, "merge-base", "--is-ancestor", git(upstream, "rev-parse", run["branch"]), "HEAD")  # has the branch's latest
    repos = {r["key"]: r for r in proj.get(f"/api/runs/{run['id']}/repositories").json()}
    assert not repos["monolith"]["pending"]  # what came from git is not the run's to commit


def _pushed_first_step(client, tmp_path):
    """A run whose first step changed the monolith, committed and pushed; paused afterwards. (upstream, proj, run,
    the step, its file)."""
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    first = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    path = f"studio-stub/{first}.txt"
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "First step", "branch": run["branch"], "files": [path],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)
    return upstream, proj, run, first, path


def _push_as_someone_else(upstream, tmp_path, branch, files):
    other = tmp_path / "someone-else"
    subprocess.run(["git", "clone", "--quiet", "--branch", branch, str(upstream), str(other)], check=True)
    for name, text in files.items():
        (other / name).parent.mkdir(parents=True, exist_ok=True)
        (other / name).write_text(text)
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "someone else's work")
    git(other, "push", "-q", "origin", branch)


def test_a_rerun_step_starts_from_the_latest_of_the_branch_in_git(client, tmp_path):
    """Someone pushed to the run's feature branch after the run's own push. Re-running the step and confirming its
    branches fetches that work into the run's copy before the agent starts."""
    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    _push_as_someone_else(upstream, tmp_path, run["branch"], {"OTHER.md": "from a colleague\n"})

    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first}).status_code == 200
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert "the latest from it in git is fetched" in paused["pending_branches"]["existing_branch"]["message"]
    assert not (Path(run["workspace"]) / "OTHER.md").exists()  # not before confirming
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert (Path(run["workspace"]) / "OTHER.md").read_text() == "from a colleague\n"
    with session_scope() as s:
        from app.db.models import AuditEvent
        log = " ".join(e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run["id"])))
    assert "Fetched the latest of migration/loan/approve in Monolith" in log
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={  # the step's change goes on top of the colleague's work
        "repo": "monolith", "message": "Second attempt", "branch": run["branch"], "files": [path],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "show", f"{run['branch']}:OTHER.md") == "from a colleague"
    assert "attempt 2" in git(upstream, "show", f"{run['branch']}:{path}")

    # Bringing it up to date again with nothing new in git changes nothing.
    wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)
    from app import delivery
    delivery.update_from_git(run["id"], "monolith", None, lambda _m: None)  # brings in the step's own push
    head = git(Path(run["workspace"]), "rev-parse", "HEAD")
    logs = []
    delivery.update_from_git(run["id"], "monolith", None, logs.append)
    assert git(Path(run["workspace"]), "rev-parse", "HEAD") == head and "already has its latest" in logs[-1]


def test_changes_in_git_that_conflict_with_the_runs_work_stop_the_step(client, tmp_path):
    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    ws = Path(run["workspace"])
    (ws / path).write_text("this run's newer version, not pushed yet\n")  # the run's own work, not pushed
    git(ws, "commit", "-q", "-am", "unpushed work")
    _push_as_someone_else(upstream, tmp_path, run["branch"], {path: "a colleague's different version\n"})

    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first}).status_code == 200
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    failed = wait_for(proj, run["id"], {"failed"}, timeout=30)
    assert "conflict with this run's work not pushed yet" in failed["error"] and path in failed["error"]
    assert (ws / path).read_text() == "this run's newer version, not pushed yet\n"  # nothing lost
    assert not git(ws, "status", "--porcelain", "--untracked-files=no")


def test_a_fresh_rerun_does_not_bring_the_pushed_work_it_redoes_back_from_git(client, tmp_path):
    """Re-running a step "starting fresh" rolls the run's copy back to before its earlier, pushed work. Confirming the
    step's branches doesn't bring that work back from git: the step starts fresh, and its next commit replaces it."""
    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    ws = Path(run["workspace"])
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first, "fresh": True}).status_code == 200
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert not (ws / path).exists()  # rolled back: the copy lacks the pushed work

    from app import delivery
    logs = []
    delivery.update_from_git(run["id"], "monolith", None, logs.append)  # what confirming does first
    assert not (ws / path).exists() and "isn't brought back in" in logs[-1]
    delivery.update_from_git(run["id"], "monolith", None, logs.append)
    assert "isn't brought back in" in logs[-1]  # said every time, even with nothing new


def _two_monolith_steps(client, tmp_path):
    """The first two agent steps change the monolith; the first one's file is committed and pushed, and the run waits
    to commit the second one's. (upstream, proj, run, first file, second file)."""
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    for agent in agents[:2]:
        domain_repo(proj, upstream, agent)  # both change the monolith
    steps = [{**st, "code": {"repository": ""}} if st.get("key") in agents[:2] else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    first = f"studio-stub/{agents[0]}.txt"
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "First step", "branch": run["branch"], "files": [first],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    return upstream, proj, run, first, f"studio-stub/{agents[1]}.txt"


def test_files_left_out_of_a_commit_stay_as_they_are_on_the_branch_in_git(client, tmp_path):
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    listed = [f["path"] for f in changes["files"]]
    assert second in listed and first not in listed  # the first is already in git as it is: not a change, not listed
    assert changes["unchanged_in_git"] == 1
    r = proj.post(f"/api/runs/{run['id']}/commit", json={  # only the second step's file
        "repo": "monolith", "message": "Second step", "branch": run["branch"], "files": [second],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    on_branch = git(upstream, "ls-tree", "-r", "--name-only", run["branch"]).split()
    assert first in on_branch and second in on_branch  # was: the first file deleted from the branch


def test_a_commit_goes_on_top_of_work_pushed_meanwhile(client, tmp_path):
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    _push_as_someone_else(upstream, tmp_path, run["branch"], {"OTHER.md": "from a colleague\n"})
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    body = {"repo": "monolith", "message": "Second step", "branch": run["branch"], "files": [second],
            "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]}
    r = proj.post(f"/api/runs/{run['id']}/commit", json=body)
    assert r.status_code == 200, r.text  # was: "has commits this run didn't make"
    assert git(upstream, "show", f"{run['branch']}:OTHER.md") == "from a colleague"
    assert second in git(upstream, "ls-tree", "-r", "--name-only", run["branch"]).split()



def test_a_commit_is_refused_when_work_pushed_meanwhile_changed_the_same_files(client, tmp_path):
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    _push_as_someone_else(upstream, tmp_path, run["branch"], {second: "a colleague's version\n"})
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": "Second step", "branch": run["branch"], "files": [second],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 409 and "has changed in git, in files you're committing" in r.text and second in r.text
    assert git(upstream, "show", f"{run['branch']}:{second}") == "a colleague's version"  # untouched


def test_a_rebuilt_checkout_keeps_what_the_run_brought_in_from_git(client, tmp_path):
    """A run that never pushed brought a colleague's work in from git before a step. If its checkout is lost and
    rebuilt from the database, it starts from the branch in git again, so that work is still there."""
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    for agent in agents[:2]:
        domain_repo(proj, upstream, agent)  # both change the monolith
    steps = [{**st, "code": {"repository": ""}} if st.get("key") in agents[:2] else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    git(upstream, "branch", run["branch"], "main")  # the branch exists in git, from someone else
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204  # go on without pushing
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    _push_as_someone_else(upstream, tmp_path, run["branch"], {"OTHER.md": "from a colleague\n"})  # after checkout
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    ws = Path(run["workspace"])
    assert (ws / "OTHER.md").is_file()  # brought in before the second step

    from app import workspace
    from app.tools import git_ops
    git_ops.remove_tree(ws)
    workspace.ensure(run["id"])
    assert (ws / "OTHER.md").read_text() == "from a colleague\n"  # still there after the rebuild
    assert all((ws / f"studio-stub/{a}.txt").is_file() for a in agents[:2])  # and the steps' own changes, replayed


def test_a_step_whose_change_matches_git_asks_for_no_review(client, tmp_path):
    """A file the run changed but that is back to exactly what the branch in git has is no change: it isn't listed,
    and on its own it doesn't stop the run for a review."""
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    ws = Path(run["workspace"])
    from app import delivery
    from app.db.models import Run
    with session_scope() as s:
        r = s.get(Run, run["id"])
        repo = next(x for x in delivery.run_repos(r) if x["key"] == "monolith")
        assert delivery._pending(r, repo)  # the second step's file is new
    pushed = git(upstream, "show", f"{run['branch']}:{first}") + "\n"
    (ws / second).unlink()
    (ws / first).write_text("edited, then put back\n")
    git(ws, "commit", "-qam", "edit")
    (ws / first).write_text(pushed)
    git(ws, "commit", "-qam", "back to what git has")
    with session_scope() as s:
        r = s.get(Run, run["id"])
        repo = next(x for x in delivery.run_repos(r) if x["key"] == "monolith")
        assert not delivery._pending(r, repo)  # nothing differs from git: no review
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    assert [f["path"] for f in changes["files"] if not f["document"]] == []


def test_a_change_the_base_branch_already_has_is_not_listed(client, tmp_path):
    """The run changed a file, and the base branch got exactly that version meanwhile: committing it on top of the
    latest base branch changes nothing, so the review leaves it out and counts it."""
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    first = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    path = f"studio-stub/{first}.txt"
    before = [f["path"] for f in proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()["files"]]
    assert path in before

    (upstream / path).parent.mkdir(parents=True, exist_ok=True)
    (upstream / path).write_text((Path(run["workspace"]) / path).read_text())  # the same change, on main
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "same change on main")
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    assert path not in [f["path"] for f in changes["files"]]
    assert changes["unchanged_in_git"] == 1


def test_the_features_of_a_domain_run_on_their_own_branches_and_never_share_one(client, tmp_path):
    """Each feature on its own branch, cut from the base branch: a later feature's run doesn't start from an earlier
    one's pushed work (only from the base branch, once it is merged there), and no run may take a branch an unfinished
    run works on, the same domain's included."""
    upstream, key, proj = setup_project(client, tmp_path)
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    first = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    assert first["branch"] == "migration/loan/approve"
    first = wait_for(proj, first["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{first['id']}/branches", json={"feature_branch": first["branch"]})
    step = wait_for(proj, first["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    path = f"studio-stub/{step}.txt"
    changes = proj.get(f"/api/runs/{first['id']}/changes", params={"repo": "monolith"}).json()
    r = proj.post(f"/api/runs/{first['id']}/commit", json={
        "repo": "monolith", "message": "approve's module", "branch": first["branch"], "files": [path],
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    wait_for(proj, first["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)  # unfinished

    second = proj.post("/api/runs", json={"domain": "loan", "operation": "reject"})
    assert second.status_code == 201, second.text
    second = wait_for(proj, second.json()["id"], {"awaiting_branches"}, timeout=30)
    assert second["branch"] == "migration/loan/reject" and not second["pending_branches"]["existing_branch"]
    proj.post(f"/api/runs/{second['id']}/branches", json={"feature_branch": second["branch"]})
    wait_for(proj, second["id"], {"awaiting_commit"}, timeout=30)
    detail = proj.get(f"/api/runs/{second['id']}").json()
    pushed = git(upstream, "rev-parse", first["branch"])
    assert subprocess.run(["git", "merge-base", "--is-ancestor", pushed, "HEAD"],
                          cwd=detail["workspace"]).returncode != 0  # nothing of approve's branch in reject's

    proj.post("/api/domains/loan/features", json={"key": "cancel", "name": "cancel"})
    taken = proj.post("/api/runs", json={"domain": "loan", "operation": "cancel",
                                         "feature_branch": "migration/loan/approve"})
    assert taken.status_code == 422 and "Another unfinished run" in taken.json()["detail"]


def test_each_feature_has_its_own_branch_so_the_pattern_names_it(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    wf = proj.get("/api/workflows/standard").json()
    put = lambda git: proj.put("/api/workflows/standard?library=project", json={"steps": wf["steps"], "git": git})  # noqa: E731
    r = put({"commit": True, "branch_pattern": "mig/{domain}/modules"})
    assert r.status_code == 422 and "must name {feature}" in r.json()["detail"]
    assert put({"commit": True, "branch_pattern": ""}).status_code == 200
    assert proj.get("/api/runs-git-plan", params={"domain": "loan", "feature": "approve"}).json()["feature_branch"] == \
        "migration/loan/approve"
    assert put({"commit": True, "branch_pattern": "mig/{domain}-{feature}"}).status_code == 200
    from app import delivery
    assert delivery.git_settings({"git": {"branch_per": "domain", "branch_pattern": "migration/{domain}/modules"}}) \
        == {"commit": True, "branch_pattern": "migration/{domain}/{feature}"}  # saved before: per feature now


def test_each_domain_sets_its_agents_repositories_whatever_their_names(client, tmp_path):
    """One workflow, two domains running at once: each run works in the repository its own domain sets for the step's
    agent. A domain without one is refused before the run starts; domains may share a repository."""
    upstream, key, proj = setup_project(client, tmp_path)
    first = next(st["key"] for st in proj.get("/api/workflows/standard").json()["steps"] if st["kind"] == "agent")
    proj.post("/api/domains", json={"key": "card", "name": "Card", "description": ""})
    proj.post("/api/domains/card/features", json={"key": "issue", "name": "issue"})
    proj.post("/api/domains/card/features", json={"key": "renew", "name": "renew"})
    for name in ("lending-api", "plastic-flows"):
        make_monolith(tmp_path / name)
    wf = proj.get("/api/workflows/standard").json()
    later = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][2]
    steps = [{**st, "code": {"changes_code": True}} if st.get("key") == later else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    title = next(st["title"] for st in proj.get("/api/workflows/standard").json()["stages"] if st["key"] == later)
    r = proj.post("/api/runs", json={"domain": "card", "operation": "issue"})
    assert r.status_code == 422 and "in Card's Agent inputs" in r.json()["detail"] and title in r.json()["detail"]
    domain_repo(proj, tmp_path / "lending-api", later)
    domain_repo(proj, tmp_path / "plastic-flows", later, domain="card", base_branch="release/2.4", also={first: upstream})

    loan = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    card = proj.post("/api/runs", json={"domain": "card", "operation": "issue"}).json()
    assert [(r["url"], r["base_branch"]) for r in loan["repositories"]] == [(str(tmp_path / "lending-api"), None)]
    assert [(r["url"], r["base_branch"]) for r in card["repositories"]] == [(str(tmp_path / "plastic-flows"), "release/2.4")]
    domain_repo(proj, tmp_path / "lending-api", later, domain="card")  # domains may share a repository
    assert proj.post("/api/runs", json={"domain": "card", "operation": "renew"}).status_code == 201

def test_workflows_saved_with_repositories_become_flags_after_the_upgrade(client):
    from app import workflow_store
    from app.db.models import GLOBAL, SHARED, Setting, WorkflowDef

    with session_scope() as s:
        s.add(WorkflowDef(project=SHARED, key="old-urls", name="Old", description="", steps=[
            {"kind": "agent", "key": "as_is_doc"},
            {"kind": "agent", "key": "modular_monolith", "code": {"repository": "", "base_branch": ""}},
            {"kind": "agent", "key": "microservice_scaffold",
             "code": {"repository": "https://github.com/acme/springboot-test.git", "base_branch": "main"}},
            {"kind": "agent", "key": "orchestrator_service", "code": {"repository": "@orchestrator", "base_branch": ""}}]))
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == "_step_code_flags").delete()
    workflow_store.seed()
    with session_scope() as s:
        row = s.get(WorkflowDef, (SHARED, "old-urls"))
        steps = row.steps
        s.delete(row)  # shared by every project: don't leave its code steps behind
    assert [st.get("code") for st in steps] == [None, {"changes_code": True}, {"changes_code": True},
                                                {"changes_code": True}]


def test_a_domain_configures_the_agents_of_the_workflows_it_uses(client, tmp_path):
    """The domain page shows the workflows a domain uses (its new runs', then its earlier runs'); a repository is
    mandatory only for the code-changing agents of those workflows."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    lean = proj.get("/api/workflows/lean").json()
    service = next(st["key"] for st in lean["steps"] if st.get("key") == "microservice_scaffold")
    steps = [{**st, "code": {"changes_code": True}} if st.get("key") == service else st for st in lean["steps"]]
    assert proj.put("/api/workflows/lean?library=project", json={"steps": steps}).status_code == 200

    domain = proj.get("/api/domains/loan").json()  # uses the standard workflow: lean's code step isn't its concern
    assert [w["key"] for w in domain["workflows"]] == ["standard"]
    assert proj.put("/api/domains/loan", json={"agent_inputs": {"as_is_doc": {"notes": "x"}}}).status_code == 200

    assert proj.put("/api/domains/loan", json={"default_workflow": "lean"}).status_code == 200
    domain = proj.get("/api/domains/loan").json()
    assert [w["key"] for w in domain["workflows"]] == ["lean"]
    assert domain["workflows"][0]["code_agents"] == [service] and service in domain["workflows"][0]["agents"]
    r = proj.put("/api/domains/loan", json={"agent_inputs": {"as_is_doc": {"notes": "x"}}})
    assert r.status_code == 422 and "Set the repository of" in r.text  # now its own workflow's


def test_a_domain_whose_runs_used_another_workflow_doesnt_list_the_standard(client, tmp_path):
    """No default of its own or the project's: the domain uses the workflows its runs used, not the built-in fallback."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    assert [w["key"] for w in proj.get("/api/domains/loan").json()["workflows"]] == ["standard"]  # no runs yet
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve", "workflow": "lean"})
    assert run.status_code == 201, run.text
    assert [w["key"] for w in proj.get("/api/domains/loan").json()["workflows"]] == ["lean"]
    proj.put("/api/domains/loan", json={"default_workflow": "standard"})  # an explicit default comes first
    assert [w["key"] for w in proj.get("/api/domains/loan").json()["workflows"]] == ["standard", "lean"]


def test_the_orchestrators_repository_is_only_needed_when_a_cross_domain_feature_runs_it(client, tmp_path):
    """Steps skipped for domain-only features (the orchestrator) don't make a domain set their repository up front:
    saving and starting work without it; the step asks for it if a cross-domain feature reaches it."""
    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    make_monolith(tmp_path / "loan-api")
    lean = proj.get("/api/workflows/lean").json()
    steps = [{**st, "code": {"changes_code": True}} if st.get("key") in ("microservice_scaffold", "orchestrator_service")
             else st for st in lean["steps"]]
    assert proj.put("/api/workflows/lean?library=project", json={"steps": steps}).status_code == 200
    assert proj.put("/api/domains/loan", json={"default_workflow": "lean"}).status_code == 200
    workflow = proj.get("/api/domains/loan").json()["workflows"][0]
    assert workflow["when_needed"] == ["orchestrator_service"]

    domain_repo(proj, tmp_path / "loan-api", "microservice_scaffold")  # no orchestrator repository: saved
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"})
    assert run.status_code == 201, run.text  # and the run starts

    from app import delivery
    stages = proj.get("/api/workflows/lean").json()["stages"]
    orchestrator = [st for st in stages if st["key"] == "orchestrator_service"]
    assert "Orchestrator microservice" in delivery.missing_repositories(orchestrator, "loan", key, when_needed=True)


def test_a_rerun_waiting_to_confirm_its_branches_no_longer_says_its_agent_changed(client, tmp_path):
    """Update and re-run from a code step whose agent changed: the run waits to confirm the step's branches before the
    new attempt exists. That step uses the current agent, so the run doesn't offer the re-run again meanwhile."""
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    first = next(st["key"] for st in proj.get("/api/workflows/standard").json()["steps"] if st["kind"] == "agent")
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204  # go on without committing
    wait_for(proj, run["id"], {"awaiting_approval", "awaiting_publish"}, timeout=30)

    agent = proj.get(f"/api/agents/{first}").json()
    assert proj.put(f"/api/agents/{first}", json={**agent, "guardrails": "Name every table."}).status_code == 200
    try:
        update = proj.get(f"/api/runs/{run['id']}").json()["workflow_update"]
        assert update and update["agents_changed"] and update["rerun"]["from"] == first
        assert proj.post(f"/api/runs/{run['id']}/update-workflow", json={"rerun": True}).status_code == 200
        paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
        assert paused["current_stage"] == first
        assert paused["workflow_update"] is None  # the step about to run uses the current agent
    finally:
        proj.put(f"/api/agents/{first}", json=agent)


def test_confirming_can_bring_in_the_latest_of_the_base_branch(client, tmp_path):
    """Work merged into the base branch since the run started (a fix made by hand, another feature) is offered when the
    step's branches are confirmed, and merged into the run's copy when asked."""
    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    (upstream / "FIX.md").write_text("fixed by hand on the base branch\n")  # the base branch is checked out there
    git(upstream, "add", "FIX.md")
    git(upstream, "commit", "-q", "-m", "a fix made by hand")

    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first}).status_code == 200
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    from app import repos as repos_module
    with session_scope() as s:
        project = s.get(Run, run["id"]).project
    repos_module.fetch(project, paused["pending_branches"]["repository"]["url"])  # as a background fetch would
    behind = proj.get(f"/api/runs/{run['id']}").json()["pending_branches"]["repository"]["base_behind"]
    assert behind == 1
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"], "update_base": True})
    wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert (Path(run["workspace"]) / "FIX.md").read_text() == "fixed by hand on the base branch\n"
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    assert "FIX.md" not in {f["path"] for f in changes["files"]}  # the base branch's work isn't the run's to commit


def test_a_code_step_does_not_start_on_a_stale_copy_when_git_cant_be_reached(client, tmp_path, monkeypatch):
    from app import delivery
    from app import repos as repos_module
    from app.tools import git_ops

    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)

    def unreachable(*_a, **_k):
        raise git_ops.GitError("could not read from remote repository")

    monkeypatch.setattr(repos_module, "fetch", unreachable)
    with pytest.raises(delivery.DeliveryError, match="didn't start"):
        delivery.update_from_git(run["id"], "monolith", None, lambda _m: None)


def test_confirming_a_step_refreshes_every_repository_the_run_reads(client, tmp_path, monkeypatch):
    """Modernise changes the modern service but converts the lifted service: the lift (and the monolith) get the
    latest of their feature branch too, so a fix pushed there by hand is what it converts."""
    from app import delivery

    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    real = delivery.run_repos
    monkeypatch.setattr(delivery, "run_repos", lambda r: [*real(r), {"key": "lift", "url": "x", "path": "lift",
                                                                      "base_sha": "abc", "name": "Lift"},
                                                          {"key": "modern", "url": "y", "path": "modern",
                                                           "base_sha": "abc", "name": "Modern"}])
    updated = []
    monkeypatch.setattr(delivery, "update_from_git", lambda rid, key, user, log: updated.append(key))
    delivery._apply_confirmation(run["id"], "modern", {"feature_branch": run["branch"]}, lambda _m: None)
    assert updated[0] == "modern" and set(updated) == {"modern", "monolith", "lift"}


def test_a_fresh_rerun_of_a_pushed_step_undoes_its_pushed_code_on_the_next_commit(client, tmp_path):
    """Start fresh (or a redo) after the step's code was pushed: the step starts from before it, not from the pushed
    code (what a teammate pushed since is still brought in), and the next commit undoes the old code in git, on top
    of the branch (no force push)."""
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    run = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    first = run["current_stage"]
    ws = Path(run["workspace"])
    (ws / "Extra.java").write_text("only the first attempt wrote this\n")  # as its agent did
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "agent work")
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    code = [f["path"] for f in changes["files"] if not f["document"]]
    assert sorted(code) == ["Extra.java", f"studio-stub/{first}.txt"]
    r = proj.post(f"/api/runs/{run['id']}/commit", json={"message": "first", "branch": run["branch"], "files": code,
                                                         "latest_sha": changes["latest_sha"],
                                                         "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)

    mate = tmp_path / "mate"  # a teammate pushes on top
    subprocess.run(["git", "clone", "--quiet", "--branch", run["branch"], str(upstream), str(mate)], check=True)
    (mate / "Teammate.java").write_text("by a teammate\n")
    git(mate, "add", "-A")
    git(mate, "commit", "-qm", "teammate's fix")
    git(mate, "push", "--quiet", "origin", run["branch"])
    teammate = git(mate, "rev-parse", "HEAD")

    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first, "fresh": True}).status_code == 200
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    run = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    assert not (ws / "Extra.java").exists()  # not brought back from git
    assert (ws / "Teammate.java").read_text() == "by a teammate\n"  # what others pushed is
    assert "attempt 2" in (ws / f"studio-stub/{first}.txt").read_text()

    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    files = {f["path"]: f for f in changes["files"] if not f["document"]}
    assert files["Extra.java"]["status"] == "D" and files["Extra.java"]["undo"]
    assert not files[f"studio-stub/{first}.txt"]["undo"]
    assert "Teammate.java" not in files
    detail = proj.get(f"/api/runs/{run['id']}/changes/file",
                      params={"repo": "monolith", "path": "Extra.java", "latest": changes["latest_sha"]})
    assert detail.status_code == 200, detail.text
    r = proj.post(f"/api/runs/{run['id']}/commit", json={"message": "fresh", "branch": run["branch"], "files": list(files),
                                                         "latest_sha": changes["latest_sha"],
                                                         "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    branch = run["branch"]
    assert git(upstream, "rev-parse", f"{branch}^") == teammate  # on top of what was there: no force push
    tree = git(upstream, "ls-tree", "-r", "--name-only", branch).split()
    assert "Extra.java" not in tree and "Teammate.java" in tree
    assert "attempt 2" in git(upstream, "show", f"{branch}:studio-stub/{first}.txt")
    record = proj.get(f"/api/runs/{run['id']}").json()["delivery"]["repos"]["monolith"]
    assert "replaces" not in record  # done: later refreshes bring the branch in as usual


def test_a_fresh_rerun_can_cut_a_new_feature_branch_from_the_latest_base(client, tmp_path, monkeypatch):
    """Per-feature branching: a fresh re-run can start on a new feature branch cut from the latest base branch. The old
    branch stays in git as it is, and the run's commits go to the new one, on top of the latest base."""
    upstream, key, proj = setup_project(client, tmp_path)
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    run = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    first, old = run["current_stage"], run["branch"]
    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    code = [f["path"] for f in changes["files"] if not f["document"]]
    assert proj.post(f"/api/runs/{run['id']}/commit", json={
        "message": "first", "branch": old, "files": code, "latest_sha": changes["latest_sha"],
        "head_sha": changes["head_sha"]}).status_code == 200
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)
    pushed = git(upstream, "rev-parse", old)
    (upstream / "Hotfix.java").write_text("on main since\n")  # the base branch moves on
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "hotfix")
    main = git(upstream, "rev-parse", "HEAD")

    url = f"/api/runs/{run['id']}/restart"
    assert proj.post(url, json={"stage": first, "new_branch": f"{old}-v2"}).status_code == 422  # fresh only
    assert proj.post(url, json={"stage": first, "fresh": True, "new_branch": old}).status_code == 422
    assert proj.post(url, json={"stage": first, "fresh": True, "new_branch": "main"}).status_code == 422
    r = proj.post(url, json={"stage": first, "fresh": True, "new_branch": f"{old}-v2"})
    assert r.status_code == 200, r.text

    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert run["branch"] == f"{old}-v2" and run["pending_branches"]["feature_branch"] == f"{old}-v2"
    assert run["delivery"]["repos"] == {} and run["delivery"]["previous_branches"][-1]["branch"] == old
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    run = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)
    ws = Path(run["workspace"])
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == f"{old}-v2"
    assert (ws / "Hotfix.java").exists()  # from the latest base branch
    assert "attempt 2" in (ws / f"studio-stub/{first}.txt").read_text()

    changes = proj.get(f"/api/runs/{run['id']}/changes").json()
    code = [f["path"] for f in changes["files"] if not f["document"]]
    assert code == [f"studio-stub/{first}.txt"] and not any(f["undo"] for f in changes["files"])
    r = proj.post(f"/api/runs/{run['id']}/commit", json={
        "message": "fresh", "branch": run["branch"], "files": code, "latest_sha": changes["latest_sha"],
        "head_sha": changes["head_sha"]})
    assert r.status_code == 200, r.text
    assert git(upstream, "rev-parse", f"{old}-v2^") == main  # straight on the latest base
    assert git(upstream, "rev-parse", old) == pushed  # the old branch as it was

    # Taken now: the same name is refused at once.
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)
    r = proj.post(url, json={"stage": first, "fresh": True, "new_branch": f"{old}-v2"})
    assert r.status_code == 422  # it is the run's branch now


def test_a_new_branch_from_a_later_step_replays_the_earlier_steps_code(client, tmp_path, monkeypatch):
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    run = proj.get(f"/api/runs/{run['id']}").json()
    old, step = run["branch"], run["current_stage"]
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)
    (upstream / "Hotfix.java").write_text("on main since\n")  # the base branch moves on
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "hotfix")
    r = proj.post(f"/api/runs/{run['id']}/restart", json={"stage": step, "fresh": True, "new_branch": "mig/v2"})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    ws = Path(run["workspace"])
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == "mig/v2"
    assert (ws / first).exists() and not (ws / second).exists()  # the step before: replayed; this one: to redo
    assert (ws / "Hotfix.java").exists()

    # Redoing the first step, which ran before the cut, goes back on the new branch, not to the old one's commits.
    run = finish_workflow_until_gate(proj, run["id"])
    gate = run["pending_gate"]
    assert gate["reviews"][0] != step
    r = proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "reject", "comments": "again", "scope": "redo",
                                                           "target_stage": gate["reviews"][0]})
    assert r.status_code == 200, r.text
    wait_for(proj, run["id"], {"awaiting_branches", "awaiting_commit", "awaiting_approval"}, timeout=30)
    assert git(ws, "rev-parse", "--abbrev-ref", "HEAD") == "mig/v2" and (ws / "Hotfix.java").exists()


def finish_workflow_until_gate(proj, run_id) -> dict:
    """Confirm branches and go on without committing until the run waits at a gate."""
    while True:
        run = wait_for(proj, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "failed"}, timeout=60)
        if run["status"] == "awaiting_branches":
            proj.post(f"/api/runs/{run_id}/branches", json={"feature_branch": run["pending_branches"]["feature_branch"]})
        elif run["status"] == "awaiting_commit":
            assert proj.post(f"/api/runs/{run_id}/finish").status_code == 204
        else:
            assert run["status"] == "awaiting_approval", run.get("error")
            return run


def test_retry_after_a_failed_cut_cuts_the_new_branch_again(client, tmp_path, monkeypatch):
    from app import workspace
    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    original, calls = workspace.rebuild, []

    def replay_fails_once(run_id, before_stage=None, branch=None, bases=None):
        if branch and not calls:
            calls.append(branch)
            raise workspace.git_ops.GitError("Could not replay as_is_doc: patch does not apply")
        return original(run_id, before_stage=before_stage, branch=branch, bases=bases)

    monkeypatch.setattr(workspace, "rebuild", replay_fails_once)
    old = run["branch"]
    r = proj.post(f"/api/runs/{run['id']}/restart", json={"stage": first, "fresh": True, "new_branch": "mig/v2"})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"failed"}, timeout=30)
    assert "Could not cut mig/v2" in run["error"] and run["branch"] == old
    assert run["delivery"]["failed_cut"] == {"stage": first, "branch": "mig/v2"}
    assert proj.post(f"/api/runs/{run['id']}/retry").status_code == 200
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert run["branch"] == "mig/v2" and "failed_cut" not in run["delivery"]


def test_a_fresh_rerun_after_the_push_does_not_mark_the_pushed_code_for_undoing(client, tmp_path):
    """Starting the step after a pushed one fresh rolls back to after the push: nothing pushed is gone."""
    upstream, proj, run, first, second = _two_monolith_steps(client, tmp_path)
    step = proj.get(f"/api/runs/{run['id']}").json()["current_stage"]
    assert proj.post(f"/api/runs/{run['id']}/finish").status_code == 204
    wait_for(proj, run["id"], {"awaiting_approval"}, timeout=30)
    assert proj.post(f"/api/runs/{run['id']}/restart", json={"stage": step, "fresh": True}).status_code == 200
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    assert "replaces" not in run["delivery"]["repos"]["monolith"]


def test_a_new_run_reads_the_latest_base_branch_with_features_merged_since(client, tmp_path):
    """A feature started after another was merged into the base branch gets that code: the run fetches the base branch
    when it starts, without anyone fetching on the Project page first."""
    upstream, key, proj = setup_project(client, tmp_path)
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    first = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    wait_for(proj, first["id"], {"awaiting_branches"}, timeout=30)
    (upstream / "Merged.java").write_text("class Merged {}\n")  # approve's work, merged into main by hand
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "merge approve")
    merged = git(upstream, "rev-parse", "HEAD")
    second = proj.post("/api/runs", json={"domain": "loan", "operation": "reject"}).json()
    second = wait_for(proj, second["id"], {"awaiting_branches"}, timeout=30)
    assert second["legacy"]["sha"] == merged  # it reads the base branch with approve's merge
    proj.post(f"/api/runs/{second['id']}/branches", json={"feature_branch": second["branch"]})
    second = wait_for(proj, second["id"], {"awaiting_commit"}, timeout=30)
    assert (Path(second["workspace"]) / "Merged.java").exists()  # and its code branch is cut from there


def test_a_migration_needs_the_base_package_prefix(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    assert client.put(f"/api/projects/{key}/settings", json={"base_package_prefix": "Com.Acme"}).status_code == 422
    assert client.put(f"/api/projects/{key}/settings", json={"base_package_prefix": ""}).status_code == 200
    r = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"})
    assert r.status_code == 422 and "Set the base package prefix" in r.json()["detail"]
    assert client.put(f"/api/projects/{key}/settings", json={"base_package_prefix": "com.acme"}).status_code == 200
    assert proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).status_code == 201


def test_an_empty_repository_is_refused_with_a_clear_message_instead_of_a_git_error(client, tmp_path):
    """A domain's repository created empty on the git host (no commit yet): confirming the step's branches says so,
    instead of failing later with git's "ambiguous argument 'HEAD'"; starting a run says so once it's been fetched."""
    from app.tools import git_ops

    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    empty = tmp_path / "loan-service.git"
    subprocess.run(["git", "init", "-q", "--bare", str(empty)], check=True)
    wf = proj.get("/api/workflows/standard").json()
    first = next(st["key"] for st in wf["steps"] if st["kind"] == "agent")
    steps = [{**st, "code": {"repository": domain_repo(proj, empty, first)}} if st.get("key") == first else st
             for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200

    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    assert r.status_code == 422 and "has no commit yet (an empty repository)" in r.json()["detail"]
    assert proj.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_branches"  # nothing checked out

    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    r = proj.post("/api/runs", json={"domain": "loan", "operation": "reject"})  # its copy is on the server now
    assert r.status_code == 422 and "has no commit yet" in r.json()["detail"]

    mirror = tmp_path / "mirror.git"  # the checkout itself: no half-made repository left behind
    git_ops.clone_mirror(str(empty), mirror)
    try:
        git_ops.init_workspace(tmp_path / "ws", str(mirror), "migration/loan/approve")
        raise AssertionError("an empty repository was checked out")
    except git_ops.GitError as e:
        assert "has no commit yet" in str(e) and not (tmp_path / "ws").exists()


def test_a_checkout_left_without_commits_is_rebuilt_and_a_step_that_empties_one_is_stopped(client, tmp_path):
    import shutil

    from app import workspace
    from app.orchestrator.graph import _repo_left
    from app.tools import git_ops

    upstream, proj, run, first, path = _pushed_first_step(client, tmp_path)
    ws = Path(run["workspace"])
    start = git_ops.head(ws)
    shutil.rmtree(ws / ".git")  # an interrupted clone, or `git init` run in it
    git(ws, "init", "-q")
    assert not git_ops.has_commits(ws)
    assert _repo_left(ws, ws, run["branch"], start) == \
        "the workspace root has no commit any more: its git history was removed or re-initialised (`git init`)"
    workspace.ensure(run["id"])  # what every step and review does first
    assert git_ops.has_commits(ws) and git_ops.head(ws)


def test_confirming_a_branch_another_run_of_the_same_domain_works_on_is_refused(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    first = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    first = wait_for(proj, first["id"], {"awaiting_branches"}, timeout=30)
    assert proj.post(f"/api/runs/{first['id']}/branches", json={"feature_branch": first["branch"]}).status_code == 200
    second = proj.post("/api/runs", json={"domain": "loan", "operation": "reject"}).json()
    second = wait_for(proj, second["id"], {"awaiting_branches"}, timeout=30)
    r = proj.post(f"/api/runs/{second['id']}/branches", json={"feature_branch": first["branch"]})
    assert r.status_code == 422 and "Another unfinished run works on branch" in r.json()["detail"]
