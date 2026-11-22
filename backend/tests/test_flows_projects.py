"""Projects, members, global settings and workflows: what the Projects, Project settings, Settings and Workflows pages
let people do, and what they refuse."""

import itertools

from app.db.models import Domain, Feature, Project, ProjectMember, Setting, WorkflowDef
from app.db.session import session_scope
from tests.test_auth import make_user
from tests.test_projects import as_project, new_project, signed_in

_ids = itertools.count()


# --- projects ------------------------------------------------------------------------------------------------------

def test_a_project_key_is_checked_and_used_once(client):
    for key in ("A", "-dash", "_shared", "x"):
        r = client.post("/api/projects", json={"key": key, "name": "Bad"})
        assert r.status_code == 422, (key, r.text)
    key = new_project(client)
    assert client.post("/api/projects", json={"key": key, "name": "Again"}).status_code == 409


def test_a_project_with_invalid_settings_is_not_left_half_made(client):
    key = f"half-{next(_ids)}"
    r = client.post("/api/projects", json={"key": key, "name": "Half", "settings": {"no_such_setting": 1}})
    assert r.status_code == 422
    with session_scope() as s:
        assert s.get(Project, key) is None
        assert s.query(ProjectMember).filter(ProjectMember.project == key).count() == 0


def test_a_project_is_renamed_by_those_who_manage_it_only(client):
    key = new_project(client)
    r = client.put(f"/api/projects/{key}", json={"name": "  Renamed  ", "description": " about "})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["description"] == "about"
    developer = make_user(client, ["developer"])
    client.put(f"/api/projects/{key}/members/{developer['id']}", json={"role": "developer"})
    r = signed_in(developer["username"], key).put(f"/api/projects/{key}", json={"name": "Mine"})
    assert r.status_code == 403 and "Manage project" in r.json()["detail"]


def test_members_need_a_known_user_and_role_and_can_be_removed(client):
    key = new_project(client)
    user = make_user(client, ["viewer"])
    assert client.put(f"/api/projects/{key}/members/999999", json={"role": "viewer"}).status_code == 404
    assert client.put(f"/api/projects/{key}/members/{user['id']}", json={"role": "ghost"}).status_code == 422
    members = client.put(f"/api/projects/{key}/members/{user['id']}", json={"role": "reviewer"}).json()
    assert any(m["user_id"] == user["id"] and m["role"] == "reviewer" for m in members)
    assert client.delete(f"/api/projects/{key}/members/{user['id']}").status_code == 204
    assert all(m["user_id"] != user["id"] for m in client.get(f"/api/projects/{key}/members").json())


def test_fetching_a_project_without_a_repository_is_refused(client):
    key = new_project(client)
    r = client.post(f"/api/projects/{key}/repository/fetch")
    assert r.status_code == 422 and "monolith repository" in r.json()["detail"]


def test_deleting_a_project_removes_everything_it_holds(client):
    key = new_project(client)
    proj = as_project(client, key)
    assert proj.post("/api/domains", json={"key": "loan", "name": "Loan"}).status_code == 201
    assert proj.post("/api/domains/loan/features", json={"key": "approve", "name": "Approve"}).status_code == 201
    assert proj.post("/api/workflows?library=project", json={"key": f"wf-{next(_ids)}", "name": "Mine",
                                                             "copy_from": "standard"}).status_code == 201
    assert client.delete(f"/api/projects/{key}").status_code == 204
    with session_scope() as s:
        for model in (Domain, Feature, WorkflowDef, Setting, ProjectMember):
            assert s.query(model).filter(model.project == key).count() == 0, model.__name__
        assert s.get(Project, key) is None
    assert client.get(f"/api/projects/{key}").status_code == 404
    assert client.delete(f"/api/projects/{key}").status_code == 404


# --- global settings -----------------------------------------------------------------------------------------------

def test_unknown_global_settings_are_refused(client):
    r = client.put("/api/settings", json={"no_such_setting": True})
    assert r.status_code == 422


def test_changing_the_network_settings_restarts_the_copilot_runtime(client, monkeypatch):
    from app.api import settings_api

    restarted = []
    monkeypatch.setattr(settings_api, "get_service", lambda: type("S", (), {"restart": lambda self: restarted.append(1)})())
    before = client.get("/api/settings").json()["no_proxy"]
    try:
        assert client.put("/api/settings", json={"no_proxy": "localhost,.example.com"}).status_code == 200
        assert restarted == [1]
        client.put("/api/settings", json={"ai_provider": client.get("/api/settings").json()["ai_provider"]})
        assert restarted == [1]  # not for other settings
    finally:
        client.put("/api/settings", json={"no_proxy": before})


# --- workflows -----------------------------------------------------------------------------------------------------

def test_every_action_on_an_unknown_workflow_says_not_found(client):
    assert client.get("/api/workflows/ghost").status_code == 404
    assert client.put("/api/workflows/ghost", json={"name": "x"}).status_code == 404
    assert client.get("/api/workflows/ghost/usage").status_code == 404
    assert client.delete("/api/workflows/ghost").status_code == 404
    assert client.post("/api/workflows/ghost/revert").status_code in (404, 409)
    r = client.post("/api/workflows", json={"key": f"wf-{next(_ids)}", "name": "Copy", "copy_from": "ghost"})
    assert r.status_code == 404 and "ghost" in r.json()["detail"]


def test_a_project_only_workflow_is_deleted_not_reverted(client):
    key = f"wf-{next(_ids)}"
    assert client.post("/api/workflows?library=project", json={"key": key, "name": "Own",
                                                               "copy_from": "standard"}).status_code == 201
    r = client.post(f"/api/workflows/{key}/revert")
    assert r.status_code == 409 and "only in this project" in r.json()["detail"]
    assert client.delete(f"/api/workflows/{key}?library=project").status_code == 204


def test_the_shared_workflows_are_changed_only_with_the_global_permission(client):
    architect = make_user(client, ["viewer"])  # globally a viewer...
    client.put(f"/api/projects/default/members/{architect['id']}", json={"role": "architect"})  # ...architect here
    theirs = signed_in(architect["username"])
    r = theirs.put("/api/workflows/standard?library=shared", json={"description": "changed for everyone"})
    assert r.status_code == 403 and "customise it for this project" in r.json()["detail"]
