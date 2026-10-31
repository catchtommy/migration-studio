"""Several people and features at once: runs of different features on their own branches in shared repositories,
two people acting on the same run, the same feature started twice, and design approvals sharing a contract."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app import contracts_store, delivery
from app.db.models import Contract
from app.db.session import session_scope
from tests.conftest import wait_for
from tests.test_delivery import git, setup_project


def _to_first_code_step(proj, feature):
    proj.post("/api/domains/loan/features", json={"key": feature, "name": feature})
    run = proj.post("/api/runs", json={"domain": "loan", "operation": feature}).json()
    return wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)


def _commit_step(proj, run, files):
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    return proj.post(f"/api/runs/{run['id']}/commit", json={
        "repo": "monolith", "message": f"{run['operation']}", "branch": run["branch"], "files": files,
        "latest_sha": changes["latest_sha"], "head_sha": changes["head_sha"]})


def test_two_features_change_the_same_repository_on_their_own_branches(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    a, b = _to_first_code_step(proj, "approve"), _to_first_code_step(proj, "reject")
    assert a["branch"] != b["branch"]
    assert proj.post(f"/api/runs/{a['id']}/branches", json={"feature_branch": a["branch"]}).status_code == 200
    first = wait_for(proj, a["id"], {"awaiting_commit"}, timeout=30)["current_stage"]

    b = proj.get(f"/api/runs/{b['id']}").json()  # each feature on its own: nothing of the other's run
    assert "parallel_runs" not in b["pending_branches"]
    assert a["branch"] == "migration/loan/approve" and b["branch"] == "migration/loan/reject"
    assert proj.post(f"/api/runs/{b['id']}/branches", json={"feature_branch": b["branch"]}).status_code == 200
    wait_for(proj, b["id"], {"awaiting_commit"}, timeout=30)

    with ThreadPoolExecutor(4) as pool:  # both reviews at once: the shared copy of the monolith is fetched for each
        listed = list(pool.map(lambda r: delivery.changes(r["id"], "monolith", None), [a, b, a, b]))
    assert all(f"studio-stub/{first}.txt" in [f["path"] for f in c["files"]] for c in listed)

    for run in (a, b):
        r = _commit_step(proj, run, [f"studio-stub/{first}.txt"])
        assert r.status_code == 200, r.text
    for run in (a, b):  # each branch has its own feature's work, and nothing of the other's
        text = git(upstream, "show", f"{run['branch']}:studio-stub/{first}.txt")
        assert f"loan/{run['operation']}" in text
    assert git(upstream, "rev-parse", f"{a['branch']}^") == git(upstream, "rev-parse", f"{b['branch']}^")  # both on main


def test_two_people_committing_the_same_run_at_once_make_one_commit(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    run = _to_first_code_step(proj, "approve")
    proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": run["branch"]})
    first = wait_for(proj, run["id"], {"awaiting_commit"}, timeout=30)["current_stage"]
    changes = proj.get(f"/api/runs/{run['id']}/changes", params={"repo": "monolith"}).json()
    results, start = [], threading.Barrier(2)

    def commit():
        start.wait()
        try:
            results.append(delivery.commit(run["id"], "monolith", 1, "Same moment", run["branch"],
                                           [f"studio-stub/{first}.txt"], {}, changes["latest_sha"],
                                           changes["head_sha"]))
        except delivery.DeliveryError as e:
            results.append(e)

    threads = [threading.Thread(target=commit) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    pushed = [r for r in results if isinstance(r, dict)]
    assert len(pushed) == 1, results  # the second found nothing left to commit
    assert git(upstream, "rev-parse", run["branch"]) == pushed[0]["sha"]
    assert git(upstream, "rev-list", "--count", f"main..{run['branch']}") == "1"


def test_the_same_feature_started_by_several_people_at_once_gets_one_run(client, tmp_path):
    upstream, key, proj = setup_project(client, tmp_path)
    proj.post("/api/domains/loan/features", json={"key": "approve", "name": "approve"})
    start = threading.Barrier(5)

    def go():
        start.wait()
        return proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).status_code

    with ThreadPoolExecutor(5) as pool:
        codes = sorted(pool.map(lambda _: go(), range(5)))
    assert codes == [201, 409, 409, 409, 409]


def test_two_approvals_adding_the_same_new_contract_keep_both_consumers(client, monkeypatch):
    key = "customer.get-summary-concurrent"
    contract = {"key": key, "provider": "customer", "kind": "api", "name": "Get summary", "version": "1"}
    start, first = threading.Barrier(2), threading.local()

    def record(domain):
        if not getattr(first, "done", False):  # both start together; a retry goes at once
            first.done = True
            start.wait()
        with session_scope() as s:
            contracts_store._upsert_contract(s, "default", "run", contract, contracts_store.PurePosixPath("."),
                                             "proposed", [domain])
        return domain

    def retried(domain):  # as the gate's recording does (contracts_store._retrying)
        return contracts_store._retrying(lambda: record(domain))()

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(retried, ["loan", "payments"])) == ["loan", "payments"]
    with session_scope() as s:
        assert s.get(Contract, ("default", key)).consumers == ["loan", "payments"]
