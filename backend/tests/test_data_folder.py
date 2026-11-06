"""Choosing the data folder: runs store paths relative to it, and it can move, on Windows or Linux."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from app import config, data_folder, delivery, legacy, maintenance, repos
from app.agents import stub
from app.config import settings
from app.db.migrations import _config, upgrade
from app.db.models import Run
from app.db.session import get_engine, session_scope
from app.orchestrator.runner import get_runner
from app.tools import git_ops
from tests.conftest import start_run, wait_for
from tests.test_discovery import _domain


@pytest.fixture
def chooseable(tmp_path, monkeypatch):
    """As without STUDIO_DATA_DIR, and put back afterwards."""
    monkeypatch.setattr(config, "DATA_DIR_FROM_ENV", False)
    monkeypatch.setattr(config, "LOCATION_FILE", tmp_path / "data-location.json")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "current")
    settings.data_dir.mkdir()
    (settings.data_dir / "database.json").write_text("{}", encoding="utf-8")
    (settings.data_dir / "secret.key").write_text("key", encoding="utf-8")


def test_the_database_stores_the_workspace_relative_to_the_data_folder(client, tmp_path, monkeypatch):
    run_id = start_run(client, {"domain": "customer", "operation": "relative"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "completed", "failed"})
    with get_engine().connect() as c:
        stored = c.execute(text("SELECT workspace FROM runs WHERE id = :id"), {"id": run_id}).scalar()
    assert stored == f"workspaces/{run_id}"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with session_scope() as s:
        assert Path(s.get(Run, run_id).workspace) == tmp_path / "workspaces" / run_id


@pytest.mark.parametrize("old", ["/opt/studio/backend/data/workspaces/r1", r"C:\studio\backend\data\workspaces\r1",
                                 r"D:\moved\by\hand\workspaces\r1"])
def test_the_migration_makes_paths_from_any_server_relative(tmp_path, old):
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    with engine.begin() as c:
        command.upgrade(_config(c), "0018")
        c.execute(text("INSERT INTO runs (id, project, domain, operation, status, workspace, created_at, updated_at) "
                       "VALUES ('r1', 'p', 'd', 'o', 'failed', :w, '2026-01-01', '2026-01-01')"), {"w": old})
    upgrade(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT workspace FROM runs")).scalar() == "workspaces/r1"


def test_changing_it_copies_the_data_and_applies_at_once(client, tmp_path, chooseable):
    old, new = settings.data_dir, tmp_path / "new"
    (old / "workspaces" / "r").mkdir(parents=True)
    (old / "workspaces" / "r" / "a.md").write_text("doc", encoding="utf-8")
    r = client.put("/api/data-folder", json={"path": str(new)})
    assert r.status_code == 200, r.text
    assert r.json() == {"path": str(new), "source": "settings"} and settings.data_dir == new
    assert (new / "workspaces" / "r" / "a.md").read_text(encoding="utf-8") == "doc"
    assert (old / "workspaces" / "r" / "a.md").exists(), "the old folder is left for the administrator to delete"
    assert json.loads(config.LOCATION_FILE.read_text(encoding="utf-8")) == {"data_dir": str(new)}
    assert config._chosen_data_dir() == new


def test_a_folder_already_holding_the_data_can_be_used_without_copying(client, tmp_path, chooseable):
    new = tmp_path / "new"
    new.mkdir()
    r = client.put("/api/data-folder", json={"path": str(new), "copy_data": False})
    assert r.status_code == 422 and "database.json" in r.json()["detail"]  # it would lose the database at its next start
    (new / "database.json").write_text("{}", encoding="utf-8")
    (new / "secret.key").write_text("other", encoding="utf-8")
    r = client.put("/api/data-folder", json={"path": str(new), "copy_data": False})
    assert r.status_code == 422 and "secret.key" in r.json()["detail"]
    (new / "secret.key").write_text("key", encoding="utf-8")
    assert client.put("/api/data-folder", json={"path": str(new), "copy_data": False}).status_code == 200
    assert settings.data_dir == new


@pytest.mark.parametrize("path", ["relative/folder", "current/inside"])
def test_unusable_folders_are_refused(client, tmp_path, chooseable, path):
    target = path if path.startswith("relative") else str(tmp_path / path)
    before = settings.data_dir
    assert client.put("/api/data-folder", json={"path": target}).status_code == 422
    assert settings.data_dir == before and not config.LOCATION_FILE.exists()


def test_copying_needs_an_empty_folder(client, tmp_path, chooseable):
    new = tmp_path / "new"
    new.mkdir()
    (new / "other.txt").write_text("x", encoding="utf-8")
    assert client.put("/api/data-folder", json={"path": str(new)}).status_code == 422


def test_set_in_the_environment_it_cannot_be_changed_here(client, tmp_path, chooseable, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR_FROM_ENV", True)
    assert client.get("/api/data-folder").json()["source"] == "env"
    r = client.put("/api/data-folder", json={"path": str(tmp_path / "new")})
    assert r.status_code == 422 and "STUDIO_DATA_DIR" in r.json()["detail"]


def test_a_shared_copy_of_the_monolith_works_after_the_folder_moved(tmp_path, monkeypatch):
    """Its git worktree link holds absolute paths, whether the folder was moved from Settings or by hand."""
    import shutil

    data = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data)
    source = tmp_path / "source"
    source.mkdir()
    git_ops.git(source, "init", "--quiet")
    (source / "a.txt").write_text("a", encoding="utf-8")
    git_ops.git(source, "add", ".")
    git_ops.git(source, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first")
    sha = git_ops.git(source, "rev-parse", "HEAD").strip()
    mirror = settings.repos_dir / "p" / "m.git"
    mirror.parent.mkdir(parents=True)
    git_ops.git(tmp_path, "clone", "--quiet", "--bare", str(source), str(mirror))
    legacy._make("p", mirror, sha)

    moved = tmp_path / "moved"
    shutil.move(str(data), str(moved))
    monkeypatch.setattr(settings, "data_dir", moved)
    copy = legacy.copy_dir("p", sha)
    with pytest.raises(git_ops.GitError):
        git_ops.git(copy, "rev-parse", "HEAD")
    legacy.repair_moved()
    assert git_ops.git(copy, "rev-parse", "HEAD").strip() == sha


def _features_run(client, label):
    r = client.post(f"/api/domains/{_domain(client, label)}/discover-features")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _stopped(client, run_id):
    return wait_for(client, run_id, {"awaiting_approval", "awaiting_branches", "awaiting_commit", "completed", "failed"})


def _fail_first_attempt(monkeypatch):
    original, failed = stub.StubAgent.run, []

    def run(self, ctx):
        if not failed:
            failed.append(self.stage)
            raise RuntimeError("boom")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)


@pytest.mark.parametrize("damage", ["whole folder", "only a stub of .git left"])
def test_a_domain_features_retry_rebuilds_a_damaged_workspace(client, monkeypatch, damage):
    _fail_first_attempt(monkeypatch)
    run_id = _features_run(client, "wsdamage")
    run = _stopped(client, run_id)
    assert run["status"] == "failed"
    ws = Path(run["workspace"])
    git_ops.remove_tree(ws)
    if damage != "whole folder":
        (ws / ".git" / "info").mkdir(parents=True)
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
    run = _stopped(client, run_id)
    assert run["status"] != "failed", run["error"]
    assert git_ops.has_commits(ws)


def test_a_workspace_lost_just_before_a_step_is_rebuilt(client, monkeypatch):
    """Lost after the run was picked up, so only the step itself can notice."""
    original, lost = delivery.confirmation_needed, []

    def lose_it(run_id, stage):
        if not lost:
            lost.append(stage)
            with session_scope() as s:
                git_ops.remove_tree(Path(s.get(Run, run_id).workspace))
        return original(run_id, stage)

    monkeypatch.setattr(delivery, "confirmation_needed", lose_it)
    run_id = _features_run(client, "wslost")
    run = _stopped(client, run_id)
    assert lost and run["status"] != "failed", run["error"]


def test_nothing_starts_while_the_folder_is_changing(client, tmp_path, chooseable, monkeypatch):
    """Runs started during the copy would write to the old folder, and be lost."""
    copy, seen = data_folder._copy, {}
    key = _domain(client, "wsheld")

    def copy_and_try(old, new):
        seen["start"] = client.post(f"/api/domains/{key}/discover-features")
        seen["fetch"] = client.post("/api/projects/default/repository/fetch")
        seen["read"] = client.get("/api/runs")
        copy(old, new)

    monkeypatch.setattr(data_folder, "_copy", copy_and_try)
    r = client.put("/api/data-folder", json={"path": str(tmp_path / "new")})
    assert r.status_code == 200, r.text
    for attempt in ("start", "fetch"):
        assert seen[attempt].status_code == 409 and "being changed" in seen[attempt].json()["detail"]
    assert seen["read"].status_code == 200
    assert client.get(f"/api/domains/{key}/features").json() == []
    assert not [run for run in client.get("/api/runs").json() if run["domain"] == key], "no run row left behind"


def test_changes_are_accepted_again_after_a_refused_change(client, tmp_path, chooseable):
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "other.txt").write_text("x", encoding="utf-8")
    assert client.put("/api/data-folder", json={"path": str(tmp_path / "new")}).status_code == 422
    assert client.post("/api/domains", json={"key": "wsafter", "name": "After"}).status_code == 201


def test_it_waits_for_changes_in_progress_and_gives_up_on_slow_ones():
    request = SimpleNamespace(method="POST")
    in_progress = maintenance.guard(request)
    next(in_progress)
    with pytest.raises(maintenance.Busy):
        with maintenance.hold("moving", wait=0.05):
            pass
    next(in_progress, None)  # it finishes
    with maintenance.hold("moving", wait=0.05):
        with pytest.raises(HTTPException) as refused:
            next(maintenance.guard(request))
        assert refused.value.status_code == 409 and refused.value.detail == "moving"
        assert next(maintenance.guard(SimpleNamespace(method="GET")), "read") is None


def test_it_is_refused_while_a_run_or_a_fetch_is_working(client, tmp_path, chooseable, monkeypatch):
    monkeypatch.setattr(repos, "is_fetching", lambda: True)
    r = client.put("/api/data-folder", json={"path": str(tmp_path / "new")})
    assert r.status_code == 409 and "fetched" in r.json()["detail"]
    monkeypatch.setattr(repos, "is_fetching", lambda: False)
    monkeypatch.setattr(get_runner(), "has_active", lambda: True)
    r = client.put("/api/data-folder", json={"path": str(tmp_path / "new")})
    assert r.status_code == 409 and "run(s) are working" in r.json()["detail"]
