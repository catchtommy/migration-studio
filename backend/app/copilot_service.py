"""GitHub Copilot connection: one Copilot runtime (via the GitHub Copilot SDK) shared by sign-in, the model list and
agent sessions. The SDK is asyncio-based, so it runs on its own event loop thread; callers use `call()`.

Sign-in uses the person's own GitHub account and Copilot subscription, on the GitHub host from Settings (github.com
or a GitHub Enterprise Cloud host with data residency, *.ghe.com):
- an existing sign-in on this machine (Copilot CLI or GitHub CLI) is picked up automatically,
- "Sign in with GitHub" runs GitHub's browser sign-in on that host (or Microsoft Entra ID),
- or a GitHub token can be stored.
The runtime keeps the credential (in the OS keychain where available), as the Copilot CLI does.

Corporate networks: network settings (proxy, extra CA certificates, Copilot CLI path, WebSockets) are passed to the
runtime's environment; changing them restarts the runtime.
"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app import settings_store

log = logging.getLogger(__name__)
T = TypeVar("T")

GITHUB_HOST = "https://github.com"


def normalize_host(host: str | None) -> str:
    """'acme.ghe.com' or 'https://acme.ghe.com/' -> 'https://acme.ghe.com'."""
    host = (host or "").strip().rstrip("/") or GITHUB_HOST
    return host if host.startswith(("https://", "http://")) else f"https://{host}"


def is_github_dot_com(host: str) -> bool:
    return normalize_host(host) in (GITHUB_HOST, "https://www.github.com")


async def begin_login(api, kind: str, host: str):
    """Start a browser sign-in. "github" signs in on github.com or, for any other host, on GitHub Enterprise Cloud
    (runtime kind "proxima"), answering the runtime's host prompt with the configured host."""
    from copilot.generated.rpc import AuthLoginAdvanceRequest, AuthLoginBeginRequest, LoginProviderKind

    wire = "entra" if kind == "entra" else "githubDotCom" if is_github_dot_com(host) else "proxima"
    begun = await api.begin(AuthLoginBeginRequest(kind=LoginProviderKind(wire)))
    step = begun.step
    if wire == "proxima" and step.kind.value == "input-required":
        step = await api.advance(AuthLoginAdvanceRequest(flow_id=begun.flow_id, input=normalize_host(host)))
    return begun.flow_id, step
MODELS_TTL = 300  # seconds


class CopilotUnavailable(RuntimeError):
    """The Copilot runtime could not start or the user is not signed in."""


def runtime_env(settings: dict) -> dict[str, str]:
    env = dict(os.environ)
    if settings.get("https_proxy"):
        env["HTTPS_PROXY"] = env["https_proxy"] = settings["https_proxy"]
        env["HTTP_PROXY"] = env["http_proxy"] = settings["https_proxy"]
    if settings.get("no_proxy"):
        env["NO_PROXY"] = env["no_proxy"] = settings["no_proxy"]
    if settings.get("extra_ca_file"):
        env["NODE_EXTRA_CA_CERTS"] = settings["extra_ca_file"]
    if settings.get("copilot_disable_websockets"):
        env["COPILOT_CLI_DISABLE_WEBSOCKET_RESPONSES"] = "1"
    if settings.get("copilot_cli_path"):
        env["COPILOT_CLI_PATH"] = settings["copilot_cli_path"]
    return env


def _step(step) -> dict[str, Any]:
    result = step.result
    return {
        "kind": step.kind.value,
        "url": step.url,
        "prompt": step.prompt,
        "message": step.message,
        "result": None if result is None else {"status": result.status.value, "host": result.host,
                                               "login": result.login},
    }


