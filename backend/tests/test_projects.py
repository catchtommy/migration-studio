import itertools
import secrets
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.base_agent import AgentContext
from app.agents.common import project_brief
from app.agents.tools import toolchain_env
from app.main import app
from tests.conftest import wait_for
from tests.test_auth import make_user

_ids = itertools.count()


def new_project(client, **settings) -> str:
    key = f"proj-{next(_ids)}-{secrets.token_hex(3)}"  # unique even if this module is imported twice
    r = client.post("/api/projects", json={"key": key, "name": key.title(), "settings": settings})
    assert r.status_code == 201, r.text
    return key


def as_project(client: TestClient, key: str) -> TestClient:
    """The same browser, working in another project."""
    c = TestClient(app, headers={"X-Project": key}, cookies=dict(client.cookies))
    return c


def signed_in(username, project="default", password="password123") -> TestClient:
    c = TestClient(app, headers={"X-Project": project})
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


def test_creating_a_project_makes_the_creator_its_admin(client):
    key = new_project(client, language="csharp", source_stack=".NET Framework 4.8, WebForms")
    project = client.get(f"/api/projects/{key}").json()
    assert project["my_role"] == "admin" and project["members"] == 1
    assert project["settings"]["language"] == "csharp"
    assert project["settings"]["max_attempts"] == 3  # defaults for the rest
    assert any(p["key"] == key for p in client.get("/api/projects").json())
    assert client.post("/api/projects", json={"key": key, "name": "Again"}).status_code == 409
    assert client.post("/api/projects", json={"key": "Bad Key", "name": "x"}).status_code == 422
    assert client.post("/api/projects", json={"key": "_shared", "name": "x"}).status_code == 422
    assert client.put(f"/api/projects/{key}/settings", json={"language": "cobol"}).status_code == 422


def test_projects_keep_their_data_apart(client):
    key = new_project(client)
    other = as_project(client, key)
    assert other.get("/api/domains").json() == []  # a new project starts empty
    assert other.post("/api/domains", json={"key": "customer", "name": "Customer B", "description": ""}).status_code == 201
    assert client.get("/api/domains/customer").json()["name"] == "Customer"  # the default project's is untouched

    other.post("/api/domains/customer/features", json={"key": "iso", "name": "iso"})
    run = other.post("/api/runs", json={"domain": "customer", "operation": "iso"}).json()
    wait_for(other, run["id"], {"awaiting_approval"})
    assert run["id"] not in [r["id"] for r in client.get("/api/runs").json()]
    assert client.get(f"/api/runs/{run['id']}").status_code == 404  # not visible from another project
    assert other.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"
    assert client.get(f"/api/runs/{run['id']}/artifacts").status_code == 404


def test_members_only_see_their_projects_with_their_role_there(client):
    key = new_project(client)
    user = make_user(client, ["viewer"])  # a viewer in the default project
    assert client.put(f"/api/projects/{key}/members/{user['id']}", json={"role": "developer"}).status_code == 200

    there = signed_in(user["username"], key)
    assert {p["key"] for p in there.get("/api/projects").json()} == {"default", key}
    mine = next(p for p in there.get("/api/projects").json() if p["key"] == key)
    assert "runs.start" in mine["permissions"]
    there.post("/api/domains", json={"key": "d", "name": "D", "description": ""})  # 403, but harmless to try
    as_admin = as_project(client, key)
    as_admin.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    as_admin.post("/api/domains/d/features", json={"key": "f", "name": "f"})
    assert there.post("/api/runs", json={"domain": "d", "operation": "f"}).status_code == 201  # developer here
    home = signed_in(user["username"], "default")
    assert home.post("/api/runs", json={"domain": "customer", "operation": "x"}).status_code == 403  # viewer here

    outsider = make_user(client, ["viewer"])
    c = signed_in(outsider["username"], key)
    assert c.get("/api/runs").status_code == 403  # not a member of that project
    assert key not in {p["key"] for p in c.get("/api/projects").json()}
    assert c.get(f"/api/projects/{key}").status_code == 403

    assert client.delete(f"/api/projects/{key}/members/{user['id']}").status_code == 204
    assert there.get("/api/runs").status_code == 403


