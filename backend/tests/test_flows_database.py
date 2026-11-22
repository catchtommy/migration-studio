"""Choosing the database (setup screen and Settings → Database): the form's checks, testing a connection, and
switching to another database with the data copied over, and back."""

from sqlalchemy import func, select

from app.db import config as db_config
from app.db import session
from app.db.models import Run


def test_the_form_asks_for_what_a_server_database_needs(client):
    r = client.post("/api/database/test", json={"dialect": "postgresql", "host": "", "database": "", "username": ""})
    assert r.status_code == 422 and r.json()["detail"] == "Fill in the host, database, username"
    r = client.post("/api/database/test", json={"dialect": "oracle", "host": "db", "username": "u"})
    assert r.status_code == 422 and "service name" in r.json()["detail"]
    for host in ("db.example.com:1521", "user@db", "jdbc:oracle:thin:@db"):  # the port and the rest have own fields
        r = client.post("/api/database/test", json={"dialect": "oracle", "host": host, "database": "ORCL",
                                                    "username": "u"})
        assert r.status_code == 422 and "only the server name" in r.json()["detail"], host


def test_a_connection_is_tested_and_failures_are_explained(client, tmp_path):
    ok = client.post("/api/database/test", json={"dialect": "sqlite", "database": str(tmp_path / "t.db")})
    assert ok.status_code == 200 and ok.json()["version"].startswith("SQLite")
    down = client.post("/api/database/test", json={"dialect": "postgresql", "host": "127.0.0.1", "port": 1,
                                                    "database": "studio", "username": "u", "password": "p"})
    assert down.status_code == 422 and down.json()["detail"].startswith("Could not connect")


def test_a_saved_password_is_kept_for_the_same_server_and_user(tmp_path, monkeypatch):
    from app.api.database_api import DatabaseIn, _config

    saved = db_config.DatabaseConfig(dialect="postgresql", host="db", port=5432, database="studio", username="u",
                                     password="kept")
    monkeypatch.setattr(db_config, "load", lambda: saved)
    same = _config(DatabaseIn(dialect="postgresql", host="db", port=5432, database="studio", username="u"))
    assert same.password == "kept"
    other = _config(DatabaseIn(dialect="postgresql", host="db", port=5432, database="studio", username="someone"))
    assert other.password == ""


def test_switching_databases_copies_the_data_and_can_switch_back(client, tmp_path, monkeypatch):
    monkeypatch.setattr(db_config, "from_env", lambda: False)  # the tests' database is otherwise set by the environment
    original = session.get_engine().url
    with session.session_scope() as s:
        runs = s.scalar(select(func.count()).select_from(Run))
    target = tmp_path / "other.db"
    try:
        r = client.post("/api/database", json={"config": {"dialect": "sqlite", "database": str(target)},
                                               "copy_data": True})
        assert r.status_code == 200, r.text
        assert r.json()["copied_rows"] > 0 and session.get_engine().url.database == str(target)
        with session.session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Run)) == runs  # everything came along
        assert client.get("/api/runs").status_code == 200  # and the studio works on it
        status = client.get("/api/database").json()
        assert status["source"] == "file" and status["config"]["database"] == str(target)
    finally:
        back = client.post("/api/database", json={"config": {"dialect": "sqlite", "database": original.database}})
        assert back.status_code == 200, back.text
    assert session.get_engine().url.database == original.database


def test_the_database_set_by_the_environment_cant_be_switched_here(client, tmp_path):
    r = client.post("/api/database", json={"config": {"dialect": "sqlite", "database": str(tmp_path / "x.db")}})
    assert r.status_code == 409 and "STUDIO_DATABASE_URL" in r.json()["detail"]
