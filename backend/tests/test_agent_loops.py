"""AI agents don't loop on guardrail checks they can't pass: a step that can't finish fails before the agent starts,
the agent isn't sent back for problems it can't fix or for earlier steps' documents, and it stops when the same
problems come back."""

import subprocess

import pytest

from app import module_guardrails as mg
from app.agents import llm
from app.agents.defaults import default_config
from app.agents.tools import ToolError, Toolbox
from app.guardrail_problems import Blocker, blocker
from app.orchestrator.stages import StageDef
from tests.test_agents_domains import ScriptedClient, _reply, _text, _tool, ctx  # noqa: F401


def _as(ctx, stage, title, code=None, outputs=None):
    ctx.stage = stage
    ctx.config = {**default_config(stage), "mode": "llm", **({"outputs": outputs} if outputs is not None else {})}
    ctx.stage_def = StageDef(stage, title, "agent", "", code=code)
    return ctx


def _never_called(monkeypatch):
    client = ScriptedClient([])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    return client


def test_a_code_agent_on_a_documents_only_step_fails_before_it_starts(ctx, monkeypatch):
    ctx, logs, _ = ctx
    _as(ctx, "monolith_integration", "Monolith integration", code=None)
    client = _never_called(monkeypatch)
    with pytest.raises(llm.AgentFailed, match="Monolith integration changes code, but its step isn't set to"):
        llm.ClaudeAgent("monolith_integration").run(ctx)
    assert client.requests == []  # no tokens spent
    assert any(line.startswith("Not started") for line in logs)


def test_a_service_step_set_to_the_monolith_fails_before_it_starts(ctx, monkeypatch):
    ctx, _, _ = ctx
    _as(ctx, "microservice_scaffold", "Spring Boot microservice", code={"repository": ""})  # the monolith
    client = _never_called(monkeypatch)
    with pytest.raises(llm.AgentFailed, match="must change code in the service's own repository"):
        llm.ClaudeAgent("microservice_scaffold").run(ctx)
    assert client.requests == []


def test_the_orchestrator_service_without_its_orchestrator_fails_before_it_starts(ctx, monkeypatch):
    ctx, _, _ = ctx
    service = ctx.workspace / ".studio/repos/orch"
    subprocess.run(["git", "init", "-q", str(service)], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=service, check=True)
    _as(ctx, "orchestrator_service", "Orchestrator microservice", code={"repository": "https://git.example/orch.git"})
    ctx.code_dir = ".studio/repos/orch"
    (ctx.output_dir / "orchestrator-service-extraction.yaml").write_text(
        "feature: approve\ndomain: loan\norchestrator: service\nmodule_commit: abc\nopenapi: o.yaml\n"
        "operations: [{method: 'A#b', operation_id: b}]\n")
    client = _never_called(monkeypatch)
    with pytest.raises(llm.AgentFailed, match="declares no orchestrator"):
        llm.ClaudeAgent("orchestrator_service").run(ctx)
    assert client.requests == []


