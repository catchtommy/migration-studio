"""Committing a run's code changes to git: feature branches, the review, conflicts, per-user credentials."""

import subprocess
from pathlib import Path

from sqlalchemy import select

from app import git_auth
from app.db.models import GitCredential
from app.db.session import session_scope
from tests.conftest import wait_for
from tests.test_auth import make_user
from tests.test_projects import as_project, make_monolith, new_project, signed_in


def git(repo, *args) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def finish_workflow(client, run_id, confirm=None) -> dict:
    """Approve every gate, confirm the proposed branches (or what `confirm(pending)` returns) and go on without
    committing right after code steps, until the workflow is done."""
    while True:
        run = wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "completed",
                                        "failed"}, timeout=60)
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

    # A new run can't take a branch the repository already has.
    proj.post("/api/domains/loan/features", json={"key": "reject", "name": "reject"})
    r = proj.post("/api/runs", json={"domain": "loan", "operation": "reject", "feature_branch": branch})
    assert r.status_code == 422 and "already has a branch" in r.json()["detail"]


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
    code = {agents[0]: {"repository": ""},  # the monolith
            agents[1]: {"repository": str(tmp_path / "{domain}-service"), "base_branch": "release/2.4"},
            agents[2]: {"repository": str(tmp_path / "orchestrator")}}
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


def test_git_tokens_are_per_user_encrypted_and_reach_git_through_askpass(client):
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
    assert env["GIT_CONFIG_VALUE_0"] == ""  # no other credential helper
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
    steps = [{**st, "code": {"repository": str(tmp_path / "{domain}-service")}} if st.get("key") == agents[1] else st
             for st in wf["steps"]]
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
    steps = [{**st, "code": {"repository": str(tmp_path / "{domain}-service")}} if st.get("key") == later else st
             for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    paused = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
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
