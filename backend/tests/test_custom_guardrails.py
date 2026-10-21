"""Guardrails written in plain words: global (Agents page) and per project (added or in place of the global ones),
given to every agent whose step changes code, in its prompt and with each guardrail check result."""

from types import SimpleNamespace

from app import custom_guardrails as cg
from app.agents import common


def ctx(settings: dict, code: bool = True, instructions: str = "Do the step."):
    stage = SimpleNamespace(key="modular_monolith", code={"repository": ""} if code else None)
    return SimpleNamespace(settings=settings, stage_def=stage, config={"instructions": instructions})


def test_the_project_adds_to_the_global_guardrails_or_replaces_them():
    settings = {"custom_guardrails": "Never touch com.acme.common.", "project_guardrails": "Log with AuditLogger.",
                "project_guardrails_mode": "add"}
    assert cg.effective(settings) == "Never touch com.acme.common.\n\nLog with AuditLogger."
    assert cg.effective({**settings, "project_guardrails_mode": "replace"}) == "Log with AuditLogger."
    assert cg.effective({**settings, "project_guardrails_mode": "replace", "project_guardrails": ""}) == ""
    assert cg.effective({"custom_guardrails": "  ", "project_guardrails": ""}) == ""


def test_code_changing_agents_get_them_in_the_prompt_and_with_each_check():
    settings = {"custom_guardrails": "Never touch com.acme.common.", "project_guardrails": "",
                "project_guardrails_mode": "add"}
    prompt = common.system_prompt(ctx(settings))
    assert "## Guardrails\n" in prompt and prompt.endswith("Never touch com.acme.common.")
    assert "Never touch com.acme.common." in cg.reminder(ctx(settings))
    # A step that changes no code (a design step), or no guardrails written: nothing added.
    assert "## Guardrails" not in common.system_prompt(ctx(settings, code=False))
    assert cg.reminder(ctx(settings, code=False)) == ""
    assert "## Guardrails" not in common.system_prompt(ctx({**settings, "custom_guardrails": ""}))


def test_the_check_tools_repeat_them():
    from app.agents.tools import Toolbox

    box = Toolbox.__new__(Toolbox)
    box.ctx = ctx({"custom_guardrails": "Never touch com.acme.common."})
    box._last_check, box._repeats = None, 0
    text = box._check("check_module_guardrails", [], lambda problems: "Module guardrails: PASS.")
    assert text.startswith("Module guardrails: PASS.") and "Never touch com.acme.common." in text


def test_edited_from_the_agents_page_and_the_project_settings(client):
    r = client.get("/api/guardrails")
    assert r.status_code == 200 and {b["key"] for b in r.json()["builtin"]} >= {"module_guardrails", "agent_limits"}
    assert all(b["description"] for b in r.json()["builtin"])
    assert client.put("/api/guardrails", json={"text": "Never touch com.acme.common."}).json()["text"] == \
        "Never touch com.acme.common."
    r = client.put("/api/projects/default/settings", json={"project_guardrails": "Log with AuditLogger.",
                                                           "project_guardrails_mode": "replace"})
    assert r.status_code == 200 and r.json()["project_guardrails_mode"] == "replace"
    assert client.put("/api/projects/default/settings", json={"project_guardrails_mode": "merge"}).status_code == 422

    from app import settings_store
    assert cg.effective(settings_store.get_all("default")) == "Log with AuditLogger."
    client.put("/api/guardrails", json={"text": ""})
    client.put("/api/projects/default/settings", json={"project_guardrails": "", "project_guardrails_mode": "add"})


def test_an_agent_has_guardrails_of_its_own_whatever_its_step():
    settings = {"custom_guardrails": "Never touch com.acme.common.", "project_guardrails": "",
                "project_guardrails_mode": "add"}
    design = ctx(settings, code=False)
    design.config["guardrails"] = "Name every table the feature writes."
    prompt = common.system_prompt(design)
    assert "### This agent\nName every table the feature writes." in prompt
    assert "Never touch com.acme.common." not in prompt  # the project's are for steps that change code
    coder = ctx(settings)
    coder.config["guardrails"] = "Keep the router under 200 lines."
    prompt = common.system_prompt(coder)
    assert prompt.index("### Project") < prompt.index("### This agent")
    assert "Keep the router under 200 lines." in cg.reminder(coder)


def test_agent_guardrails_are_saved_with_the_agent(client):
    agent = client.get("/api/agents/modular_monolith").json()
    assert agent["guardrails"] == ""
    r = client.put("/api/agents/modular_monolith", json={**agent, "guardrails": "Keep the router under 200 lines."})
    assert r.status_code == 200 and r.json()["guardrails"] == "Keep the router under 200 lines."
    assert client.get("/api/agents/modular_monolith").json()["guardrails"] == "Keep the router under 200 lines."
    builtin = {b["key"]: b for b in client.get("/api/guardrails").json()["builtin"]}
    assert builtin["module_guardrails"]["checks"] == ["module_guardrails"]
    assert client.post("/api/agents/modular_monolith/reset").json()["guardrails"] == ""
