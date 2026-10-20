import itertools
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import common, llm
from app.agents.base_agent import AgentContext
from app.agent_store import builtin_default
from app.agents.defaults import default_config
from app.agents.tools import ToolError, Toolbox
from app.config import settings
from app.tools import git_ops
from tests.conftest import document, start_run, wait_for

_ids = itertools.count()


def test_builtin_agents_are_editable_and_resettable(client):
    agents = client.get("/api/agents").json()
    assert [a["key"] for a in agents if a["builtin"]] == [
        "bounded_contexts", "domain_features", "as_is_doc", "deep_dive", "domain_dependencies",
        "linked_domain_contracts", "domain_design", "orchestrator_design", "integration_design", "modular_monolith",
        "monolith_orchestrator", "microservice_scaffold", "orchestrator_service", "independent_tests", "modernise", "service_tests", "monolith_integration"]  # superseded built-ins are hidden

    body = {**builtin_default("deep_dive"), "instructions": "Focus on the batch jobs.", "outputs": ["notes.md"]}
    r = client.put("/api/agents/deep_dive", json=body)
    assert r.status_code == 200, r.text
    assert client.get("/api/agents/deep_dive").json()["instructions"] == "Focus on the batch jobs."

    reset = client.post("/api/agents/deep_dive/reset").json()
    assert reset["outputs"] == default_config("deep_dive")["outputs"]


@pytest.mark.parametrize("change", [{"tools": ["rm_rf"]}, {"outputs": ["../escape.md"]},
                                    {"max_turns": 0}, {"title": ""}, {"model": ""}])
def test_invalid_agent_config_is_refused(client, change):
    r = client.put("/api/agents/as_is_doc", json={**builtin_default("as_is_doc"), **change})
    assert r.status_code == 422


def test_domain_crud_and_run_validation(client):
    key = f"dom{next(_ids)}"
    assert client.post("/api/domains", json={"key": key, "name": "Dom"}).status_code == 201
    assert client.post("/api/domains", json={"key": key, "name": "Dom"}).status_code == 409
    assert client.post("/api/domains", json={"key": "Bad Key", "name": "x"}).status_code == 422

    r = client.put(f"/api/domains/{key}", json={"agent_inputs": {"no_such_stage": {"paths": []}}})
    assert r.status_code == 422
    r = client.put(f"/api/domains/{key}", json={"context": "Shared", "agent_inputs": {
        "as_is_doc": {"paths": [" src/a ", ""], "notes": " look here "}}})
    assert r.json()["agent_inputs"]["as_is_doc"] == {"paths": ["src/a"], "notes": "look here"}

    assert start_run(client, {"domain": "nope", "operation": "x"}).status_code == 422
    assert client.delete(f"/api/domains/{key}").status_code == 204
    assert client.get(f"/api/domains/{key}").status_code == 404


def test_domain_and_operation_inputs_reach_the_agent_and_docs(client):
    key = f"dom{next(_ids)}"
    client.post("/api/domains", json={"key": key, "name": "Lending", "description": "Loans and repayments"})
    client.put(f"/api/domains/{key}", json={"context": "Oracle schema LND", "agent_inputs": {
        "as_is_doc": {"paths": ["src/main/java/com/acme/loan/**"], "notes": "Start at LoanController"}}})
    r = start_run(client, {"domain": key, "operation": "approveLoan",
                                       "inputs": {"paths": ["LoanController.approve"], "notes": "Batch too"}})
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})

    docs = client.get(f"/api/domains/{key}/docs").json()
    assert [d["run"]["operation"] for d in docs] == ["approveLoan"]
    as_is = next(a for a in docs[0]["artifacts"] if a["stage"] == "as_is_doc")
    content = client.get(f"/api/artifacts/{as_is['id']}").json()["content"]
    for expected in ["Loans and repayments", "Oracle schema LND", "src/main/java/com/acme/loan/**",
                     "Start at LoanController", "LoanController.approve", "Batch too"]:
        assert expected in content
    deep = next(a for a in docs[0]["artifacts"] if a["stage"] == "deep_dive")
    assert "Start at LoanController" not in client.get(f"/api/artifacts/{deep['id']}").json()["content"]  # stage-specific

    assert client.delete(f"/api/domains/{key}").status_code == 409  # has runs
    summary = next(d for d in client.get("/api/domains").json() if d["key"] == key)
    assert summary["runs"] == 1


