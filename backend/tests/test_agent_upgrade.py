"""A release brings built-in agents up to its defaults without losing what people changed or added."""

import hashlib

from app import agent_store
from app.agents.defaults import DOMAIN_ARCHITECTURE, LIFT_RULES, default_config
from app.db.models import GLOBAL, SHARED, Agent, Setting
from app.db.session import session_scope

KEY = "modernise"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _Log:
    """The upgrade's log messages (its startup report), whatever logging is configured."""

    def __init__(self, monkeypatch):
        self.lines = []
        monkeypatch.setattr(agent_store.log, "info", lambda msg, *args: self.lines.append(msg % args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _upgrade(monkeypatch, old_instructions: str, default: bool = True, **fields) -> dict:
    """Make the agent look like an earlier release's (`old_instructions`, an earlier default unless not `default`,
    plus `fields`), then run the release's upgrade."""
    if default:
        monkeypatch.setitem(agent_store.PREVIOUS_DEFAULT_HASHES, KEY,
                            agent_store.PREVIOUS_DEFAULT_HASHES[KEY] | {_sha(old_instructions)})
    with session_scope() as s:
        row = s.get(Agent, (SHARED, KEY))
        before = {f: getattr(row, f) for f in ("instructions", "guardrails", "tools", "checks", "outputs", "model")}
        row.instructions = old_instructions
        for name, value in fields.items():
            setattr(row, name, value)
        s.query(Setting).filter(Setting.project == GLOBAL, Setting.key == agent_store.UPGRADE_MARKER).delete()
    try:
        with session_scope() as s:
            agent_store._upgrade_untouched_builtins(s)
        with session_scope() as s:
            row = s.get(Agent, (SHARED, KEY))
            return {f: getattr(row, f) for f in ("instructions", "guardrails", "tools", "checks", "outputs", "model")}
    finally:
        with session_scope() as s:
            row = s.get(Agent, (SHARED, KEY))
            for name, value in before.items():
                setattr(row, name, value)


def test_an_untouched_agent_gets_the_defaults_and_keeps_the_tools_and_guardrails_people_added(client, monkeypatch):
    current = default_config(KEY)
    caplog = _Log(monkeypatch)
    after = _upgrade(monkeypatch, "Old default instructions.", tools=["read_file", "my_extra_tool"],
                     guardrails="Our own rule: never touch the billing tables.", model="custom-model")
    assert after["instructions"] == current["instructions"]
    assert "my_extra_tool" in after["tools"] and all(t in after["tools"] for t in current["tools"])  # added, not reset
    assert after["guardrails"] == "Our own rule: never touch the billing tables."  # edited: kept, never duplicated
    assert after["model"] == "custom-model"
    assert "Guardrails kept as edited" in caplog.text and "upgraded to this release's instructions" in caplog.text


def test_guardrails_still_the_default_the_studio_gave_are_brought_up_to_date(client, monkeypatch):
    old_default = "The guardrails as an earlier release shipped them."
    with session_scope() as s:
        agent_store.remember_default_guardrails(s, KEY, old_default)
    after = _upgrade(monkeypatch, "Old default instructions.", guardrails=old_default)
    assert after["guardrails"] == default_config(KEY)["guardrails"]  # replaced, not joined: no rule twice
    with session_scope() as s:
        assert s.get(Setting, (GLOBAL, f"_default_guardrails:{KEY}")).value == _sha(default_config(KEY)["guardrails"])


def test_an_agent_whose_instructions_were_edited_is_left_alone(client, monkeypatch):
    caplog = _Log(monkeypatch)
    after = _upgrade(monkeypatch, "Our own instructions for this agent.", default=False, tools=["read_file"])
    assert after["instructions"] == "Our own instructions for this agent." and after["tools"] == ["read_file"]
    assert "kept as they are (their instructions were edited" in caplog.text


def test_the_domain_rules_are_stated_once_in_the_shared_texts_not_again_in_the_instructions():
    rules = ("One package per feature", "ONE domain module", "<base_package>.shared", "READ ONLY for you")
    for key in ("modular_monolith", "monolith_orchestrator", "microservice_scaffold", "orchestrator_service",
                "modernise", "modernise_and_test"):
        instructions = default_config(key)["instructions"]
        assert not [r for r in rules if r in instructions and r in DOMAIN_ARCHITECTURE + LIFT_RULES], key
        assert "This domain's" in instructions or "The lifted feature" in instructions, key  # it points to them


def test_a_shared_text_nobody_edited_follows_the_defaults_and_an_edited_one_is_kept(client):
    """Every edit made through the studio records who made it: a text without one was never edited (whatever older
    default it holds) and follows the release's; an edited one is kept; Reset makes it follow the defaults again."""
    from app import shared_texts
    from app.agents.defaults import SHARED_TEXTS
    from app.db.models import SharedText

    key = "module_extraction_format"
    default = SHARED_TEXTS[key][2]
    with session_scope() as s:
        s.get(SharedText, (SHARED, key)).text, s.get(SharedText, (SHARED, key)).updated_by = "an old default", None
    shared_texts.seed()
    with session_scope() as s:
        assert s.get(SharedText, (SHARED, key)).text == default  # never edited: upgraded

    assert client.put(f"/api/shared-texts/{key}", json={"text": "our own format"}).status_code == 200
    shared_texts.seed()
    with session_scope() as s:
        assert s.get(SharedText, (SHARED, key)).text == "our own format"  # edited: kept
    assert client.post(f"/api/shared-texts/{key}/reset").status_code == 200
    with session_scope() as s:
        row = s.get(SharedText, (SHARED, key))
        assert row.text == default and row.updated_by is None  # follows the defaults again


def test_a_shared_text_keeps_its_run_data_and_takes_none_the_studio_does_not_fill(client):
    from app.agents.defaults import SHARED_TEXTS

    text = SHARED_TEXTS["lift_rules"][2]
    r = client.put("/api/shared-texts/lift_rules", json={"text": text.replace("{{data.lifted_files}}", "")})
    assert r.status_code == 422 and "{{data.lifted_files}}" in r.json()["detail"]
    r = client.put("/api/shared-texts/lift_rules", json={"text": text + " {{data.lifted_file}}"})  # a typo
    assert r.status_code == 422 and "{{data.lifted_file}}" in r.json()["detail"]
    r = client.put("/api/shared-texts/preamble", json={"text": "Hello {{data.feature}}"})
    assert r.status_code == 422 and "takes none" in r.json()["detail"]