def test_project_admins_manage_members_but_creating_projects_needs_the_permission(client):
    key = new_project(client)
    lead = make_user(client, ["viewer"])
    client.put(f"/api/projects/{key}/members/{lead['id']}", json={"role": "admin"})
    lead_client = signed_in(lead["username"], key)
    newcomer = make_user(client, ["viewer"])
    assert any(u["id"] == newcomer["id"] for u in lead_client.get(f"/api/projects/{key}/candidates").json())
    assert lead_client.put(f"/api/projects/{key}/members/{newcomer['id']}", json={"role": "reviewer"}).status_code == 200
    assert lead_client.put(f"/api/projects/{key}/settings", json={"monolith_repo": "/tmp/x"}).status_code == 200
    # A project role never grants global permissions.
    assert lead_client.post("/api/projects", json={"key": "nope", "name": "Nope"}).status_code == 403
    assert lead_client.get("/api/users").status_code == 403


def test_a_project_can_customise_a_shared_agent_and_workflow(client):
    key = new_project(client)
    proj = as_project(client, key)
    agent = proj.get("/api/agents/as_is_doc").json()
    body = {f: agent[f] for f in ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools",
                                  "outputs", "max_turns", "checks")}
    r = proj.put("/api/agents/as_is_doc?scope=project", json={**body, "instructions": "Project-specific."})
    assert r.status_code == 200 and r.json()["customised"] and r.json()["scope"] == "project"
    assert proj.get("/api/agents/as_is_doc").json()["instructions"] == "Project-specific."
    assert client.get("/api/agents/as_is_doc").json()["instructions"] != "Project-specific."  # others: shared
    assert proj.post("/api/agents/as_is_doc/revert").json()["customised"] is False
    assert proj.get("/api/agents/as_is_doc").json()["instructions"] == agent["instructions"]

    wf = proj.get("/api/workflows").json()[0]
    r = proj.put(f"/api/workflows/{wf['key']}?library=project", json={"name": "Our process"})
    assert r.status_code == 200 and r.json()["customised"]
    assert next(w for w in client.get("/api/workflows").json() if w["key"] == wf["key"])["name"] != "Our process"
    assert proj.post(f"/api/workflows/{wf['key']}/revert").json()["name"] == wf["name"]


def test_shared_library_changes_need_a_global_role(client):
    key = new_project(client)
    user = make_user(client, ["viewer"])
    client.put(f"/api/projects/{key}/members/{user['id']}", json={"role": "architect"})
    arch = signed_in(user["username"], key)
    agent = arch.get("/api/agents/deep_dive").json()
    body = {f: agent[f] for f in ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools",
                                  "outputs", "max_turns", "checks")}
    r = arch.put("/api/agents/deep_dive", json=body)  # scope=shared by default
    assert r.status_code == 403 and "customise" in r.json()["detail"]
    assert arch.put("/api/agents/deep_dive?scope=project", json=body).status_code == 200


def test_project_language_reaches_the_agents_and_their_toolchain(tmp_path):
    ctx = AgentContext(run_id="r", domain="loan", operation="op", stage="as_is_doc", attempt=1, workspace=tmp_path,
                       feedback=None, settings={"language": "csharp", "source_stack": ".NET 4.8 WebForms",
                                                "target_stack": ".NET 9 minimal APIs"},
                       log=lambda m: None, config={"jdk": "modern"})
    text = project_brief(ctx)
    assert "C# / .NET" in text and ".NET 9 minimal APIs" in text and "target toolchain" in text
    env = toolchain_env({"PATH": "/usr/bin"}, "/opt/dotnet", "csharp")
    assert env["DOTNET_ROOT"] == "/opt/dotnet" and env["PATH"].startswith("/opt/dotnet/bin")
    assert "JAVA_HOME" not in env
    assert toolchain_env({"PATH": "/usr/bin"}, "/opt/jdk", "java")["JAVA_HOME"] == "/opt/jdk"


