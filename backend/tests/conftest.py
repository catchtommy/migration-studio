import os
import tempfile
import time

import pytest

# Isolate all state (DB, workspaces) before the app is imported. STUDIO_TEST_DATABASE_URL runs the suite against an
# empty PostgreSQL or Oracle database instead of SQLite.
os.environ["STUDIO_DATA_DIR"] = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_DATABASE_URL"] = (os.environ.get("STUDIO_TEST_DATABASE_URL") or
                                     f"sqlite:///{os.environ['STUDIO_DATA_DIR']}/studio.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import projects  # noqa: E402
from app.agents import stub  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fast_stubs(monkeypatch):
    monkeypatch.setattr(stub, "STUB_DELAY", 0)


@pytest.fixture(autouse=True)
def default_project():
    """Store functions called directly by tests work in the default project, like the API calls do."""
    with projects.use(projects.DEFAULT):
        yield


ADMIN_PASSWORD = "admin-password"


@pytest.fixture(scope="session")
def client():
    """Signed in as the first admin, created through the bootstrap screen's API."""
    with TestClient(app, headers={"X-Project": "default"}) as c:
        r = c.post("/api/auth/bootstrap", json={"username": "admin", "display_name": "Test Admin",
                                                "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        from app import settings_store
        settings_store.update_project(projects.DEFAULT, {"base_package_prefix": "com.acme"})  # names the domains' packages
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


class Document:
    """A run's document as the run has it now (documents live in the database, not in the workspace)."""

    def __init__(self, run_id: str, path: str):
        self.run_id, self.path = run_id, path

    def _content(self):
        from app.workspace import effective_documents
        return effective_documents(self.run_id).get(self.path)

    def read_text(self) -> str:
        content = self._content()
        if content is None:
            raise FileNotFoundError(self.path)
        return content

    def exists(self) -> bool:
        return self._content() is not None

    is_file = exists


def document(run: dict, stage: str, name: str | None = None) -> Document:
    return Document(run["id"], f".migration/{run['domain']}/{run['operation']}/{name or stage + '.md'}")
