"""Managing users and roles (Users page): every outcome an admin can meet, including the refusals."""

import itertools

from app.db.models import GitCredential, ProjectMember
from app.db.session import session_scope
from tests.test_auth import make_user, signed_in

_ids = itertools.count()


def _admin_id(client) -> int:
    return client.get("/api/auth/me").json()["user"]["id"]


def test_the_permissions_are_listed_with_their_labels(client):
    listed = client.get("/api/permissions").json()
    assert {"key": "runs.start", "label": "Run migrations",
            "description": "Start migration runs and retry failed ones"} in listed


def test_a_user_needs_a_new_username_known_roles_and_a_valid_password(client):
    user = make_user(client, ["developer"])
    again = client.post("/api/users", json={"username": user["username"], "display_name": "X", "password": "password123",
                                            "roles": []})
    assert again.status_code == 409 and "already a user" in again.json()["detail"]
    unknown = client.post("/api/users", json={"username": f"new{next(_ids)}", "display_name": "X",
                                              "password": "password123", "roles": ["nope"]})
    assert unknown.status_code == 422 and "Unknown role(s): nope" in unknown.json()["detail"]
    short = client.post("/api/users", json={"username": f"new{next(_ids)}", "display_name": "X", "password": "short",
                                            "roles": []})
    assert short.status_code == 422


def test_a_user_is_renamed_given_roles_and_refused_unknown_ones(client):
    user = make_user(client, ["viewer"])
    r = client.put(f"/api/users/{user['id']}", json={"display_name": "  Renamed  ", "roles": ["reviewer", "reviewer"]})
    assert r.status_code == 200 and r.json()["display_name"] == "Renamed" and r.json()["roles"] == ["reviewer"]
    assert client.put(f"/api/users/{user['id']}", json={"roles": ["ghost"]}).status_code == 422
    assert client.put("/api/users/999999", json={"display_name": "x"}).status_code == 404


def test_an_admin_cannot_deactivate_or_delete_themselves(client):
    me = _admin_id(client)
    r = client.put(f"/api/users/{me}", json={"active": False})
    assert r.status_code == 409 and "deactivate yourself" in r.json()["detail"]
    r = client.delete(f"/api/users/{me}")
    assert r.status_code == 409 and "delete yourself" in r.json()["detail"]


def test_a_password_reset_signs_the_user_out_and_needs_a_valid_password(client):
    user = make_user(client, ["developer"])
    theirs = signed_in(user["username"])
    assert client.put(f"/api/users/{user['id']}/password", json={"password": "short"}).status_code == 422
    assert client.put("/api/users/999999/password", json={"password": "long-enough-1"}).status_code == 404
    assert client.put(f"/api/users/{user['id']}/password", json={"password": "long-enough-1"}).status_code == 204
    assert theirs.get("/api/auth/me").json()["user"] is None  # signed out everywhere
    assert theirs.get("/api/runs").status_code == 401
    signed_in(user["username"], "long-enough-1")  # the new password works


def test_deleting_a_user_removes_their_project_access_and_tokens(client):
    user = make_user(client, ["developer"])
    with session_scope() as s:
        s.add(GitCredential(user_id=user["id"], host="example.com", username="u", token="t"))
    assert client.delete(f"/api/users/{user['id']}").status_code == 204
    with session_scope() as s:
        assert s.query(ProjectMember).filter(ProjectMember.user_id == user["id"]).count() == 0
        assert s.query(GitCredential).filter(GitCredential.user_id == user["id"]).count() == 0
    assert client.delete(f"/api/users/{user['id']}").status_code == 404
    assert all(u["id"] != user["id"] for u in client.get("/api/users").json())


def test_roles_are_created_once_with_known_permissions(client):
    key = f"role{next(_ids)}"
    r = client.post("/api/roles", json={"key": key, "name": " Auditor ", "description": " reads ",
                                        "permissions": ["runs.edit", "runs.start", "runs.start"]})
    assert r.status_code == 201
    assert r.json()["name"] == "Auditor" and r.json()["permissions"] == ["runs.start", "runs.edit"]  # in their order
    assert client.post("/api/roles", json={"key": key, "name": "Again"}).status_code == 409
    bad = client.post("/api/roles", json={"key": f"role{next(_ids)}", "name": "Bad", "permissions": ["fly"]})
    assert bad.status_code == 422 and "Unknown permission(s): fly" in bad.json()["detail"]


def test_a_role_is_edited_but_the_admin_role_keeps_every_permission(client):
    key = f"role{next(_ids)}"
    client.post("/api/roles", json={"key": key, "name": "Editor", "permissions": []})
    r = client.put(f"/api/roles/{key}", json={"name": "Editor 2", "permissions": ["runs.edit"]})
    assert r.status_code == 200 and r.json()["permissions"] == ["runs.edit"]
    assert client.put("/api/roles/ghost", json={"name": "x"}).status_code == 404
    admin = client.put("/api/roles/admin", json={"name": "Admin", "permissions": []})
    assert admin.status_code == 200 and "users.manage" in admin.json()["permissions"]


def test_deleting_a_role_is_refused_while_needed(client):
    assert client.delete("/api/roles/ghost").status_code == 404
    assert client.delete("/api/roles/admin").status_code == 409

    key = f"role{next(_ids)}"
    client.post("/api/roles", json={"key": key, "name": "Member role", "permissions": ["runs.edit"]})
    user = make_user(client, ["viewer"])
    client.put(f"/api/projects/default/members/{user['id']}", json={"role": key})  # a project uses it
    r = client.delete(f"/api/roles/{key}")
    assert r.status_code == 409 and "Members of project default" in r.json()["detail"]

    client.put(f"/api/projects/default/members/{user['id']}", json={"role": "viewer"})
    client.put(f"/api/users/{user['id']}", json={"roles": ["viewer", key]})
    assert client.delete(f"/api/roles/{key}").status_code == 204
    assert next(u for u in client.get("/api/users").json() if u["id"] == user["id"])["roles"] == ["viewer"]
    assert all(r["key"] != key for r in client.get("/api/roles").json())


def test_a_change_that_leaves_no_one_able_to_manage_users_is_refused(client):
    import pytest
    from fastapi import HTTPException

    from app.api.users_api import _admins_remain
    from app.db.models import User
    from app.db.session import SessionLocal

    key = f"role{next(_ids)}"
    client.post("/api/roles", json={"key": key, "name": "Managers", "permissions": ["users.manage"]})
    manager = make_user(client, [key])
    # While the admin can manage users, other managers can lose the permission or go
    assert client.put(f"/api/roles/{key}", json={"name": "Managers", "permissions": []}).status_code == 200
    assert client.delete(f"/api/users/{manager['id']}").status_code == 204

    # Nobody left with the permission: refused (checked in a transaction that is rolled back, so nobody is locked out)
    s = SessionLocal()
    try:
        for user in s.query(User):
            user.roles = ["viewer"]
        s.flush()
        with pytest.raises(HTTPException) as refused:
            _admins_remain(s)
        assert refused.value.status_code == 409 and "manage users" in refused.value.detail
    finally:
        s.rollback()
        s.close()
    assert client.get("/api/users").status_code == 200  # the admin still can