def test_deleting_projects(client):
    key = new_project(client)
    assert client.delete(f"/api/projects/{key}").status_code == 204
    assert client.get(f"/api/projects/{key}").status_code == 404
    busy = new_project(client)
    proj = as_project(client, busy)
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    proj.post("/api/domains/d/features", json={"key": "f", "name": "f"})
    run = proj.post("/api/runs", json={"domain": "d", "operation": "f"}).json()
    wait_for(proj, run["id"], {"awaiting_approval"})
    assert client.delete(f"/api/projects/{busy}").status_code == 409


def test_requests_without_a_project_are_refused(client):
    bare = TestClient(app, cookies=dict(client.cookies))
    assert bare.get("/api/runs").status_code == 400
    assert bare.get("/api/projects").status_code == 200  # listing projects needs none
    assert TestClient(app, headers={"X-Project": "missing"}, cookies=dict(client.cookies)).get("/api/runs").status_code == 404


def test_access_to_a_project_and_its_runs_comes_only_from_membership(client):
    key = new_project(client)
    proj = as_project(client, key)
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    proj.post("/api/domains/d/features", json={"key": "f", "name": "f"})
    run = proj.post("/api/runs", json={"domain": "d", "operation": "f"}).json()
    wait_for(proj, run["id"], {"awaiting_approval"})

    # An architect everywhere else is still nobody in a project they haven't been given.
    user = make_user(client, ["architect"])
    c = signed_in(user["username"], key)
    assert c.get("/api/runs").status_code == 403
    assert c.get(f"/api/runs/{run['id']}").status_code == 403
    assert c.get(f"/api/runs/{run['id']}/events").status_code == 403

    # Given access as a viewer, they see the project and its runs and can't change anything, whatever their own roles.
    client.put(f"/api/projects/{key}/members/{user['id']}", json={"role": "viewer"})
    assert c.get(f"/api/runs/{run['id']}").status_code == 200
    assert next(p for p in c.get("/api/projects").json() if p["key"] == key)["permissions"] == []
    assert c.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve", "comment": ""}).status_code == 403
    assert c.post("/api/runs", json={"domain": "d", "operation": "f"}).status_code == 403
    users = {u["id"]: u for u in client.get("/api/users").json()}
    assert {"project": key, "name": key.title(), "role": "viewer"} in users[user["id"]]["projects"]


def test_project_admins_manage_access_to_projects_they_are_not_in(client):
    key = new_project(client)
    lead = make_user(client, ["admin"])  # a global admin, but not a member of the new project
    c = signed_in(lead["username"], key)
    listed = next(p for p in c.get("/api/projects").json() if p["key"] == key)
    assert listed["member"] is False and listed["permissions"] == ["project.manage"]
    assert c.get("/api/runs").status_code == 403  # can't open its data
    assert c.get(f"/api/projects/{key}/members").status_code == 200
    assert c.put(f"/api/projects/{key}/members/{lead['id']}", json={"role": "reviewer"}).status_code == 200
    assert c.get("/api/runs").status_code == 200  # now a member
    assert next(p for p in c.get("/api/projects").json() if p["key"] == key)["permissions"] == ["runs.approve",
                                                                                                "runs.edit"]


def make_monolith(path):
    """A small monolith repository: main, and a release/2.4 branch with a different App.java."""
    import subprocess

    path.mkdir()
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=path, check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "--quiet", "-b", "main")
    (path / "App.java").write_text("main")
    run("add", "-A")
    run("commit", "--quiet", "-m", "main")
    run("checkout", "--quiet", "-b", "release/2.4")
    (path / "App.java").write_text("release")
    run("commit", "--quiet", "-am", "release")
    run("checkout", "--quiet", "main")
    return run


