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


# --- bounded contexts refresh the domains they found ---------------------------------------------------------------

def _approve_contexts(proj, yaml, replace=False):
    run_id = proj.post("/api/discovery/bounded-contexts").json()["id"]
    wait_for(proj, run_id, {"awaiting_approval"})
    doc = next(a for a in proj.get(f"/api/runs/{run_id}/artifacts").json() if a["name"] == "bounded-contexts.yaml")
    assert proj.put(f"/api/artifacts/{doc['id']}", json={"content": yaml}).status_code == 200
    r = proj.post(f"/api/runs/{run_id}/decision", json={"decision": "approve", "replace": replace})
    assert r.status_code == 200, r.text
    wait_for(proj, run_id, {"completed"})
    return run_id


def test_approved_bounded_contexts_refresh_existing_domains(client):
    proj = as_project(client, new_project(client))
    _approve_contexts(proj, """contexts:
  - {key: loan, name: Loan, description: Loans v1, tables: [LOAN, LOAN_OLD], packages: [com.acme.loan],
     entry_points: [LoanController]}
  - {key: fees, name: Fees, description: Fees v1, tables: [FEE]}
""")
    # Changed by hand since: a table added to loan, the fees description rewritten
    proj.put("/api/domains/loan", json={"owned_tables": ["LOAN", "LOAN_OLD", "HAND_T"]})
    proj.put("/api/domains/fees", json={"description": "Written by hand"})

    run_id = _approve_contexts(proj, """contexts:
  - {key: loan, name: Loan, description: Loans v2, tables: [LOAN, LOAN_NEW], packages: [com.acme.loan],
     entry_points: [LoanApi]}
  - {key: fees, name: Fees, description: Fees v2, tables: [FEE, LOAN_OLD]}
""")
    loan, fees = (proj.get(f"/api/domains/{k}").json() for k in ("loan", "fees"))
    assert loan["owned_tables"] == ["LOAN", "HAND_T", "LOAN_NEW"]  # LOAN_OLD gone, the hand-added table kept
    assert fees["owned_tables"] == ["FEE", "LOAN_OLD"]  # moved by the run, free once loan no longer has it
    assert loan["description"] == "Loans v2" and fees["description"] == "Written by hand"
    assert loan["agent_inputs"]["domain_features"]["paths"] == ["LoanApi"]  # the feature listing looks at the latest
    from sqlalchemy import select

    from app.db.models import AuditEvent
    from app.db.session import flush_logs, session_scope
    flush_logs()  # log lines are written in batches
    with session_scope() as s:
        summary = s.scalars(select(AuditEvent.message).where(AuditEvent.run_id == run_id,
                                                             AuditEvent.message.like("Bounded contexts recorded%"))).all()
    assert len(summary) == 1 and "removed as no longer found there: LOAN_OLD from loan" in summary[0]


def test_hand_set_entry_points_are_kept(client):
    proj = as_project(client, new_project(client))
    _approve_contexts(proj, "contexts:\n  - {key: loan, name: Loan, entry_points: [LoanController]}\n")
    proj.put("/api/domains/loan", json={"agent_inputs": {"domain_features": {"paths": ["src/loan/"], "notes": "n"}}})
    _approve_contexts(proj, "contexts:\n  - {key: loan, name: Loan, entry_points: [LoanApi]}\n")
    assert proj.get("/api/domains/loan").json()["agent_inputs"]["domain_features"] == {"paths": ["src/loan/"],
                                                                                         "notes": "n"}


# --- a feature listing from scratch --------------------------------------------------------------------------------