def test_agent_question_pauses_the_run_until_answered(client, monkeypatch):
    monkeypatch.setattr(settings, "stub_questions", True)
    r = start_run(client, {"domain": "loan", "operation": f"q{next(_ids)}"})
    run_id = r.json()["id"]

    run = wait_for(client, run_id, {"awaiting_input"})
    assert run["current_stage"] == "as_is_doc"
    [q] = client.get(f"/api/runs/{run_id}/questions").json()
    assert q["status"] == "open" and q["options"]

    answer = {"answer": "Also check the nightly batch", "answered_by": "tester"}
    assert client.post(f"/api/questions/{q['id']}/answer", json=answer).status_code == 200
    assert client.post(f"/api/questions/{q['id']}/answer", json=answer).status_code == 409

    wait_for(client, run_id, {"awaiting_input"})  # deep dive asks too
    q2 = client.get(f"/api/runs/{run_id}/questions").json()[1]
    assert q2["stage"] == "deep_dive"
    client.post(f"/api/questions/{q2['id']}/answer", json={"answer": "", "answered_by": "tester"})  # skip
    run = wait_for(client, run_id, {"awaiting_approval"})

    content = document(run, "as_is_doc").read_text()
    assert "Also check the nightly batch" in content
    deep = document(run, "deep_dive").read_text()
    assert "Use your best judgement" in deep
    events = [e for e in client.get(f"/api/runs/{run_id}/questions").json()]
    assert [e["status"] for e in events] == ["answered", "answered"]


# --- the Claude agent loop, against a scripted client -------------------------------------------------------------

def _tool(name, **args):
    return SimpleNamespace(type="tool_use", id=f"tu_{next(_ids)}", name=name, input=args)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _reply(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None)


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    @contextmanager
    def _stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        reply = self.replies.pop(0)
        yield SimpleNamespace(get_final_message=lambda: reply)


@pytest.fixture
def ctx(tmp_path):
    ws = tmp_path / "ws"
    git_ops.init_workspace(ws, None, "migration/loan/approve")
    (ws / "src").mkdir()
    (ws / "src" / "LoanController.java").write_text("class LoanController {\n  void approve() {}\n}\n")
    logs, asked = [], []
    config = {**default_config("as_is_doc"), "mode": "llm"}

    def ask(question, options=None):
        asked.append((question, options))
        return "It is in LoanService"

    return AgentContext(run_id="r", domain="loan", operation="approve", stage="as_is_doc", attempt=1, workspace=ws,
                        feedback=None, settings={"max_attempts": 2, "jdk_legacy_home": "", "jdk_modern_home": ""},
                        log=logs.append, config=config, ask=ask), logs, asked