def test_runs_clone_the_monolith_from_the_projects_base_branch(tmp_path):
    from app.tools import git_ops

    repo = tmp_path / "monolith"
    make_monolith(repo)
    ws = tmp_path / "ws"
    sha = git_ops.init_workspace(ws, str(repo), "migration/x/y", source_branch="release/2.4")
    assert (ws / "App.java").read_text() == "release" and git_ops.head(ws) == sha
    default = tmp_path / "ws2"
    git_ops.init_workspace(default, str(repo), "migration/x/y")
    assert (default / "App.java").read_text() == "main"
    try:
        git_ops.init_workspace(tmp_path / "ws3", str(repo), "migration/x/y", source_branch="nope")
        raise AssertionError("cloning a missing branch should fail")
    except git_ops.GitError as e:
        assert "branch nope" in str(e)


def test_the_base_branch_must_be_a_branch_name(client):
    key = new_project(client)
    assert client.put(f"/api/projects/{key}/settings", json={"monolith_branch": "release/2.4"}).status_code == 200
    for bad in ("--upload-pack=x", "a..b", "has space", "x.lock"):
        assert client.put(f"/api/projects/{key}/settings", json={"monolith_branch": bad}).status_code == 422, bad
    assert client.put(f"/api/projects/{key}/settings", json={"monolith_branch": ""}).status_code == 200


def test_runs_that_only_read_the_monolith_share_one_read_only_copy(client, tmp_path):
    import os
    import time

    from app import legacy
    from app.agents.base_agent import AgentContext
    from app.agents.tools import Toolbox, ToolError
    from app.tools import git_ops

    upstream = tmp_path / "monolith"
    commit = make_monolith(upstream)
    key = new_project(client)
    proj = as_project(client, key)
    assert proj.put(f"/api/projects/{key}/settings", json={"monolith_repo": str(upstream)}).status_code == 200
    for _ in range(100):  # saving a new repository clones it to the server in the background
        status = proj.get(f"/api/projects/{key}/repository").json()
        if not status["fetching"]:
            break
        time.sleep(0.05)
    assert status["cloned"] and status["error"] is None
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    for f in ("one", "two", "three"):
        proj.post("/api/domains/d/features", json={"key": f, "name": f})

    first = proj.post("/api/runs", json={"domain": "d", "operation": "one"}).json()
    wait_for(proj, first["id"], {"awaiting_approval"})
    second = proj.post("/api/runs", json={"domain": "d", "operation": "two"}).json()
    wait_for(proj, second["id"], {"awaiting_approval"})
    runs = [proj.get(f"/api/runs/{r['id']}").json() for r in (first, second)]
    assert runs[0]["legacy"]["sha"] == runs[1]["legacy"]["sha"]  # one shared copy
    shared = legacy.copy_dir(key, runs[0]["legacy"]["sha"])
    assert (shared / "App.java").read_text() == "main"
    assert not os.access(shared / "App.java", os.W_OK)  # read-only on disk
    for r in runs:
        assert not (Path(r["workspace"]) / "App.java").exists()  # not checked out per run

    # Agents read it at its usual paths, and can't change it.
    ctx = AgentContext(run_id=first["id"], domain="d", operation="one", stage="as_is_doc", attempt=1,
                       workspace=Path(runs[0]["workspace"]), feedback=None, settings={}, log=lambda m: None,
                       config={"tools": ["list_files", "read_file", "search_code", "write_file"]}, legacy_dir=shared)
    box = Toolbox(ctx, None)
    assert "main" in box.run("read_file", {"path": "App.java"})
    assert "App.java" in box.run("list_files", {"pattern": "*.java"})
    assert "App.java" in box.run("search_code", {"pattern": "main"})
    try:
        box.run("write_file", {"path": "App.java", "content": "changed"})
        raise AssertionError("writing the legacy code should be refused")
    except ToolError as e:
        assert "read-only" in str(e)
    assert box.run("write_file", {"path": ".migration/d/one/note.md", "content": "ok"}).startswith("Wrote")

    # Fetching the latest makes a new copy current for new runs; runs keep theirs.
    (upstream / "App.java").write_text("main v2")
    commit("commit", "--quiet", "-am", "v2")
    assert proj.post(f"/api/projects/{key}/repository/fetch").status_code == 202
    for _ in range(100):
        status = proj.get(f"/api/projects/{key}/repository").json()
        if not status["fetching"]:
            break
        time.sleep(0.05)
    assert status["error"] is None and status["legacy"]["subject"] == "v2"
    third = proj.post("/api/runs", json={"domain": "d", "operation": "three"}).json()
    wait_for(proj, third["id"], {"awaiting_approval"})
    newer = legacy.copy_dir(key, proj.get(f"/api/runs/{third['id']}").json()["legacy"]["sha"])
    assert (newer / "App.java").read_text() == "main v2"
    assert shared.exists()  # still read by unfinished runs
    assert proj.get(f"/api/runs/{first['id']}").json()["monolith_behind"] == 1

    # A copy no unfinished run reads is deleted at the next fetch; a missing copy is made again when needed.
    from app.db.models import Run
    from app.db.session import session_scope
    with session_scope() as s:
        for r in (first, second):
            s.get(Run, r["id"]).status = "completed"
    legacy.refresh(key)
    assert not shared.exists() and newer.exists()
    assert (legacy.path_for(key, runs[0]["legacy"]) / "App.java").read_text() == "main"

    viewer = make_user(client, ["viewer"])
    client.put(f"/api/projects/{key}/members/{viewer['id']}", json={"role": "viewer"})
    assert signed_in(viewer["username"], key).post(f"/api/projects/{key}/repository/fetch").status_code == 403
    assert git_ops  # imported for symmetry with the other repository tests


