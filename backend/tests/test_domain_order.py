"""The order domains are migrated in, set on the Domains page."""

from tests.test_projects import as_project, new_project


def _keys(proj):
    return [d["key"] for d in proj.get("/api/domains").json()]


def test_domains_are_listed_in_their_migration_order(client):
    proj = as_project(client, new_project(client))
    for key in ("alpha", "beta", "gamma"):
        assert proj.post("/api/domains", json={"key": key, "name": key.title()}).status_code == 201
    assert _keys(proj) == ["alpha", "beta", "gamma"]  # none placed yet: oldest first

    r = proj.post("/api/domains/reorder", json={"keys": ["gamma", "alpha"]})
    assert r.status_code == 200, r.text
    assert [d["key"] for d in r.json()] == ["gamma", "alpha", "beta"]  # those left out follow
    assert [d["migration_order"] for d in r.json()] == [1, 2, 3]
    assert _keys(proj) == ["gamma", "alpha", "beta"]

    proj.post("/api/domains", json={"key": "delta", "name": "Delta"})
    assert _keys(proj) == ["gamma", "alpha", "beta", "delta"]  # a new domain comes last until placed
    assert proj.get("/api/domains/delta").json()["migration_order"] is None
    assert [d["key"] for d in proj.get("/api/discovery").json()["domains"]] == _keys(proj)


def test_an_order_with_unknown_or_repeated_domains_is_refused(client):
    proj = as_project(client, new_project(client))
    proj.post("/api/domains", json={"key": "alpha", "name": "Alpha"})
    assert proj.post("/api/domains/reorder", json={"keys": ["alpha", "nowhere"]}).status_code == 422
    assert proj.post("/api/domains/reorder", json={"keys": ["alpha", "alpha"]}).status_code == 422


def test_each_project_has_its_own_order(client):
    one, two = (as_project(client, new_project(client)) for _ in range(2))
    for proj in (one, two):
        proj.post("/api/domains", json={"key": "alpha", "name": "Alpha"})
        proj.post("/api/domains", json={"key": "beta", "name": "Beta"})
    one.post("/api/domains/reorder", json={"keys": ["beta", "alpha"]})
    assert _keys(one) == ["beta", "alpha"] and _keys(two) == ["alpha", "beta"]