def test_a_feature_listing_from_scratch_keeps_only_features_being_migrated(client):
    from app import discovery_store, projects
    from tests.conftest import start_run

    key = new_project(client)
    proj = as_project(client, key)
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    migration = start_run(proj, {"domain": "d", "operation": "keep-me"}).json()
    proj.post("/api/domains/d/features", json={"key": "drop-me", "name": "Drop me"})
    wait_for(proj, migration["id"], {"awaiting_approval"})

    r = proj.post("/api/domains/d/discover-features", json={"from_scratch": True})
    assert r.status_code == 201, r.text
    run = wait_for(proj, r.json()["id"], {"awaiting_approval"})
    assert run["inputs"]["from_scratch"] is True

    projects.set_current(key)
    brief = discovery_store.domains_brief("d", from_scratch=True)
    assert "keep-me" in brief and "drop-me" not in brief and "already listed" not in brief
    assert "already listed" in discovery_store.domains_brief("d")  # a normal re-list still keeps the list

    # Editing the run's paths keeps it a listing from scratch
    proj.put(f"/api/runs/{run['id']}/inputs", json={"paths": ["src/d/"], "notes": ""})
    assert proj.get(f"/api/runs/{run['id']}").json()["inputs"]["from_scratch"] is True

    doc = next(a for a in proj.get(f"/api/runs/{run['id']}/artifacts").json() if a["name"] == "features.yaml")
    proj.put(f"/api/artifacts/{doc['id']}", json={"content": "features:\n  - {key: new-one, name: New one}\n"})
    assert proj.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    wait_for(proj, run["id"], {"completed"})
    features = {f["key"]: f for f in proj.get("/api/domains/d/features").json()}
    assert set(features) == {"keep-me", "new-one"} and features["keep-me"]["missing_from_latest"]


def test_replacing_with_the_runs_results_overwrites_hand_edits(client):
    proj = as_project(client, new_project(client))
    _approve_contexts(proj, """contexts:
  - {key: loan, name: Loan, description: Loans v1, tables: [LOAN], packages: [com.acme.loan], entry_points: [LoanController]}
""")
    proj.post("/api/domains", json={"key": "legacy", "name": "Legacy", "description": ""})  # not found by the runs
    proj.put("/api/domains/legacy", json={"owned_tables": ["FEE", "KEEP_ME"]})
    proj.put("/api/domains/loan", json={"owned_tables": ["LOAN", "HAND_T"], "description": "Mine",
                                        "agent_inputs": {"domain_features": {"paths": ["src/loan/"], "notes": "n"}}})

    _approve_contexts(proj, """contexts:
  - {key: loan, name: Loan, description: Loans v2, tables: [LOAN, LOAN_NEW], packages: [com.acme.loan2],
     entry_points: [LoanApi]}
  - {key: fees, name: Fees, description: Fees, tables: [FEE]}
""", replace=True)
    loan, fees, legacy = (proj.get(f"/api/domains/{k}").json() for k in ("loan", "fees", "legacy"))
    assert loan["owned_tables"] == ["LOAN", "LOAN_NEW"] and loan["owned_packages"] == ["com.acme.loan2"]
    assert loan["description"] == "Loans v2"
    assert loan["agent_inputs"]["domain_features"] == {"paths": ["LoanApi"], "notes": "n"}  # notes kept
    assert fees["owned_tables"] == ["FEE"]
    assert legacy["owned_tables"] == ["KEEP_ME"]  # FEE moved to fees; the domain itself stays


def test_replace_is_only_for_approving_bounded_contexts(client):
    proj = as_project(client, new_project(client))
    contexts = proj.post("/api/discovery/bounded-contexts").json()
    wait_for(proj, contexts["id"], {"awaiting_approval"})
    r = proj.post(f"/api/runs/{contexts['id']}/decision",
                  json={"decision": "reject", "comments": "again", "replace": True})
    assert r.status_code == 422

    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    listing = proj.post("/api/domains/d/discover-features").json()
    wait_for(proj, listing["id"], {"awaiting_approval"})
    r = proj.post(f"/api/runs/{listing['id']}/decision", json={"decision": "approve", "replace": True})
    assert r.status_code == 422
    assert proj.get(f"/api/runs/{listing['id']}").json()["status"] == "awaiting_approval"  # nothing recorded