def test_a_run_gets_its_own_checkout_of_the_monolith_only_when_a_step_changes_it(client, tmp_path):
    from app import legacy
    from app.tools import git_ops

    upstream = tmp_path / "monolith"
    make_monolith(upstream)
    key = new_project(client, monolith_repo=str(upstream))
    proj = as_project(client, key)
    proj.post("/api/domains", json={"key": "d", "name": "D", "description": ""})
    proj.post("/api/domains/d/features", json={"key": "f", "name": "f"})
    wf = proj.get("/api/workflows/standard").json()
    second = [st["key"] for st in wf["steps"] if st["kind"] == "agent"][1]
    steps = [{**st, "code": {"repository": ""}} if st.get("key") == second else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "d", "operation": "f", "base_branch": "release/2.4"}).json()

    # The steps before it read the shared read-only copy, of the base branch chosen for the run.
    run = wait_for(proj, run["id"], {"awaiting_branches"}, timeout=30)
    detail = proj.get(f"/api/runs/{run['id']}").json()
    assert detail["current_stage"] == second and detail["pending_branches"]["repository"]["checked_out"] is False
    assert detail["legacy"]["branch"] == "release/2.4"
    assert (legacy.copy_dir(key, detail["legacy"]["sha"]) / "App.java").read_text() == "release"
    ws = Path(detail["workspace"])
    assert not (ws / "App.java").exists()

    # Confirming its branches checks the monolith out, at the commit the run has read so far.
    r = proj.post(f"/api/runs/{run['id']}/branches", json={"feature_branch": detail["branch"]})
    assert r.status_code == 200, r.text
    run = wait_for(proj, run["id"], {"awaiting_approval", "awaiting_commit"}, timeout=30)
    assert run["legacy"] is None and run["base_sha"] == detail["legacy"]["sha"] and run["base_branch"] == "release/2.4"
    assert (ws / "App.java").read_text() == "release"
    assert git_ops.git(ws, "rev-parse", "--abbrev-ref", "HEAD") == detail["branch"]
    assert not ws.with_name(ws.name + ".monolith").exists()
