import itertools
import secrets

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, verify_password
from app.main import app
from tests.conftest import start_run, wait_for

_ids = itertools.count()


def other_client() -> TestClient:
    """A second browser: its own cookies, same app (no lifespan, the session client already started it)."""
    return TestClient(app, headers={"X-Project": "default"})


def make_user(client, roles, password="password123"):
    username = f"user{next(_ids)}-{secrets.token_hex(3)}"  # unique even if this module is imported twice
    r = client.post("/api/users", json={"username": username, "display_name": username.title(), "password": password,
                                        "roles": roles})
    assert r.status_code == 201, r.text
    user = r.json()
    # Project data needs membership; the member role matches, so the global roles stay what's tested.
    assert client.put(f"/api/projects/default/members/{user['id']}",
                      json={"role": roles[0] if roles else "viewer"}).status_code == 200
    return user


def signed_in(username, password="password123") -> TestClient:
    c = other_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def test_passwords_are_hashed_and_verified():
    stored = hash_password("s3cret-pass")
    assert "s3cret-pass" not in stored and stored.startswith("scrypt$")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)


def test_everything_needs_sign_in(client):
    anon = other_client()
    assert anon.get("/api/runs").status_code == 401
    assert anon.get("/api/agents").status_code == 401
    me = anon.get("/api/auth/me").json()
    assert me == {"user": None, "needs_bootstrap": False}
    assert anon.get("/api/health").status_code == 200
    # The first admin exists, so bootstrap is closed.
    assert anon.post("/api/auth/bootstrap", json={"username": "x", "display_name": "X",
                                                  "password": "password123"}).status_code == 409


def test_admin_sees_all_permissions(client):
    me = client.get("/api/auth/me").json()["user"]
    assert me["username"] == "admin" and "admin" in me["roles"]
    assert set(me["permissions"]) >= {"agents.manage", "workflows.manage", "runs.start", "runs.approve",
                                      "users.manage", "settings.manage"}


def test_login_logout_and_wrong_password(client):
    user = make_user(client, ["viewer"])
    c = other_client()
    assert c.post("/api/auth/login", json={"username": user["username"], "password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": user["username"].upper(),
                                           "password": "password123"}).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["username"] == user["username"]
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/runs").status_code == 401


def test_viewer_can_read_but_not_change(client):
    viewer = signed_in(make_user(client, ["viewer"])["username"])
    assert viewer.get("/api/agents").status_code == 200
    assert viewer.get("/api/runs").status_code == 200
    r = viewer.put("/api/agents/as_is_doc", json={})
    assert r.status_code == 403 and "Manage agents" in r.json()["detail"]
    assert viewer.post("/api/workflows", json={}).status_code == 403
    assert viewer.post("/api/runs", json={"domain": "customer", "operation": "x"}).status_code == 403
    assert viewer.put("/api/settings", json={}).status_code == 403
    assert viewer.get("/api/users").status_code == 403


def test_viewer_can_read_shared_texts_but_not_change_them(client):
    viewer = signed_in(make_user(client, ["viewer"])["username"])
    texts = {t["key"]: t for t in viewer.get("/api/shared-texts").json()}
    assert "rest_rules" in texts and texts["rest_rules"]["text"]  # what a placeholder in the instructions stands for
    assert viewer.put("/api/shared-texts/rest_rules", json={"text": "Mine."}).status_code == 403
    assert viewer.post("/api/shared-texts/rest_rules/reset").status_code == 403
    assert client.get("/api/shared-texts").json() == list(texts.values())  # the same texts an admin sees


def test_developer_runs_but_reviewer_approves(client):
    dev_user = make_user(client, ["developer"])
    reviewer_user = make_user(client, ["reviewer"])
    dev, reviewer = signed_in(dev_user["username"]), signed_in(reviewer_user["username"])

    client.post("/api/domains/customer/features", json={"key": "authop", "name": "authop"})
    r = dev.post("/api/runs", json={"domain": "customer", "operation": "authop"})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})

    assert dev.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"}).status_code == 403
    r = reviewer.post(f"/api/runs/{run_id}/decision", json={"decision": "approve", "reviewer": "someone else"})
    assert r.status_code == 200, r.text
    assert r.json()["reviewer"] == reviewer_user["display_name"]  # the signed-in user, not the typed name
    assert reviewer.post("/api/runs", json={"domain": "customer", "operation": "authop2"}).status_code == 403


