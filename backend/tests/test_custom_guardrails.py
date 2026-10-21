"""Guardrails written in plain words, kept per agent: given to the agent in its prompt and with each guardrail check
result. The global and project guardrails of earlier releases are moved into the agents once."""

from types import SimpleNamespace

from sqlalchemy import select

from app import agent_store
from app import custom_guardrails as cg
from app.agents import common, defaults
from app.db.models import GLOBAL, SHARED, Agent, Setting
from app.db.session import session_scope


def ctx(guardrails: str = "", code: bool = True, instructions: str = "Do the step."):
    stage = SimpleNamespace(key="modular_monolith", code={"repository": ""} if code else None)
    return SimpleNamespace(settings={}, stage_def=stage, config={"instructions": instructions, "guardrails": guardrails})


def test_an_agent_gets_its_own_guardrails_in_the_prompt_and_with_each_check_whatever_its_step():
    for code in (True, False):
        prompt = common.system_prompt(ctx("Keep the router under 200 lines.", code=code))
        assert "## Guardrails\n" in prompt and prompt.endswith("Keep the router under 200 lines.")
        assert "Guardrails section" in cg.reminder(ctx("Keep the router under 200 lines.", code=code))
    assert "## Guardrails" not in common.system_prompt(ctx("  "))
    assert cg.reminder(ctx("")) == ""


def test_the_check_tools_point_back_to_them_rather_than_repeat_them():
    from app.agents.tools import Toolbox

    box = Toolbox.__new__(Toolbox)
    box.ctx = ctx("Never touch com.acme.common.")
    box._last_check, box._repeats = None, 0
    text = box._check("check_module_guardrails", [], lambda problems: "Module guardrails: PASS.")
    assert text.startswith("Module guardrails: PASS.") and "Guardrails section of your instructions" in text
    assert "Never touch com.acme.common." not in text  # in the prompt already: not sent again with every check


def test_only_the_built_in_checks_are_listed_and_there_is_no_global_text_any_more(client):
    r = client.get("/api/guardrails")
    assert r.status_code == 200 and set(r.json()) == {"builtin"}
    assert {b["key"] for b in r.json()["builtin"]} >= {"module_guardrails", "agent_limits"}
    assert all(b["description"] for b in r.json()["builtin"])
    assert client.put("/api/guardrails", json={"text": "x"}).status_code == 405
    for key in ("project_guardrails", "project_guardrails_mode"):
        assert client.put("/api/projects/default/settings", json={key: "add"}).status_code == 422


def test_agent_guardrails_are_saved_with_the_agent(client):
    agent = client.get("/api/agents/modular_monolith").json()
    before = agent["guardrails"]
    r = client.put("/api/agents/modular_monolith", json={**agent, "guardrails": "Keep the router under 200 lines."})
    assert r.status_code == 200 and r.json()["guardrails"] == "Keep the router under 200 lines."
    assert client.get("/api/agents/modular_monolith").json()["guardrails"] == "Keep the router under 200 lines."
    builtin = {b["key"]: b for b in client.get("/api/guardrails").json()["builtin"]}
    assert builtin["module_guardrails"]["checks"] == ["module_guardrails"]
    assert client.post("/api/agents/modular_monolith/reset").json()["guardrails"] == \
        defaults.GUARDRAIL_TEMPLATES["modular_monolith"]
    client.put("/api/agents/modular_monolith", json={**agent, "guardrails": before})


def _move(settings: dict[tuple[str, str], str]):
    """Run the move once more with these old settings stored; returns the agents' guardrails afterwards."""
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == agent_store.GUARDRAILS_MOVED_MARKER).delete()
        for (project, key), value in settings.items():
            s.merge(Setting(project=project, key=key, value=value))
        before = {(a.project, a.key): a.guardrails for a in s.scalars(select(Agent))}
    with session_scope() as s:
        agent_store._move_guardrails_to_agents(s)
    with session_scope() as s:
        after = {(a.project, a.key): a.guardrails for a in s.scalars(select(Agent))}
        left = s.query(Setting).filter(Setting.key.in_(["custom_guardrails", "project_guardrails",
                                                          "project_guardrails_mode"])).count()
        # Put the agents back as they were, and drop the copies the move made.
        for (project, key), value in after.items():
            if (project, key) in before:
                s.get(Agent, (project, key)).guardrails = before[(project, key)]
            else:
                s.delete(s.get(Agent, (project, key)))
    return before, after, left


