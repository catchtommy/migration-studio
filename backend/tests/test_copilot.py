"""The Copilot stage agent against a scripted stand-in for the GitHub Copilot SDK client and session."""

import asyncio
from types import SimpleNamespace

import pytest
from copilot.session_events import AssistantMessageData, SessionErrorData

from app.agents.common import AgentFailed
from app.agents.copilot_agent import CopilotAgent
from app.copilot_service import runtime_env
from tests.test_agents_domains import ctx  # noqa: F401  (fixture)

OUT = ".migration/loan/approve"


def call(tools, name, **args):
    """Invoke a registered tool the way the runtime would."""
    tool = next(t for t in tools if t.name == name)
    return tool.handler(SimpleNamespace(arguments=args, tool_name=name))


class FakeSession:
    def __init__(self, script, kwargs):
        self.script, self.kwargs, self.prompts, self.disconnected = script, kwargs, [], False

    async def send_and_wait(self, prompt, timeout):
        self.prompts.append(prompt)
        await self.script.pop(0)(self)

    def emit(self, data):
        self.kwargs["on_event"](SimpleNamespace(data=data))

    async def disconnect(self):
        self.disconnected = True


class FakeClient:
    def __init__(self, script, signed_in=True, models=("gpt-5",)):
        self.script, self.signed_in, self.model_ids, self.sessions = script, signed_in, models, []

    async def get_auth_status(self):
        return SimpleNamespace(isAuthenticated=self.signed_in, login="octocat")

    async def list_models(self):
        return [SimpleNamespace(id=m, supported_reasoning_efforts=["low", "high"]) for m in self.model_ids]

    async def create_session(self, **kwargs):
        session = FakeSession(self.script, kwargs)
        self.sessions.append(session)
        return session


class FakeService:
    def __init__(self, client):
        self._client = client

    def call(self, fn, timeout=None):
        return asyncio.run(fn())

    async def client(self):
        return self._client


def message(text):
    return AssistantMessageData(content=text, message_id="m")


def test_copilot_agent_runs_the_stage_with_only_this_apps_tools(ctx):  # noqa: F811
    ctx, logs, asked = ctx

    async def turn1(session):
        tools = session.kwargs["tools"]
        result = await call(tools, "search_code", pattern="approve")
        assert result.result_type == "success" and "LoanController.java:2:" in result.text_result_for_llm
        answer = await call(tools, "ask_user", question="Where is the rule?")
        assert answer.text_result_for_llm == "It is in LoanService"
        bad = await call(tools, "read_file", path="../../etc/passwd")
        assert bad.result_type == "failure" and "outside" in bad.error
        session.emit(message("Read the code."))  # finishes without the required document → self-check

    async def turn2(session):
        await call(session.kwargs["tools"], "write_file", path=f"{OUT}/as-is.md", content="# AS-IS\n")
        session.emit(message("Done."))

    client = FakeClient([turn1, turn2])
    ctx.config["model"] = "gpt-5"
    artifacts = CopilotAgent("as_is_doc", FakeService(client)).run(ctx)

    assert [a.path for a in artifacts] == [f"{OUT}/as-is.md"]
    [session] = client.sessions
    kw = session.kwargs
    assert [t.name for t in kw["tools"]] == ["list_files", "read_file", "search_code", "write_file", "ask_user"]
    assert kw["available_tools"].to_list() == [f"custom:{t.name}" for t in kw["tools"]]  # no built-in tools
    assert all(t.overrides_built_in_tool for t in kw["tools"])
    assert kw["working_directory"] == str(ctx.workspace)
    assert ctx.config["instructions"] in kw["system_message"]["content"]
    assert kw["model"] == "gpt-5" and kw["reasoning_effort"] == "high"
    assert "# as_is_doc: loan / approve" in session.prompts[0] and "have not been written yet" in session.prompts[1]
    assert session.disconnected and asked == [("Where is the rule?", None)]
    assert "Read the code." in logs and any("Self-check" in line for line in logs)