def test_an_earlier_steps_invalid_document_does_not_bounce_a_later_agent(ctx, monkeypatch):
    ctx, _, _ = ctx
    out = ".migration/loan/approve"
    (ctx.workspace / out).mkdir(parents=True, exist_ok=True)
    (ctx.workspace / out / "domain-dependencies.yaml").write_text("decision: [\n")  # broken, by an earlier step
    client = ScriptedClient([
        _reply(_tool("write_file", path=f"{out}/as-is.md", content="# AS-IS\n")),
        _reply(_text("Done."), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    artifacts = llm.ClaudeAgent("as_is_doc").run(ctx)
    assert [a.name for a in artifacts] == ["as-is.md"] and len(client.requests) == 2  # not sent back


def test_the_same_problems_twice_end_the_step_instead_of_using_every_attempt(ctx, monkeypatch):
    ctx, _, _ = ctx
    ctx.settings["max_attempts"] = 5
    client = ScriptedClient([_reply(_text("Done."), stop="end_turn")] * 6)
    monkeypatch.setattr(llm, "make_client", lambda: client)
    with pytest.raises(llm.AgentFailed, match="The same problems remain after the agent tried to fix them"):
        llm.ClaudeAgent("as_is_doc").run(ctx)
    assert len(client.requests) == 2  # sent back once; the same answer again ends it


def test_a_problem_the_agent_cant_fix_ends_the_step_at_once(ctx, monkeypatch):
    ctx, _, _ = ctx
    out = ".migration/loan/approve"
    from app.agents import common
    monkeypatch.setattr(common, "guardrail_problems", lambda c, checks: [
        blocker("Guardrail: 14 files outside the modules need changing. Stop and ask the user")])
    monkeypatch.setattr(common, "preflight", lambda c: [])  # appears only once the agent has worked
    client = ScriptedClient([
        _reply(_tool("write_file", path=f"{out}/as-is.md", content="# AS-IS\n")),
        _reply(_text("Done."), stop="end_turn"),
    ])
    monkeypatch.setattr(llm, "make_client", lambda: client)
    with pytest.raises(llm.AgentFailed, match="can't finish until something outside the agent's work changes"):
        llm.ClaudeAgent("as_is_doc").run(ctx)
    assert len(client.requests) == 2  # not sent back to fix it


def test_a_check_tool_says_when_to_stop(ctx, monkeypatch):
    ctx, _, _ = ctx
    _as(ctx, "modular_monolith", "Module in monolith", code={"repository": ""})
    tools = Toolbox(ctx, None)
    monkeypatch.setattr(mg, "for_context", lambda c: ["SQL changed in Broker.xml"])
    first, second, third = (tools.run("check_module_guardrails", {}) for _ in range(3))
    assert "same result" not in first and "same result" not in second
    assert "This is the same result as the last two times" in third
    monkeypatch.setattr(mg, "for_context", lambda c: [Blocker("The step's starting commit is unknown")])
    out = tools.run("check_module_guardrails", {})
    assert "You can't fix these" in out and "Don't try to work around them" in out


def test_the_comparison_works_whatever_git_thinks_of_ignored_paths(tmp_path, monkeypatch):
    """The studio's folders are ignored by .git/info/exclude; naming them in `git add` made some gits fail ("paths
    are ignored"). The comparison names no paths, and leaves the studio's documents out even when one was forced in."""
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root, check=True,
                                    capture_output=True)
    (root / "App.java").write_text("class App {}\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    (root / ".git/info/exclude").write_text("/.studio/\n/.migration/\n")
    (root / ".migration/loan/approve").mkdir(parents=True)
    (root / ".migration/loan/approve/module-extraction.yaml").write_text("feature: approve\n")
    git("add", "-f", ".migration/loan/approve/module-extraction.yaml")  # what the agent did
    (root / "App.java").write_text("class App { int x; }\n")

    calls = []
    real = mg._git
    monkeypatch.setattr(mg, "_git", lambda r, *args, env=None: calls.append(args) or real(r, *args, env=env))
    changes = mg.changes(root, base)
    assert [c.path for c in changes] == ["App.java"]  # the document left out
    adds = [a for a in calls if "add" in a]
    assert adds and all(not any(str(x).startswith(":") or x == "--" for x in a) for a in adds)


def test_agents_may_not_force_ignored_files_into_git(ctx):
    ctx, _, _ = ctx
    tools = _as(ctx, "modular_monolith", "Module in monolith", code={"repository": ""}) and Toolbox(ctx, None)
    with pytest.raises(ToolError, match="ignored files are never forced in"):
        tools.run("run_command", {"command": "git add -f .migration/loan/approve/module-extraction.yaml"})


def test_a_failing_tool_reports_its_error_to_the_agent_instead_of_ending_it(ctx, monkeypatch):
    """ask_user whose question was cancelled (the server restarted while it waited), or any tool raising, comes back
    to the model as an error it can read, not as a crash of the tool call or the step."""
    from app.agents import common
    from app.orchestrator.questions import QuestionAbandoned

    ctx, logs, _ = ctx
    tools = Toolbox(ctx, None)

    def abandoned(question, options=None):
        raise QuestionAbandoned("Server stopped while waiting for an answer. Retry to ask again.")

    ctx.ask = abandoned
    text, is_error = common.run_tool(ctx, tools, "ask_user", {"question": "Which domain owns BANK_ACCOUNT?"})
    assert is_error and "could not be answered" in text and "Don't ask it again" in text
    monkeypatch.setattr(Toolbox, "check_boundaries", lambda self: 1 / 0, raising=False)
    ctx.config = {**ctx.config, "tools": [*ctx.config["tools"], "check_boundaries"]}
    text, is_error = common.run_tool(ctx, tools, "check_boundaries", {})
    assert is_error and "ZeroDivisionError" in text