def test_claude_agent_uses_tools_asks_and_writes_outputs(ctx, monkeypatch):
    ctx, logs, asked = ctx
    out = ".migration/loan/approve"
    client = ScriptedClient([
        _reply(_tool("search_code", pattern="approve"), _tool("read_file", path="src/LoanController.java")),
        _reply(_tool("ask_user", question="Where is the approval rule?", options=["LoanService", "SQL"])),
        _reply(_text("Done reading."), stop="end_turn"),  # finishes without the required document: self-check
        _reply(_tool("write_file", path=f"{out}/as-is.md", content="# AS-IS\n"),
               _tool("write_file", path=f"{out}/extra.md", content="notes\n")),
        _reply(_text("Wrote the AS-IS document."), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)

    artifacts = llm.ClaudeAgent("as_is_doc").run(ctx)

    assert [a.path for a in artifacts] == [f"{out}/as-is.md", f"{out}/extra.md"]
    assert asked == [("Where is the approval rule?", ["LoanService", "SQL"])]
    first_results = client.requests[1]["messages"][-1]["content"]
    assert "LoanController.java:2:" in first_results[0]["content"]
    assert "void approve()" in first_results[1]["content"]
    assert client.requests[2]["messages"][-1]["content"][0]["content"] == "It is in LoanService"
    assert "has not been written yet" in client.requests[3]["messages"][-1]["content"]
    assert any("Self-check" in line for line in logs)
    req = client.requests[0]
    assert req["model"] == llm.DEFAULT_MODEL and req["output_config"] == {"effort": "high"}  # "auto" → default
    from app import shared_texts
    assert "{{legacy}}" in ctx.config["instructions"]  # the shared text is filled in
    assert shared_texts.expand(ctx.config["instructions"]) in req["system"] and "{{" not in req["system"]
    assert [t["name"] for t in req["tools"]] == ["list_files", "read_file", "search_code", "write_file", "ask_user"]


def test_claude_agent_does_not_run_tools_from_a_truncated_response(ctx, monkeypatch):
    ctx, _, _ = ctx
    client = ScriptedClient([
        _reply(_tool("write_file", path="half.md", content="cut off"), stop="max_tokens"),
        _reply(_tool("write_file", path=".migration/loan/approve/as-is.md", content="ok")),
        _reply(_text("Done"), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    llm.ClaudeAgent("as_is_doc").run(ctx)
    assert not (ctx.workspace / "half.md").exists()
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_claude_agent_fails_when_outputs_stay_missing(ctx, monkeypatch):
    ctx, _, _ = ctx
    monkeypatch.setattr(llm, "make_client", lambda: ScriptedClient([_reply(_text("Done"), stop="end_turn")] * 3))
    with pytest.raises(llm.AgentFailed, match="as-is.md"):
        llm.ClaudeAgent("as_is_doc").run(ctx)


def test_tools_stay_inside_the_workspace_and_respect_the_config(ctx):
    ctx, _, _ = ctx
    tools = Toolbox(ctx, None)
    with pytest.raises(ToolError, match="outside"):
        tools.run("read_file", {"path": "../../etc/passwd"})
    with pytest.raises(ToolError, match=".git"):
        tools.run("write_file", {"path": ".git/config", "content": "x"})
    with pytest.raises(ToolError, match="not enabled"):
        tools.run("run_command", {"command": "ls"})  # analysis agents have no shell by default
    with pytest.raises(ToolError, match="Missing"):
        tools.run("read_file", {})
    assert tools.run("list_files", {"pattern": "**/*.java"}) == "src/LoanController.java"


def test_a_step_that_writes_documents_only_cannot_change_code(ctx):
    from app.orchestrator.stages import StageDef
    ctx, _, _ = ctx
    ctx.config = {**ctx.config, "tools": [*ctx.config["tools"], "edit_file", "run_command"]}  # even when enabled
    ctx.stage_def = StageDef("linked_domain_contracts", "Linked-domain contracts", "agent", "")
    tools = Toolbox(ctx, None)
    for name, args in [("write_file", {"path": "src/LoanPort.java", "content": "x"}),
                       ("edit_file", {"path": "src/LoanController.java", "old_text": "approve", "new_text": "x"}),
                       ("write_file", {"path": "contracts/loan.yaml", "content": "x"})]:
        with pytest.raises(ToolError, match="documents only"):
            tools.run(name, args)
    with pytest.raises(ToolError, match="documents only"):
        tools.run("run_command", {"command": "touch src/Changed.java"})
    assert not (ctx.workspace / "src" / "Changed.java").exists()
    assert tools.run("write_file", {"path": ".migration/loan/approve/contracts/loan.yaml", "content": "x"}).startswith("Wrote")

    ctx.stage_def = StageDef("modular_monolith", "Modular monolith", "agent", "", code={"repository": ""})
    assert Toolbox(ctx, None).run("write_file", {"path": "src/LoanPort.java", "content": "x"}).startswith("Wrote")


def test_claude_agent_brief_includes_earlier_documents(ctx, monkeypatch):
    from app.agents.base_agent import PriorDocument
    ctx, _, _ = ctx
    ctx.previous_artifacts = [
        PriorDocument("as_is_doc", "AS-IS documentation", "a/as-is.md", "# AS-IS\nflow"),
        PriorDocument("deep_dive", "Deep dive", "a/deep-dive.md", "x" * 50),
        PriorDocument("deep_dive", "Deep dive", "a/scenarios.md", None),
    ]
    monkeypatch.setattr(common, "INLINE_LIMIT", 30)
    brief = common.brief(ctx)
    assert '### AS-IS documentation\n<document path="a/as-is.md">\n# AS-IS\nflow\n</document>' in brief
    assert "- `a/deep-dive.md` (too large to include here; read it with read_file)" in brief
    assert "- `a/scenarios.md`" in brief
    assert brief.index("### AS-IS documentation") < brief.index("### Deep dive")


AGENT_MD = """---
name: as-is-documenter
description: Documents legacy flows from JSP to SQL
tools: ['read', 'search', 'web']
model: claude-sonnet-4.5
mcp-servers: {}
---

You document how one operation works today.
"""


def test_agent_md_is_parsed_into_agent_fields(client):
    r = client.post("/api/agents/parse-agent-md", json={"content": AGENT_MD})
    assert r.status_code == 200, r.text
    parsed = r.json()
    assert parsed["title"] == "As is documenter" and parsed["description"] == "Documents legacy flows from JSP to SQL"
    assert parsed["instructions"] == "You document how one operation works today."
    assert parsed["tools"] == ["ask_user", "read_file", "list_files", "search_code"]
    assert parsed["model"] == "claude-sonnet-4.5"
    assert any("web" in w for w in parsed["warnings"]) and any("mcp-servers" in w for w in parsed["warnings"])

    no_tools = client.post("/api/agents/parse-agent-md", json={"content": "---\ndescription: x\n---\nDo it.\n"}).json()
    assert "tools" not in no_tools and "model" not in no_tools  # the agent keeps its current tools and model
    shell = client.post("/api/agents/parse-agent-md", json={"content": "---\ntools: read, shell, edit\n---\nGo\n"})
    assert {"run_command", "edit_file", "check_boundaries"} <= set(shell.json()["tools"])

    for bad in ("No frontmatter", "---\n: [\n---\nx", "---\nname: x\n---\n"):
        assert client.post("/api/agents/parse-agent-md", json={"content": bad}).status_code == 422


def test_every_agent_round_trips_through_agent_md_text(client):
    fields = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs",
              "max_turns", "checks")
    for agent in client.get("/api/agents").json():
        text = client.get(f"/api/agents/{agent['key']}/agent.md").text
        parsed = client.post("/api/agents/parse-agent-md", json={"content": text}).json()
        same = {f: parsed[f] for f in fields if f != "tools"}
        assert same == {f: (agent[f].strip() if f == "instructions" else agent[f])
                        for f in fields if f != "tools"}, agent["key"]
        assert set(parsed["tools"]) == set(agent["tools"]), agent["key"]  # order doesn't matter
        assert parsed["warnings"] == []
        rendered = client.post("/api/agents/render-agent-md", json={f: agent[f] for f in fields}).json()["content"]
        assert rendered == text

    r = client.get("/api/agents/deep_dive/agent.md")
    assert 'filename="deep-dive.agent.md"' in r.headers["content-disposition"]
    assert r.text.startswith("---\nname: Deep dive\n")


def test_agent_is_saved_from_agent_md_text(client):
    before = client.get("/api/agents/modernise").json()
    text = client.get("/api/agents/modernise/agent.md").text
    edited = (text.replace("effort: high", "effort: low")
                  .replace("- run_command\n", "")
                  .replace(before["instructions"].strip(), "Modernise carefully."))
    r = client.put("/api/agents/modernise/agent-md", json={"content": edited})
    assert r.status_code == 200, r.text
    saved = r.json()["agent"]
    assert saved["effort"] == "low" and "run_command" not in saved["tools"]
    assert saved["instructions"] == "Modernise carefully."
    assert saved["checks"] == before["checks"] and saved["outputs"] == before["outputs"]

    # A plain Copilot agent.md (no metadata) keeps the app-only settings.
    plain = "---\nname: Modernisation\ndescription: Java 25\ntools: [read, edit]\n---\nUpgrade it.\n"
    saved = client.put("/api/agents/modernise/agent-md", json={"content": plain}).json()["agent"]
    assert saved["effort"] == "low" and saved["jdk"] == before["jdk"] and saved["max_turns"] == before["max_turns"]
    assert "ask_user" in saved["tools"] and "run_command" not in saved["tools"]

    for bad, reason in [("metadata:\n  effort: extreme", "effort"), ("metadata:\n  max-turns: 0", "max-turns"),
                        ("metadata:\n  checks: spelling", "checks"), ("tools: [\n", "line")]:
        r = client.put("/api/agents/modernise/agent-md", json={"content": f"---\n{bad}\n---\nGo\n"})
        assert r.status_code == 422 and reason in r.json()["detail"], (bad, r.json())
    assert client.post("/api/agents/modernise/reset").status_code == 200


def test_domain_with_runs_is_deleted_only_with_its_runs(client):
    key = f"scratch{next(_ids)}"
    client.post("/api/domains", json={"key": key, "name": "Scratch"})
    run = start_run(client, {"domain": key, "operation": "tryout"}).json()
    run = wait_for(client, run["id"], {"awaiting_approval"})
    listing = client.post(f"/api/domains/{key}/discover-features").json()
    wait_for(client, listing["id"], {"awaiting_approval"})

    r = client.delete(f"/api/domains/{key}")
    assert r.status_code == 409 and "with its runs" in r.json()["detail"]
    assert client.delete(f"/api/domains/{key}?with_runs=true").status_code == 204
    assert client.get(f"/api/domains/{key}").status_code == 404
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert not Path(run["workspace"]).exists()
    assert all(r["domain"] != key for r in client.get("/api/runs").json())


def test_a_migration_is_scoped_to_its_feature_not_the_domains_others(client, monkeypatch):
    """Create, update and view of a bank account share one legacy controller: a run for update tells every agent
    which entry points are its own and which features are out of scope."""
    from app.agents import common, stub

    client.post("/api/domains", json={"key": "bank", "name": "Bank accounts", "description": ""})
    for key, name, method in (("create-bank-account", "Create bank account", "create"),
                              ("update-bank-account", "Update bank account", "update"),
                              ("view-bank-account", "View bank account", "view")):
        client.post("/api/domains/bank/features", json={
            "key": key, "name": name, "entry_points": [f"BankAccountController#{method}"],
            "calls": ["customer"] if method == "create" else []})
    seen = {}
    real_run = stub.StubAgent.run

    def run(self, ctx):
        seen.setdefault(ctx.stage, (ctx.inputs.scope_markdown(), common.brief(ctx)))
        return real_run(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)
    r = client.post("/api/runs", json={"domain": "bank", "operation": "update-bank-account"})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    client.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"})  # on to the design steps
    wait_for(client, run_id, {"awaiting_approval"}, timeout=60)
    scope, brief = seen["as_is_doc"]
    assert "## Feature scope: Update bank account (`update-bank-account`)" in scope
    assert "Entry points: `BankAccountController#update`" in scope
    own, others = scope.split("### Other features")
    assert "BankAccountController#update" in own and "create" not in own
    assert "Create bank account (`create-bank-account`): `BankAccountController#create`" in others
    assert "View bank account (`view-bank-account`): `BankAccountController#view`" in others
    assert "domain_design" in seen
    assert "## Feature scope" in brief and "Stay on this feature" in brief  # every agent's brief carries it
    for design_step in ("domain_design", "orchestrator_design", "linked_domain_contracts"):
        if design_step in seen:  # design agents get the same rules (some steps may be skipped by the scope decision)
            assert "Design (contracts, domain, orchestration" in seen[design_step][1]

    from app.agents.defaults import default_config
    finder = default_config("domain_dependencies")["instructions"]
    assert "Trace only from this feature's entry points" in finder and "Left out (other" in finder


def test_shared_texts_are_edited_reset_and_follow_new_defaults_until_edited(client, monkeypatch):
    import hashlib
    from app import shared_texts
    from app.agents.defaults import SHARED_TEXTS
    from app.db.models import SHARED, SharedText
    from app.db.session import session_scope

    texts = {t["key"]: t for t in client.get("/api/shared-texts").json()}
    assert "Modulith design: Domain" in texts["rest_rules"]["used_by"] and not texts["rest_rules"]["customised"]
    r = client.put("/api/shared-texts/rest_rules", json={"text": "Our REST rules."})
    assert r.status_code == 200 and r.json()["customised"]
    assert "Our REST rules." in shared_texts.expand("{{rest_rules}}")
    assert client.put("/api/shared-texts/rest_rules", json={"text": "See {{legacy}}"}).status_code == 422
    assert client.put("/api/shared-texts/nope", json={"text": "x"}).status_code == 404
    assert client.post("/api/shared-texts/rest_rules/reset").json()["text"] == SHARED_TEXTS["rest_rules"][2]

    agent = client.get("/api/agents/domain_design").json()
    r = client.put("/api/agents/domain_design", json={**agent, "instructions": "Use {{rest_rulez}}"})
    assert r.status_code == 422 and "rest_rulez" in r.json()["detail"]

    # A release changes a default: a text still holding an earlier default follows it; an edited one is kept.
    old = "Earlier REST rules."
    monkeypatch.setitem(shared_texts.PREVIOUS_DEFAULT_HASHES, "rest_rules", {hashlib.sha256(old.encode()).hexdigest()})
    for value, expected in ((old, SHARED_TEXTS["rest_rules"][2]), ("Edited rules.", "Edited rules.")):
        with session_scope() as s:
            s.get(SharedText, (SHARED, "rest_rules")).text = value
        shared_texts.seed()
        assert shared_texts.text("rest_rules") == expected
    client.post("/api/shared-texts/rest_rules/reset")
