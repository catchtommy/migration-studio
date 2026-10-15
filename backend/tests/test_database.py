import itertools
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from app.agents import stub
from app.db import migrations, session, transfer
from app.db.models import Artifact, Domain, GraphCheckpoint
from app.orchestrator.checkpointer import SqlAlchemySaver
from app.tools import git_ops
from tests.conftest import start_run, wait_for

_ids = itertools.count()


def new_run(client, domain="loan"):
    r = start_run(client, {"domain": domain, "operation": f"dbop{next(_ids)}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def decide(client, run_id, **body):
    r = client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "tester", **body})
    assert r.status_code == 200, r.text


def stored_documents(run_id):
    """Latest stored content per document path."""
    with session.session_scope() as s:
        return {a.path: a.content for a in s.scalars(select(Artifact).where(Artifact.run_id == run_id)
                                                     .order_by(Artifact.id))}


def code_writing_stub(monkeypatch):
    """Stub agents that also change code outside the documents folder, as real agents do."""
    original = stub.StubAgent.run

    def run(self, ctx):
        f = ctx.workspace / "src" / f"{self.stage}.java"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"class {self.stage} {{ int attempt = {ctx.attempt}; }}\n")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)


def test_every_document_and_the_stage_diff_are_stored(client, monkeypatch):
    code_writing_stub(monkeypatch)
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    docs = stored_documents(run_id)
    ws = Path(run["workspace"])
    for path, content in docs.items():
        assert (ws / path).read_text() == content
    ex = next(s for s in run["stages"] if s["stage"] == "deep_dive")
    diff = client.get(f"/api/runs/{run_id}/stages/{ex['id']}/diff").json()["diff"]
    assert "src/deep_dive.java" in diff

    shutil.rmtree(ws)  # the diff is served from the database, not the workspace
    assert client.get(f"/api/runs/{run_id}/stages/{ex['id']}/diff").json()["diff"] == diff


def test_lost_workspace_is_rebuilt_for_a_rejection(client, monkeypatch):
    code_writing_stub(monkeypatch)
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    decide(client, run_id, decision="approve")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    ws = Path(run["workspace"])
    shutil.rmtree(ws)

    decide(client, run_id, decision="reject", comments="Rebuild me", target_stage="domain_dependencies")
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    log = git_ops.git(ws, "log", "--format=%s")
    assert "[restore]" in log
    # Code from the stages before the target was replayed; the re-run stages wrote theirs again.
    assert (ws / "src" / "as_is_doc.java").read_text() == "class as_is_doc { int attempt = 1; }\n"
    assert (ws / "src" / "domain_dependencies.java").read_text() == "class domain_dependencies { int attempt = 2; }\n"
    docs = stored_documents(run_id)
    for path, content in docs.items():
        assert (ws / path).read_text() == content
    assert "Rebuild me" in docs[f".migration/loan/{run['operation']}/domain_dependencies.md"]


def test_reviewer_edit_survives_a_rebuild(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    art = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["stage"] == "deep_dive")
    assert client.put(f"/api/artifacts/{art['id']}", json={"content": "# Edited\n"}).status_code == 200
    ws = Path(run["workspace"])
    shutil.rmtree(ws)

    decide(client, run_id, decision="approve")  # the next stage starts from a rebuilt workspace
    wait_for(client, run_id, {"awaiting_approval"})
    assert (ws / art["path"]).read_text() == "# Edited\n"


def test_checkpoints_are_in_the_database_and_deleted_with_the_run(client):
    run_id = new_run(client)
    wait_for(client, run_id, {"awaiting_approval"})
    with session.session_scope() as s:
        assert s.scalar(select(GraphCheckpoint).where(GraphCheckpoint.thread_id == run_id)) is not None
    saver = SqlAlchemySaver()
    assert saver.get_tuple({"configurable": {"thread_id": run_id}}) is not None
    assert len(list(saver.list({"configurable": {"thread_id": run_id}}, limit=2))) == 2
    saver.delete_thread(run_id)
    assert saver.get_tuple({"configurable": {"thread_id": run_id}}) is None


def test_empty_text_reads_back_as_empty(client):
    r = client.post("/api/domains", json={"key": "emptydesc", "name": "Empty", "description": ""})
    assert r.status_code in (200, 201), r.text
    with session.session_scope() as s:
        assert s.get(Domain, "emptydesc").description == ""


def test_copy_all_moves_every_table(client, tmp_path):
    new_run(client)
    target = create_engine(f"sqlite:///{tmp_path / 'copy.db'}")
    migrations.upgrade(target)
    counts = transfer.copy_all(session.get_engine(), target)
    assert counts["runs"] >= 1 and counts["agents"] >= 1 and counts["graph_checkpoints"] >= 1
    with pytest.raises(transfer.TargetNotEmpty):
        transfer.copy_all(session.get_engine(), target)
    assert transfer.copy_all(session.get_engine(), target, replace=True)["runs"] == counts["runs"]
    target.dispose()


def test_setup_mode_until_a_database_is_chosen(client, monkeypatch):
    monkeypatch.setattr(session, "_engine", None)
    r = client.get("/api/runs")
    assert r.status_code == 503
    assert r.json()["detail"]["setup_required"] is True
    assert client.get("/api/database").json()["configured"] is False
    assert client.get("/api/health").status_code == 200


def test_database_status_and_connection_test(client, tmp_path):
    status = client.get("/api/database").json()
    assert status["configured"] is True and status["source"] == "env"
    assert "password" not in (status["config"] or {})
    r = client.post("/api/database/test", json={"dialect": "sqlite", "database": str(tmp_path / "t.db")})
    assert r.status_code == 200 and r.json()["version"].startswith("SQLite")
    r = client.post("/api/database/test", json={"dialect": "postgresql", "host": "127.0.0.1", "port": 1,
                                                "database": "x", "username": "x"})
    assert r.status_code == 422 and "Could not connect" in r.json()["detail"]
    # Set by the environment (as in this test run): it can't be switched from the UI.
    r = client.post("/api/database", json={"config": {"dialect": "sqlite", "database": str(tmp_path / "t.db")}})
    assert r.status_code == 409