def test_global_and_project_guardrails_move_into_the_agents_that_change_code(client):
    before, after, left = _move({(GLOBAL, "custom_guardrails"): "Never touch com.acme.common.",
                                 ("default", "project_guardrails"): "Log with AuditLogger.",
                                 ("default", "project_guardrails_mode"): "add"})
    assert left == 0  # the old settings are gone
    # A shared agent that changes code keeps its own rules and gets the global ones.
    assert after[(SHARED, "modular_monolith")] == "\n\n".join(
        t for t in (before[(SHARED, "modular_monolith")], "Never touch com.acme.common.") if t)
    # The project gets its own copy, with the rules it had in effect: global, then its own.
    assert after[("default", "modular_monolith")].endswith("Never touch com.acme.common.\n\nLog with AuditLogger.")
    # An agent that changes no code (a design step) got neither, and so gets nothing.
    assert after[(SHARED, "domain_design")] == before[(SHARED, "domain_design")]
    assert ("default", "domain_design") not in after


def test_a_project_that_replaced_the_global_guardrails_keeps_only_its_own(client):
    _, after, _ = _move({(GLOBAL, "custom_guardrails"): "Never touch com.acme.common.",
                         ("default", "project_guardrails"): "Log with AuditLogger.",
                         ("default", "project_guardrails_mode"): "replace"})
    assert after[("default", "modular_monolith")].endswith("Log with AuditLogger.")
    assert "Never touch com.acme.common." not in after[("default", "modular_monolith")]


def test_the_move_happens_once(client):
    with session_scope() as s:
        s.merge(Setting(project=GLOBAL, key="custom_guardrails", value="Late text."))
        before = s.get(Agent, (SHARED, "modular_monolith")).guardrails
    with session_scope() as s:
        agent_store._move_guardrails_to_agents(s)  # the marker is set: nothing moves
    with session_scope() as s:
        assert s.get(Agent, (SHARED, "modular_monolith")).guardrails == before
        s.query(Setting).filter(Setting.key == "custom_guardrails").delete()


def test_built_in_agents_have_their_guardrails_in_the_guardrails_box_not_the_instructions():
    for stage, sections in defaults.GUARDRAIL_SECTIONS.items():
        config = defaults.default_config(stage)
        assert config["guardrails"], stage
        for section in defaults.GUARDRAIL_SECTION_TEMPLATES[stage]:
            assert section not in config["instructions"] and section in config["guardrails"], stage
        # Nothing is lost: put back where they were, the sections give the instructions of earlier releases.
        assert defaults.without(defaults.templated(defaults.INSTRUCTIONS_WITH_GUARDRAILS[stage]),
                                defaults.GUARDRAIL_SECTION_TEMPLATES[stage]) == config["instructions"]
    assert "Hard rules for this step" in defaults.GUARDRAILS["modular_monolith"]
    assert "Hard rules for this step" not in defaults.INSTRUCTIONS["modular_monolith"]
    assert defaults.GUARDRAIL_TEMPLATES["domain_design"] == "{{modulith_guardrails}}"


def test_the_prompt_gets_the_guardrails_once_with_shared_texts_filled_in():
    config = defaults.default_config("domain_design")
    prompt = common.system_prompt(SimpleNamespace(settings={}, stage_def=None, config=config))
    rules = defaults.SHARED_TEXTS["modulith_guardrails"][2]
    assert prompt.count(rules) == 1 and prompt.index("## Guardrails") < prompt.index(rules)
    assert "{{modulith_guardrails}}" not in prompt


def test_stored_agents_get_their_guardrails_moved_out_of_their_instructions(client):
    old = defaults.templated(defaults.INSTRUCTIONS_WITH_GUARDRAILS["modular_monolith"])
    edited = old.replace("Procedure:", "Procedure (our version):")  # edited elsewhere: still moved
    with session_scope() as s:
        s.query(Setting).filter(Setting.key == agent_store.GUARDRAILS_OUT_MARKER).delete()
        row = s.get(Agent, (SHARED, "modular_monolith"))
        saved = row.instructions, row.guardrails
        row.instructions, row.guardrails = edited, "Keep the router under 200 lines."
        other = s.get(Agent, (SHARED, "monolith_orchestrator"))
        saved_other = other.instructions, other.guardrails
        other.instructions, other.guardrails = "All my own words.", ""  # nothing of the defaults left: unchanged
    try:
        with session_scope() as s:
            agent_store._move_guardrails_out_of_instructions(s)
        with session_scope() as s:
            row = s.get(Agent, (SHARED, "modular_monolith"))
            assert row.instructions == defaults.TEMPLATES["modular_monolith"].replace("Procedure:",
                                                                                     "Procedure (our version):")
            assert row.guardrails == defaults.GUARDRAIL_TEMPLATES["modular_monolith"] + \
                "\n\nKeep the router under 200 lines."
            other = s.get(Agent, (SHARED, "monolith_orchestrator"))
            assert (other.instructions, other.guardrails) == ("All my own words.", "")
    finally:
        with session_scope() as s:
            for key, (instructions, guardrails) in (("modular_monolith", saved), ("monolith_orchestrator", saved_other)):
                row = s.get(Agent, (SHARED, key))
                row.instructions, row.guardrails = instructions, guardrails
