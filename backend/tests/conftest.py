import os
import tempfile
import time

import pytest

# Isolate all state (DB, checkpoints, workspaces) before the app is imported.
os.environ["STUDIO_DATA_DIR"] = tempfile.mkdtemp(prefix="studio-test-")

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import stub  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fast_stubs(monkeypatch):
    monkeypatch.setattr(stub, "STUB_DELAY", 0)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def wait_for(client, run_id, statuses, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run stuck in {run['status']} (wanted {statuses}); error={run.get('error')}")


def start_run(client, body: dict):
    """POST /api/runs for a feature, adding the feature to the domain first (migrations need a listed feature)."""
    client.post(f"/api/domains/{body['domain']}/features", json={"key": body["operation"], "name": body["operation"]})
    return client.post("/api/runs", json=body)
