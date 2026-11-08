"""What resetting a feature (or otherwise deleting runs) does to the contracts those runs recorded."""

import yaml

from app import projects, run_cleanup
from app.contracts_store import DECISIONS_FILE, LINKED_CONTRACTS_FILE
from app.db.models import Artifact, Contract
from app.db.session import session_scope
from tests.conftest import start_run, wait_for
from tests.test_discovery import _domain


def _started(client, domain, feature):
    run_id = start_run(client, {"domain": domain, "operation": feature}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "failed"}, 30)
    return run_id


def _contract(key, run_id, status="approved", provider="x"):
    with projects.use(projects.DEFAULT), session_scope() as s:
        s.add(Contract(project=projects.DEFAULT, key=key, provider=provider, kind="event", name=key, status=status,
                       consumers=["auditing"], run_id=run_id))


def _uses(run_id, key, name=DECISIONS_FILE):
    with projects.use(projects.DEFAULT), session_scope() as s:
        s.add(Artifact(run_id=run_id, stage="design", attempt=1, name=name, path=f"design/{name}", kind="yaml",
                       content=yaml.safe_dump({"contracts": [{"key": key}]})))


def _keys(*keys):
    with projects.use(projects.DEFAULT), session_scope() as s:
        return {k for k in keys if s.get(Contract, (projects.DEFAULT, k)) is not None}


def test_a_reset_removes_the_contracts_its_runs_recorded_that_nothing_else_uses(client):
    domain = _domain(client, "ctr")
    mine, other = _started(client, domain, "view"), _started(client, domain, "close")
    keep = {f"{domain}.used-elsewhere", f"{domain}.linked-elsewhere", f"{domain}.provided", f"{domain}.deprecated",
            f"{domain}.of-another-run"}
    _contract(f"{domain}.view-completed", mine)
    _contract(f"{domain}.view-proposed", mine, status="proposed")
    _contract(f"{domain}.used-elsewhere", mine)
    _uses(other, f"{domain}.used-elsewhere")  # another run's design relies on it
    _contract(f"{domain}.linked-elsewhere", mine)
    _uses(other, f"{domain}.linked-elsewhere", LINKED_CONTRACTS_FILE)
    _contract(f"{domain}.provided", mine, status="provided-by-service")  # built already
    _contract(f"{domain}.deprecated", mine, status="deprecated")
    _contract(f"{domain}.of-another-run", other)

    assert client.post(f"/api/domains/{domain}/features/view/reset").status_code == 200
    assert _keys(f"{domain}.view-completed", f"{domain}.view-proposed", *keep) == keep


def test_contracts_left_by_runs_deleted_earlier_are_removed_once(client):
    domain = _domain(client, "orph")
    alive = _started(client, domain, "view")
    _contract(f"{domain}.orphan", "gone-run")
    _contract(f"{domain}.orphan-proposed", "gone-run-2", status="proposed")
    _contract(f"{domain}.orphan-used", "gone-run")
    _uses(alive, f"{domain}.orphan-used")
    _contract(f"{domain}.orphan-provided", "gone-run", status="provided-by-monolith")
    _contract(f"{domain}.run-alive", alive)
    _contract(f"{domain}.no-run", None)
    keep = {f"{domain}.orphan-used", f"{domain}.orphan-provided", f"{domain}.run-alive", f"{domain}.no-run"}

    assert run_cleanup.remove_orphan_contracts() >= 2
    assert _keys(f"{domain}.orphan", f"{domain}.orphan-proposed", *keep) == keep
    assert run_cleanup.remove_orphan_contracts() == 0