class CopilotService:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._client = None
        self._client_lock: asyncio.Lock | None = None
        self._login_session = None
        self._models: tuple[float, list[dict]] | None = None

    # --- event loop plumbing ------------------------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="copilot", daemon=True).start()
                self._loop = loop
            return self._loop

    def call(self, fn: Callable[[], Awaitable[T]], timeout: float | None = 120) -> T:
        """Run a coroutine on the Copilot loop from any thread and wait for its result."""
        return asyncio.run_coroutine_threadsafe(fn(), self._ensure_loop()).result(timeout)

    async def client(self):
        if self._client_lock is None:
            self._client_lock = asyncio.Lock()
        async with self._client_lock:
            if self._client is None:
                from copilot import CopilotClient

                settings = settings_store.get_all()
                env = runtime_env(settings)
                # The runtime download (first start) happens in this process, so it needs the proxy too.
                for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
                    if key in env:
                        os.environ[key] = env[key]
                client = CopilotClient(env=env, log_level="error",
                                       client_info={"application_name": "migration-studio"})
                try:
                    await client.start()
                except Exception as e:
                    raise CopilotUnavailable(f"The GitHub Copilot runtime could not start: {e}. On a corporate "
                                             "network, check the proxy and certificate settings, or set the "
                                             "Copilot CLI path to an installed copilot binary.") from e
                self._client = client
            return self._client

    async def _stop(self) -> None:
        client, self._client, self._login_session, self._models = self._client, None, None, None
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception("Stopping the Copilot runtime failed")

    def restart(self) -> None:
        """Apply changed network settings: the runtime starts again on next use."""
        if self._loop is not None:
            self.call(self._stop)

    def shutdown(self) -> None:
        if self._loop is not None:
            try:
                self.call(self._stop, timeout=20)
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None

    # --- account ------------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        async def go():
            client = await self.client()
            auth = await client.get_auth_status()
            return {"available": True, "signed_in": bool(auth.isAuthenticated), "login": auth.login,
                    "host": auth.host, "auth_type": auth.authType, "message": auth.statusMessage}
        try:
            return self.call(go, timeout=180)  # the first start may download the runtime
        except Exception as e:
            return {"available": False, "signed_in": False, "login": None, "host": None, "auth_type": None,
                    "message": str(e)}

    async def _login_api(self):
        from copilot.session import PermissionHandler

        if self._login_session is None:
            client = await self.client()
            self._login_session = await client.create_session(on_permission_request=PermissionHandler.approve_all)
        return self._login_session.rpc.accounts.login

    def login_begin(self, kind: str) -> dict[str, Any]:
        host = settings_store.get("github_host")

        async def go():
            api = await self._login_api()
            flow_id, step = await begin_login(api, kind, host)
            return {"flow_id": flow_id, "step": _step(step)}
        return self.call(go)

    def login_advance(self, flow_id: str, value: str | None) -> dict[str, Any]:
        from copilot.generated.rpc import AuthLoginAdvanceRequest

        async def go():
            step = await (await self._login_api()).advance(AuthLoginAdvanceRequest(flow_id=flow_id, input=value))
            if step.kind.value == "completed":
                self._models = None
            return _step(step)
        return self.call(go, timeout=600)  # waits while the person completes the browser sign-in

    def login_cancel(self, flow_id: str) -> None:
        from copilot.generated.rpc import AuthLoginCancelRequest

        async def go():
            await (await self._login_api()).cancel(AuthLoginCancelRequest(flow_id=flow_id))
        self.call(go)

    def login_with_token(self, token: str, host: str) -> None:
        from copilot.generated.rpc import AccountLoginRequest

        async def go():
            client = await self.client()
            await client.rpc.account.login(AccountLoginRequest(
                host=normalize_host(host or settings_store.get("github_host")), token=token))
            self._models = None
        self.call(go)

    def logout(self) -> None:
        from copilot.generated.rpc import AccountLogoutRequest

        async def go():
            client = await self.client()
            current = await client.rpc.account.get_current_auth()
            if current.auth_info is not None:
                await client.rpc.account.logout(AccountLogoutRequest(auth_info=current.auth_info))
            self._models = None
        self.call(go)

    def models(self) -> list[dict[str, Any]]:
        """Models available to the signed-in account (cached briefly). Empty when not signed in."""
        if self._models and time.time() - self._models[0] < MODELS_TTL:
            return self._models[1]

        async def go():
            client = await self.client()
            return [{"id": m.id, "name": m.name, "efforts": m.supported_reasoning_efforts or []}
                    for m in await client.list_models()]
        try:
            models = self.call(go, timeout=180)
        except Exception:
            return []
        self._models = (time.time(), models)
        return models


_service: CopilotService | None = None


def get_service() -> CopilotService:
    global _service
    if _service is None:
        _service = CopilotService()
    return _service