def test_copilot_agent_auto_model_and_unknown_model(ctx):  # noqa: F811
    ctx, _, _ = ctx

    async def write(session):
        await call(session.kwargs["tools"], "write_file", path=f"{OUT}/as-is.md", content="x")

    client = FakeClient([write])
    CopilotAgent("as_is_doc", FakeService(client)).run(ctx)  # model "auto": Copilot chooses
    assert "model" not in client.sessions[0].kwargs and "reasoning_effort" not in client.sessions[0].kwargs

    ctx.config["model"] = "not-a-model"
    with pytest.raises(AgentFailed, match="not available to your Copilot account"):
        CopilotAgent("as_is_doc", FakeService(FakeClient([]))).run(ctx)


def test_copilot_agent_requires_sign_in(ctx):  # noqa: F811
    ctx, _, _ = ctx
    with pytest.raises(AgentFailed, match="Not signed in to GitHub Copilot"):
        CopilotAgent("as_is_doc", FakeService(FakeClient([], signed_in=False))).run(ctx)


def test_copilot_errors_fail_the_stage_when_documents_are_missing(ctx):  # noqa: F811
    ctx, _, _ = ctx

    async def error(session):
        session.emit(SessionErrorData(error_type="quota", message="Premium request limit reached"))

    with pytest.raises(AgentFailed, match="Premium request limit reached"):
        CopilotAgent("as_is_doc", FakeService(FakeClient([error]))).run(ctx)


def test_network_settings_reach_the_runtime():
    env = runtime_env({"https_proxy": "http://proxy.corp:8080", "no_proxy": ".corp", "extra_ca_file": "/ca.pem",
                       "copilot_cli_path": "/opt/copilot", "copilot_disable_websockets": True})
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://proxy.corp:8080"
    assert env["NO_PROXY"] == ".corp" and env["NODE_EXTRA_CA_CERTS"] == "/ca.pem"
    assert env["COPILOT_CLI_PATH"] == "/opt/copilot" and env["COPILOT_CLI_DISABLE_WEBSOCKET_RESPONSES"] == "1"


def test_agents_use_the_provider_chosen_in_settings(client):
    from app.agents import get_agent
    from app.agents.copilot_agent import CopilotAgent as Copilot
    from app.agents.llm import ClaudeAgent
    from app.agents.stub import StubAgent

    assert client.get("/api/settings").json()["ai_provider"] == "copilot"
    assert isinstance(get_agent("x", {"mode": "llm"}, "copilot"), Copilot)
    assert isinstance(get_agent("x", {"mode": "llm"}, "claude"), ClaudeAgent)
    assert isinstance(get_agent("x", {"mode": "stub"}, "copilot"), StubAgent)


class FakeLoginApi:
    """Stands in for the runtime's session.accounts.login API."""

    def __init__(self):
        self.calls = []

    def _begun(self, kind):
        from copilot.generated.rpc import AuthLoginBegun
        first = {"kind": "input-required", "prompt": "Enter your GitHub Enterprise Cloud host"} if kind == "proxima" \
            else {"kind": "open-url", "url": "https://github.com/login/oauth/authorize"}
        return AuthLoginBegun.from_dict({"flowId": "f1", "step": first})

    async def begin(self, request):
        self.calls.append(("begin", request.kind.value))
        return self._begun(request.kind.value)

    async def advance(self, request):
        from copilot.generated.rpc import AuthLoginStep
        self.calls.append(("advance", request.input))
        return AuthLoginStep.from_dict({"kind": "open-url", "url": f"{request.input}/login/oauth/authorize"})


@pytest.mark.parametrize("host, expected_calls, url", [
    ("https://github.com", [("begin", "githubDotCom")], "https://github.com/login/oauth/authorize"),
    ("acme.ghe.com/", [("begin", "proxima"), ("advance", "https://acme.ghe.com")],
     "https://acme.ghe.com/login/oauth/authorize"),
])
def test_browser_sign_in_goes_to_the_configured_github_host(host, expected_calls, url):
    from app.copilot_service import begin_login
    api = FakeLoginApi()
    flow_id, step = asyncio.run(begin_login(api, "github", host))
    assert api.calls == expected_calls
    assert flow_id == "f1" and step.kind.value == "open-url" and step.url == url


def test_github_host_setting_is_normalised(client):
    r = client.put("/api/settings", json={"github_host": "acme.ghe.com/"})
    assert r.json()["github_host"] == "https://acme.ghe.com"
    client.put("/api/settings", json={"github_host": "https://github.com"})
