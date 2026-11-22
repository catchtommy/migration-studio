"""The Agents page's actions on agents that don't allow them: unknown agents, duplicates, files that aren't agent
files, and actions that only apply to built-in agents or project copies."""

import itertools

_ids = itertools.count()


def _custom(client) -> str:
    key = f"custom_{next(_ids)}"
    r = client.post("/api/agents", json={"key": key, "title": "Custom", "description": "made here"})
    assert r.status_code == 201, r.text
    return key


def test_every_action_on_an_unknown_agent_says_not_found(client):
    for method, path, body in (("get", "/api/agents/ghost", None), ("get", "/api/agents/ghost/agent.md", None),
                               ("get", "/api/agents/ghost/compare", None), ("get", "/api/agents/ghost/usage", None),
                               ("post", "/api/agents/ghost/customise", None), ("post", "/api/agents/ghost/revert", None),
                               ("delete", "/api/agents/ghost", None),
                               ("put", "/api/agents/ghost/agent-md", {"content": "---\nname: x\n---\nhi"})):
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    current = client.get("/api/agents/as_is_doc").json()
    body = {k: current[k] for k in ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools",
                                    "outputs", "max_turns", "checks", "guardrails")}
    assert client.put("/api/agents/ghost", json=body).status_code == 404
    reset = client.post("/api/agents/ghost/reset")  # not a built-in: there is no default to go back to
    assert reset.status_code == 409 and "built-in" in reset.json()["detail"]


def test_an_agent_key_is_used_once(client):
    key = _custom(client)
    r = client.post("/api/agents", json={"key": key, "title": "Again", "description": ""})
    assert r.status_code == 409


def test_text_that_isnt_an_agent_file_is_refused(client):
    for content in ("just some text", "---\nname: [unclosed\n---\nbody"):
        assert client.post("/api/agents/parse-agent-md", json={"content": content}).status_code == 422
        assert client.put("/api/agents/as_is_doc/agent-md", json={"content": content}).status_code == 422


def test_an_agent_file_with_invalid_settings_is_refused_and_changes_nothing(client):
    before = client.get("/api/agents/as_is_doc").json()
    for content in ("---\nname: X\nmetadata:\n  max-turns: 900\n---\nDo the work.",  # the file reader refuses it
                    "---\nname: X\nmodel: " + "m" * 200 + "\n---\nDo the work."):  # the agent's settings refuse it
        r = client.put("/api/agents/as_is_doc/agent-md", json={"content": content})
        assert r.status_code == 422, r.text
    assert client.get("/api/agents/as_is_doc").json()["instructions"] == before["instructions"]


def test_actions_for_built_in_agents_or_project_copies_are_refused_elsewhere(client):
    key = _custom(client)
    assert client.get(f"/api/agents/{key}/compare").status_code == 409  # no release default to compare with
    assert client.post(f"/api/agents/{key}/reset").status_code == 409  # nothing to reset to
    own = f"own_{next(_ids)}"  # an agent only this project has: deleted, not reverted
    assert client.post("/api/agents?scope=project", json={"key": own, "title": "Own", "description": ""}).status_code == 201
    r = client.post(f"/api/agents/{own}/revert")
    assert r.status_code == 409 and "only in this project" in r.json()["detail"]


def test_the_editor_catalog_lists_models_efforts_and_tools(client):
    catalog = client.get("/api/agents/catalog").json()
    assert catalog["models"][0] == "auto" and catalog["efforts"] and catalog["tools"]
    assert all({"name", "description"} <= set(t) for t in catalog["tools"])
