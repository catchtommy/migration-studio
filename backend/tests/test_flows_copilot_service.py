"""The GitHub Copilot runtime wrapper (copilot_service.py) against a stand-in client: account status, models, token
sign-in and sign-out, network settings given to the runtime, and a runtime that can't start."""

from types import SimpleNamespace

import pytest

from app import copilot_service
from app.copilot_service import CopilotService, runtime_env


class FakeClient:
    def __init__(self, signed_in=True, models_fail=False):
        self.signed_in, self.models_fail, self.calls, self.stopped = signed_in, models_fail, [], False
        outer = self

        class Account:
            async def login(self, request):
                outer.calls.append(("login", request.host, request.token))

            async def get_current_auth(self):
                return SimpleNamespace(auth_info="who" if outer.signed_in else None)

            async def logout(self, request):
                outer.calls.append(("logout", request.auth_info))

        self.rpc = SimpleNamespace(account=Account())

    async def get_auth_status(self):
        return SimpleNamespace(isAuthenticated=self.signed_in, login="octocat", host="https://github.com",
                               authType="token", statusMessage="ok")

    async def list_models(self):
        self.calls.append(("models",))
        if self.models_fail:
            raise RuntimeError("not signed in")
        return [SimpleNamespace(id="gpt-x", name="GPT X", supported_reasoning_efforts=["low", "high"])]

    async def stop(self):
        self.stopped = True


@pytest.fixture
def service():
    svc = CopilotService()
    yield svc
    svc.shutdown()


def test_the_runtime_gets_the_network_settings(monkeypatch):
    env = runtime_env({"https_proxy": "http://proxy:8080", "no_proxy": "localhost", "extra_ca_file": "/ca.pem",
                       "copilot_disable_websockets": True, "copilot_cli_path": "/opt/copilot"})
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://proxy:8080" and env["NO_PROXY"] == "localhost"
    assert env["NODE_EXTRA_CA_CERTS"] == "/ca.pem" and env["COPILOT_CLI_DISABLE_WEBSOCKET_RESPONSES"] == "1"
    assert env["COPILOT_CLI_PATH"] == "/opt/copilot"
    plain = runtime_env({})
    assert "COPILOT_CLI_PATH" not in plain or plain["COPILOT_CLI_PATH"] != "/opt/copilot"


def test_the_account_status_and_models_come_from_the_runtime(service):
    fake = FakeClient()
    service._client = fake
    status = service.status()
    assert status["available"] and status["signed_in"] and status["login"] == "octocat"
    assert service.models() == [{"id": "gpt-x", "name": "GPT X", "efforts": ["low", "high"]}]
    service.models()
    assert fake.calls.count(("models",)) == 1  # cached briefly


def test_models_are_empty_when_the_runtime_cant_list_them(service):
    service._client = FakeClient(models_fail=True)
    assert service.models() == []


def test_signing_in_with_a_token_and_out_again(client, service):
    fake = FakeClient()
    service._client = fake
    service.login_with_token("ghp_secret", "https://corp.ghe.com/")
    service.logout()
    assert fake.calls[0][0] == "login" and fake.calls[0][2] == "ghp_secret" and "corp.ghe.com" in fake.calls[0][1]
    assert fake.calls[1] == ("logout", "who")
    fake.signed_in = False
    service.logout()  # nobody signed in: nothing to sign out
    assert len(fake.calls) == 2


def test_a_restart_stops_the_runtime_to_start_it_again_on_next_use(service):
    fake = FakeClient()
    service._client = fake
    service.status()  # the loop is running
    service.restart()
    assert fake.stopped and service._client is None


def test_a_runtime_that_cant_start_is_reported_as_unavailable(client, service, monkeypatch):
    import copilot

    class Broken:
        def __init__(self, **_kw):
            pass

        async def start(self):
            raise OSError("proxy refused the download")

    monkeypatch.setattr(copilot, "CopilotClient", Broken)
    status = service.status()
    assert status["available"] is False and "could not start" in status["message"]
    assert "proxy refused the download" in status["message"]
    assert isinstance(copilot_service.CopilotUnavailable("x"), Exception)
