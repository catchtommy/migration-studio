"""Pages stay fast on a remote database (each statement there is a network round trip): requests don't re-read what
rarely changes (sign-in, permissions, settings, agents, workflows), the run page reads a fixed number of statements
however long the run is, and what is cached is read again as soon as it changes."""

import itertools
import re
from contextlib import contextmanager

from sqlalchemy import event, select, update

from app import agent_store, auth, settings_store, workflow_store
from app.db.models import ProjectMember, User
from app.db.session import emitted, emit, get_engine, session_scope
from tests.conftest import start_run, wait_for

_ids = itertools.count()


@contextmanager
def statements():
    seen: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def reads_of(seen: list[str], table: str) -> list[str]:
    return [s for s in seen if re.search(rf"\bFROM {table}\b", s, re.IGNORECASE)]


def test_signed_in_requests_dont_reread_the_session_or_membership(client):
    client.get("/api/domains")  # warms the caches
    with statements() as seen:
        assert client.get("/api/domains").status_code == 200
    for table in ("user_sessions", "users", "project_members", "projects", "roles"):
        assert not reads_of(seen, table), (table, seen)


def test_run_page_reads_a_fixed_number_of_statements(client):
    r = start_run(client, {"domain": "customer", "operation": f"budget{next(_ids)}"})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    client.get(f"/api/runs/{run_id}")  # warms the caches
    with statements() as seen:
        run = client.get(f"/api/runs/{run_id}").json()
    assert run["last_event_id"] > 0
    # run, its attempts, approvals, newest event, coverage documents, checkpoint and its writes, checklist attempt
    assert len(seen) <= 12, "\n".join(seen)
    assert not any(re.search(r"stage_executions\.(diff|prompt)\b", s) for s in seen)

    with statements() as seen:
        assert client.get(f"/api/runs/{run_id}/artifacts").status_code == 200
    assert not any("artifacts.content" in s for s in seen), seen  # the list never shows the documents


def test_new_events_are_signalled_to_the_activity_stream(client):
    r = start_run(client, {"domain": "customer", "operation": f"budget{next(_ids)}"})
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    before = emitted(run_id)
    emit(run_id, "log", "something happened")
    assert emitted(run_id) == before + 1


def test_cached_settings_agents_and_workflows_follow_their_changes(client):
    project = "default"
    old = settings_store.get_project(project)["monolith_docs_dir"]
    settings_store.update_project(project, {"monolith_docs_dir": "docs-changed"})
    assert settings_store.get_project(project)["monolith_docs_dir"] == "docs-changed"
    settings_store.update_project(project, {"monolith_docs_dir": old})
    assert settings_store.get_project(project)["monolith_docs_dir"] == old

    key = "as_is_doc"  # a step of the standard workflow
    title = agent_store.get(key)["title"]
    agent_store.titles()
    agent_store.update(key, {"title": "Renamed step"})
    assert agent_store.get(key)["title"] == "Renamed step"
    assert agent_store.titles()[key][0] == "Renamed step"
    wf = workflow_store.get(workflow_store.default_for(None))
    assert any(s["title"] == "Renamed step" for s in wf["stages"])  # titles are part of a cached workflow
    agent_store.update(key, {"title": title})
    assert agent_store.get(key)["title"] == title

    # what callers get is their own copy: changing it changes nothing cached
    wf["name"] = "mutated"
    assert workflow_store.get(wf["key"])["name"] != "mutated"


def test_sign_out_deactivation_and_role_changes_take_effect_at_once(client):
    with session_scope() as s:
        admin = s.scalar(select(User).where(User.username == "admin"))
        admin_id = admin.id
    token = auth.start_session(admin_id)
    assert auth.user_for_token(token) is not None
    auth.end_session(token)
    assert auth.user_for_token(token) is None

    token = auth.start_session(admin_id)
    assert auth.user_for_token(token) is not None
    with session_scope() as s:
        s.get(User, admin_id).active = False
    try:
        assert auth.user_for_token(token) is None
    finally:
        with session_scope() as s:
            s.get(User, admin_id).active = True
    assert auth.user_for_token(token) is not None

    user = auth.user_for_token(token)
    granted = auth.project_permissions(user, "default")
    assert granted is not None and "runs.start" in granted
    with session_scope() as s:
        old_role = s.get(ProjectMember, ("default", admin_id)).role
    try:
        with session_scope() as s:  # a bulk update (it never shows up in a flush): seen all the same
            s.execute(update(ProjectMember).where(ProjectMember.project == "default", ProjectMember.user_id == admin_id)
                      .values(role="viewer"))
        assert "runs.start" not in auth.project_permissions(user, "default")
    finally:
        with session_scope() as s:
            s.get(ProjectMember, ("default", admin_id)).role = old_role
    assert auth.project_permissions(user, "default") == granted
    auth.end_session(token)


def test_responses_say_how_much_database_work_they_did(client):
    r = client.get("/api/domains")
    timing = r.headers["server-timing"]
    assert re.match(r'db;dur=\d+;desc="\d+ statements", app;dur=\d+', timing), timing
