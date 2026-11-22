"""A user's own git settings (Account page): identity, access tokens per host, and checking they can reach a project's
repository."""

from tests.test_projects import make_monolith, new_project


def test_the_git_identity_is_saved_and_cleared(client):
    r = client.put("/api/account/git", json={"name": "  Ada  ", "email": "ada@example.com"})
    assert r.status_code == 200 and r.json()["name"] == "Ada" and r.json()["email"] == "ada@example.com"
    assert client.get("/api/account/git").json()["name"] == "Ada"
    assert client.put("/api/account/git", json={"name": "", "email": "not-an-email"}).status_code == 422
    cleared = client.put("/api/account/git", json={"name": "", "email": ""}).json()
    assert cleared["name"] in (None, "") and cleared["email"] in (None, "")


def test_tokens_are_kept_per_host_replaced_and_never_shown(client):
    r = client.put("/api/account/git/credentials", json={"host": "https://git.example.com/team/repo.git",
                                                          "username": "ada", "token": "secret-1"})
    assert r.status_code == 200
    saved = [c for c in r.json()["credentials"] if c["host"] == "git.example.com"]
    assert len(saved) == 1 and saved[0]["username"] == "ada" and "secret-1" not in r.text
    r = client.put("/api/account/git/credentials", json={"host": "git.example.com", "username": "ada2", "token": "x"})
    assert [c["username"] for c in r.json()["credentials"] if c["host"] == "git.example.com"] == ["ada2"]  # replaced
    assert client.put("/api/account/git/credentials", json={"host": "two words", "username": "u",
                                                           "token": "t"}).status_code == 422
    gone = client.delete(f"/api/account/git/credentials/{saved[0]['id']}").json()
    assert all(c["host"] != "git.example.com" for c in gone["credentials"])


def test_access_is_checked_only_against_the_projects_own_repositories(client, tmp_path):
    no_repo = new_project(client)
    r = client.post("/api/account/git/test", json={"project": no_repo})
    assert r.status_code == 422 and "no repository" in r.json()["detail"]

    monolith = tmp_path / "monolith"
    make_monolith(monolith)
    key = new_project(client, monolith_repo=str(monolith))
    r = client.post("/api/account/git/test", json={"project": key})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["repository"] == str(monolith)

    other = client.post("/api/account/git/test", json={"project": key, "repository": "https://elsewhere.example/x.git"})
    assert other.status_code == 422  # never contacts a URL the project doesn't use
    assert client.post("/api/account/git/test", json={"project": "no-such-project"}).status_code == 403

    # A repository one of its domains gives an agent (Agent inputs) is one of the project's: it is checked
    service = tmp_path / "service"
    make_monolith(service)
    from app.db.models import Domain
    from app.db.session import session_scope
    with session_scope() as s:
        s.add(Domain(project=key, key="loan", name="Loan", agent_inputs={
            "microservice_scaffold": {"paths": [], "notes": "", "repository": str(service)}}))
    r = client.post("/api/account/git/test", json={"project": key, "repository": str(service)})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["repository"] == str(service)


def test_an_unreachable_repository_is_reported_not_raised(client, tmp_path):
    key = new_project(client, monolith_repo=str(tmp_path / "missing"))
    r = client.post("/api/account/git/test", json={"project": key})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"]