def test_custom_role_grants_exactly_its_permissions(client):
    r = client.post("/api/roles", json={"key": "agent-editor", "name": "Agent editor",
                                        "permissions": ["agents.manage"]})
    assert r.status_code == 201, r.text
    editor = signed_in(make_user(client, ["agent-editor"])["username"])
    assert editor.get("/api/auth/me").json()["user"]["permissions"] == ["agents.manage"]
    assert editor.post("/api/workflows", json={}).status_code == 403
    # Taking the permission away applies to the next request.
    assert client.put("/api/roles/agent-editor", json={"name": "Agent editor", "permissions": []}).status_code == 200
    assert editor.get("/api/auth/me").json()["user"]["permissions"] == []
    assert client.post("/api/roles", json={"key": "x", "name": "X", "permissions": ["nope"]}).status_code == 422


def test_deactivated_or_reset_user_is_signed_out(client):
    user = make_user(client, ["viewer"])
    c = signed_in(user["username"])
    assert client.put(f"/api/users/{user['id']}", json={"active": False}).status_code == 200
    assert c.get("/api/runs").status_code == 401
    assert other_client().post("/api/auth/login", json={"username": user["username"],
                                                        "password": "password123"}).status_code == 401
    client.put(f"/api/users/{user['id']}", json={"active": True})
    c = signed_in(user["username"])
    assert client.put(f"/api/users/{user['id']}/password", json={"password": "brand-new-pass"}).status_code == 204
    assert c.get("/api/runs").status_code == 401
    signed_in(user["username"], "brand-new-pass")


def test_admin_cannot_lock_everyone_out(client):
    me = client.get("/api/auth/me").json()["user"]
    assert client.put(f"/api/users/{me['id']}", json={"active": False}).status_code == 409
    assert client.delete(f"/api/users/{me['id']}").status_code == 409
    assert client.put(f"/api/users/{me['id']}", json={"roles": ["viewer"]}).status_code == 409
    assert client.delete("/api/roles/admin").status_code == 409
    assert client.get("/api/auth/me").json()["user"]["roles"] == ["admin"]


def test_own_password_change(client):
    user = make_user(client, ["viewer"])
    c = signed_in(user["username"])
    assert c.put("/api/auth/password", json={"current_password": "wrong", "new_password": "another-pass"}).status_code == 422
    assert c.put("/api/auth/password", json={"current_password": "password123", "new_password": "short"}).status_code == 422
    assert c.put("/api/auth/password", json={"current_password": "password123",
                                             "new_password": "another-pass"}).status_code == 204
    signed_in(user["username"], "another-pass")


@pytest.mark.parametrize("username", ["a", "Has Space", "-dash"])
def test_invalid_usernames_are_refused(client, username):
    r = client.post("/api/users", json={"username": username, "display_name": "X", "password": "password123"})
    assert r.status_code == 422


def test_agent_and_workflow_definitions_are_only_for_their_managers(client):
    viewer = signed_in(make_user(client, ["developer"])["username"])
    # Other pages still get names and modes, but not the agents' instructions.
    agents = viewer.get("/api/agents").json()
    assert agents and all(a["instructions"] == "" for a in agents)
    assert viewer.get("/api/agents/as_is_doc").json()["instructions"] == ""
    assert all(a["instructions"] for a in client.get("/api/agents").json())
    for path in ("/api/agents/as_is_doc/agent.md", "/api/agents/catalog", "/api/workflows/standard"):
        assert viewer.get(path).status_code == 403, path
    assert viewer.post("/api/agents/render-agent-md", json={}).status_code == 403
    assert viewer.get("/api/workflows").status_code == 200  # names, for the run form
