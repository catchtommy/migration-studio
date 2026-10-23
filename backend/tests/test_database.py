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


@pytest.fixture
def code_steps(client):
    """Every agent step of the standard workflow set to change code (this project's copy, dropped afterwards)."""
    wf = client.get("/api/workflows/standard").json()
    steps = [{**st, "code": {"repository": ""}} if st["kind"] == "agent" else st for st in wf["steps"]]
    assert client.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    yield
    assert client.post("/api/workflows/standard/revert").status_code == 200


def code_writing_stub(monkeypatch):
    """Stub agents that also change code outside the documents folder, as real code-changing agents do (only steps
    set to change code may; see the code_steps fixture)."""
    original = stub.StubAgent.run

    def run(self, ctx):
        f = ctx.workspace / "src" / f"{self.stage}.java"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"class {self.stage} {{ int attempt = {ctx.attempt}; }}\n")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)


def test_every_document_and_the_stage_diff_are_stored(client, monkeypatch, code_steps):
    code_writing_stub(monkeypatch)
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    docs = stored_documents(run_id)
    ws = Path(run["workspace"])
    assert docs and not (ws / ".migration").exists()  # documents are in the database only
    ex = next(s for s in run["stages"] if s["stage"] == "deep_dive")
    diff = client.get(f"/api/runs/{run_id}/stages/{ex['id']}/diff").json()["diff"]
    assert "src/deep_dive.java" in diff
    assert f"b/.migration/loan/{run['operation']}/deep_dive.md" in diff  # document changes, from the stored versions

    shutil.rmtree(ws)  # the diff is served from the database, not the workspace
    assert client.get(f"/api/runs/{run_id}/stages/{ex['id']}/diff").json()["diff"] == diff


def test_lost_workspace_is_rebuilt_for_a_rejection(client, monkeypatch, code_steps):
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
    from app.workspace import effective_documents
    assert "Rebuild me" in effective_documents(run_id)[f".migration/loan/{run['operation']}/domain_dependencies.md"]
    assert not (ws / ".migration").exists()


def test_reviewer_edit_survives_a_rebuild(client):
    run_id = new_run(client)
    run = wait_for(client, run_id, {"awaiting_approval"})
    art = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["stage"] == "deep_dive")
    assert client.put(f"/api/artifacts/{art['id']}", json={"content": "# Edited\n"}).status_code == 200
    ws = Path(run["workspace"])
    shutil.rmtree(ws)

    seen = {}
    real_run = stub.StubAgent.run

    def run_and_look(self, ctx):  # what the next stage finds in its workspace
        seen.setdefault(ctx.stage, (ctx.workspace / art["path"]).read_text())
        return real_run(self, ctx)

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(stub.StubAgent, "run", run_and_look)
    try:
        decide(client, run_id, decision="approve")  # the next stage starts from a rebuilt workspace
        wait_for(client, run_id, {"awaiting_approval"}, timeout=60)
    finally:
        mp.undo()
    assert set(seen.values()) == {"# Edited\n"}  # the edited document, from the database


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
        assert s.get(Domain, ("default", "emptydesc")).description == ""


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


def test_oracle_old_password_verifier_is_explained_and_thick_mode_can_be_configured(client, tmp_path):
    from app.db import config as db_config

    message = db_config._explain("DPY-3015: password verifier type 0x939 is not supported by python-oracledb in thin mode")
    assert "reset the password" in message and "Instant Client" in message
    assert db_config._explain("ORA-01017: invalid username/password") == "ORA-01017: invalid username/password"

    # Saved settings from before the option still load; the folder is checked before connecting.
    old = db_config.DatabaseConfig(**{"dialect": "oracle", "host": "db", "port": 1521, "database": "PDB",
                                      "username": "u", "password": "p", "options": {}})
    assert old.oracle_client == ""
    r = client.post("/api/database/test", json={"dialect": "oracle", "host": "db.invalid", "database": "PDB",
                                                "username": "u", "password": "p",
                                                "oracle_client": str(tmp_path / "no-such-folder")})
    assert r.status_code == 422 and "no Oracle Instant Client folder" in r.json()["detail"]


def test_a_connection_string_in_the_host_field_is_refused_clearly(client):
    r = client.post("/api/database/test", json={"dialect": "oracle", "host": "jdbc:oracle:thin:@//db:1521/PDB",
                                                "database": "PDB", "username": "u", "password": "p"})
    assert r.status_code == 422 and "only the server name" in r.json()["detail"]


def test_boolean_filters_are_valid_on_oracle():
    """Oracle has no boolean type: `IS TRUE` / `IS NOT TRUE` become `IS 1` / `IS NOT 1`, which it rejects (ORA-00908).
    Booleans are compared with = instead, and 'not true' is written as IS NULL OR = 0."""
    import re
    from sqlalchemy import select
    from sqlalchemy.dialects import oracle

    from app.db.models import StageExecution
    from app.workspace import _not_true
    pattern = re.compile(r"\.(is_|is_not|isnot)\((True|False)\)")
    offenders = [f"{p}:{n}" for p in Path("app").rglob("*.py")
                 for n, line in enumerate(p.read_text().splitlines(), 1) if pattern.search(line)]
    assert not offenders, offenders
    sql = str(select(StageExecution.id).where(_not_true(StageExecution.rerun)).compile(dialect=oracle.dialect()))
    assert "IS NOT 1" not in sql and "rerun = 0" in sql


def test_no_query_compares_a_long_text_column(client):
    """Oracle stores long text (LongText, NonNullText, JSONText) as an NCLOB, and an NCLOB can't be compared in SQL
    (=, !=, IN, LIKE: ORA-00932), which SQLite and PostgreSQL allow, so tests would not notice. IS NULL is fine."""
    import re
    from pathlib import Path

    from app.db import models
    from app.db.types import JSONText, LongText, NonNullText

    lob = [f"{cls.__name__}.{col.key}" for cls in models.Base.__subclasses__() if hasattr(cls, "__table__")
           for col in cls.__table__.columns if isinstance(col.type, (LongText, NonNullText, JSONText))]
    assert "Question.question" in lob and "Artifact.content" in lob
    pattern = re.compile(r"\b(" + "|".join(re.escape(c) for c in lob) +
                         r")\s*(==|!=|\.in_\(|\.not_in\(|\.like\(|\.ilike\(|\.contains\(|\.startswith\(|\.endswith\()")
    found = [f"{p.relative_to(Path('app').parent)}:{n}: {line.strip()}"
             for p in sorted(Path("app").rglob("*.py")) for n, line in enumerate(p.read_text().splitlines(), 1)
             if pattern.search(line)]
    assert not found, "Long-text columns compared in SQL (fails on Oracle):\n" + "\n".join(found)


def test_an_agent_asking_the_same_question_again_gets_the_earlier_answer(client):
    """The earlier answer is found without comparing the question text in SQL (see the test above)."""
    from app.db.models import Question, Run
    from app.orchestrator import questions

    run_id = start_run(client, {"domain": "loan", "operation": f"ask{next(_ids)}"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    text = "Which domain owns BANK_ACCOUNT? " + "It is written by two DAOs. " * 40  # long, like real questions
    with session.session_scope() as s:
        s.add(Question(run_id=run_id, stage="deep_dive", attempt=1, question=text, status="answered",
                       answer="Payments owns it"))
    assert questions.ask(run_id, "deep_dive", 2, f"  {text}  ") == "Payments owns it"
    with session.session_scope() as s:  # answered from before: no new question, the run isn't held up
        assert s.query(Question).filter(Question.run_id == run_id).count() == 1
        assert s.get(Run, run_id).status != "awaiting_input"
