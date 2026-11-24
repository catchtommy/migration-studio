"""Discovery runs and the monolith's latest code: 'Fetch now' on a mirror that lost its origin, a rejected or re-run
bounded-contexts step reading the latest commit, and discarding a discovery run without recording anything."""

import subprocess
import time

from app import repos
from app.tools import git_ops
from tests.conftest import wait_for
from tests.test_projects import as_project, make_monolith, new_project


def _fetched(proj, key):
    for _ in range(200):
        status = proj.get(f"/api/projects/{key}/repository").json()
        if not status["fetching"]:
            return status
        time.sleep(0.05)
    raise AssertionError("still fetching")


def _project_with_monolith(client, tmp_path):
    upstream = tmp_path / "monolith"
    commit = make_monolith(upstream)
    key = new_project(client)
    proj = as_project(client, key)
    assert proj.put(f"/api/projects/{key}/settings", json={"monolith_repo": str(upstream)}).status_code == 200
    assert _fetched(proj, key)["cloned"]
    return key, proj, upstream, commit


def _new_commit(upstream, commit, text):
    (upstream / "App.java").write_text(text)
    commit("commit", "--quiet", "-am", text)


def test_fetching_a_mirror_without_an_origin_url(tmp_path):
    upstream = tmp_path / "up"
    commit = make_monolith(upstream)
    mirror = tmp_path / "m.git"
    git_ops.clone_mirror(str(upstream), mirror)
    subprocess.run(["git", "config", "--unset", "remote.origin.url"], cwd=mirror, check=True)
    subprocess.run(["git", "config", "--unset-all", "remote.origin.fetch"], cwd=mirror, check=True)
    _new_commit(upstream, commit, "v2")

    git_ops.fetch_mirror(mirror, str(upstream))  # used to fail: git config --get remote.origin.url failed

    assert git_ops.commit_info(mirror, "main")["subject"] == "v2"
    assert git_ops.git(mirror, "config", "--get", "remote.origin.url") == str(upstream)


def test_a_folder_that_is_not_a_repository_is_not_a_mirror(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    git_ops.git(outer, "init", "--quiet")  # an enclosing repository, as the studio's own is for backend/data
    (outer / "x.git").mkdir()
    assert not git_ops.is_mirror(outer / "x.git")
    git_ops.git(tmp_path, "init", "--quiet", "--bare", "y.git")
    assert git_ops.is_mirror(tmp_path / "y.git")


def test_fetch_now_clones_again_over_a_broken_mirror(client, tmp_path):
    key, proj, upstream, commit = _project_with_monolith(client, tmp_path)
    mirror = repos.mirror_dir(key)
    git_ops.remove_tree(mirror)
    mirror.mkdir()  # left empty, as an interrupted clone or a clean-up can
    _new_commit(upstream, commit, "v2")

    assert proj.post(f"/api/projects/{key}/repository/fetch").status_code == 202
    status = _fetched(proj, key)
    assert status["error"] is None and status["head"]["subject"] == "v2"
    assert git_ops.is_mirror(mirror)


def _contexts_run(proj):
    r = proj.post("/api/discovery/bounded-contexts")
    assert r.status_code == 201, r.text
    return wait_for(proj, r.json()["id"], {"awaiting_approval"})


def test_rejecting_bounded_contexts_reads_the_latest_monolith(client, tmp_path):
    key, proj, upstream, commit = _project_with_monolith(client, tmp_path)
    run = _contexts_run(proj)
    started = run["legacy"]["sha"]

    _new_commit(upstream, commit, "v2")
    assert proj.post(f"/api/projects/{key}/repository/fetch").status_code == 202
    _fetched(proj, key)
    assert proj.get(f"/api/runs/{run['id']}").json()["monolith_behind"] == 1  # shown for a discovery run too

    r = proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "reject", "comments": "Use the new code"})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_approval"})
    assert run["legacy"]["sha"] != started and run["legacy"]["subject"] == "v2"
    assert not run["monolith_behind"]


def test_redoing_a_bounded_contexts_step_fetches_the_latest_first(client, tmp_path):
    key, proj, upstream, commit = _project_with_monolith(client, tmp_path)
    run = _contexts_run(proj)
    _new_commit(upstream, commit, "v3")  # not fetched yet: the re-run fetches it

    stage = next(s["key"] for s in run["workflow_stages"] if s["kind"] == "agent")
    r = proj.post(f"/api/runs/{run['id']}/restart", json={"stage": stage, "fresh": True})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_approval"})
    assert run["legacy"]["subject"] == "v3"


def test_a_discovery_run_can_be_discarded_without_recording_anything(client, tmp_path):
    key, proj, upstream, commit = _project_with_monolith(client, tmp_path)
    before = {d["key"] for d in proj.get("/api/domains").json()}
    run = _contexts_run(proj)

    assert proj.post(f"/api/runs/{run['id']}/discard").status_code == 204
    assert proj.get(f"/api/runs/{run['id']}").status_code == 404
    assert {d["key"] for d in proj.get("/api/domains").json()} == before  # nothing recorded
    assert proj.get("/api/discovery").json()["bounded_contexts"] is None
    _contexts_run(proj)  # a new run can start


def test_only_unfinished_discovery_runs_can_be_discarded(client, tmp_path):
    key, proj, upstream, commit = _project_with_monolith(client, tmp_path)
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    proj.post("/api/domains/d/features", json={"key": "one", "name": "one"})
    migration = proj.post("/api/runs", json={"domain": "d", "operation": "one"}).json()
    wait_for(proj, migration["id"], {"awaiting_approval"})
    assert proj.post(f"/api/runs/{migration['id']}/discard").status_code == 422
