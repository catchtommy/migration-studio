"""Stage agent backed by GitHub Copilot (AI provider "copilot" in Settings), using the signed-in user's account.

Each stage runs as one Copilot session in the run workspace. Only this app's sandboxed tools (app/agents/tools.py)
are available, as enabled on the agent; Copilot's built-in shell and file tools are switched off. Tool calls run on
worker threads, so ask_user can wait for a person without blocking other runs.
"""

import asyncio

from app.agents import common
from app.agents.base_agent import AgentContext, ArtifactOut, StageAgent
from app.agents.common import AgentFailed
from app.agents.tools import SCHEMAS, Toolbox
from app.copilot_service import CopilotService, get_service

SESSION_TIMEOUT = 6 * 3600  # seconds one message may take, including time spent waiting for answers


def _tools(ctx: AgentContext, toolbox: Toolbox) -> tuple[list, list[str]]:
    from copilot.tools import Tool, ToolResult

    def handler_for(name: str):
        async def handler(invocation):
            text, is_error = await asyncio.to_thread(common.run_tool, ctx, toolbox, name, invocation.arguments)
            if is_error:
                return ToolResult(text_result_for_llm=text, result_type="failure", error=text)
            return ToolResult(text_result_for_llm=text, result_type="success")
        return handler

    enabled = common.tools_of(ctx)
    names = [n for n in SCHEMAS if n in enabled]
    tools = [Tool(name=n, description=SCHEMAS[n]["description"], parameters=SCHEMAS[n]["input_schema"],
                  handler=handler_for(n), overrides_built_in_tool=True, skip_permission=True) for n in names]
    return tools, names


class CopilotAgent(StageAgent):
    def __init__(self, stage: str, service: CopilotService | None = None):
        self.stage = stage
        self.service = service or get_service()

    def run(self, ctx: AgentContext) -> list[ArtifactOut]:
        return self.service.call(lambda: self._run(ctx), timeout=None)

    async def _run(self, ctx: AgentContext) -> list[ArtifactOut]:
        from copilot import ToolSet
        from copilot.session import PermissionHandler
        from copilot.session_events import AssistantMessageData, SessionErrorData

        client = await self.service.client()
        auth = await client.get_auth_status()
        if not auth.isAuthenticated:
            raise AgentFailed("Not signed in to GitHub Copilot. Sign in on the Settings page, then retry.")

        config = ctx.config
        toolbox = Toolbox(ctx, common.toolchain_home(ctx))
        common.preflight_or_fail(ctx)  # before the session starts: a step that can't finish costs nothing
        tools, names = _tools(ctx, toolbox)
        allowed = ToolSet()
        for name in names:
            allowed.add_custom(name)

        options: dict = {}
        model = config["model"] if config["model"] not in ("", "auto") else None
        if model:
            options["model"] = model
            info = next((m for m in await client.list_models() if m.id == model), None)
            if info is None:
                raise AgentFailed(f"Model {model!r} is not available to your Copilot account. Pick another model "
                                  "for this agent on the Agents page.")
            if config["effort"] in (info.supported_reasoning_efforts or []):
                options["reasoning_effort"] = config["effort"]

        errors: list[str] = []

        def on_event(event) -> None:
            data = event.data
            if isinstance(data, AssistantMessageData):
                common.log_text(ctx, data.content)
            elif isinstance(data, SessionErrorData):
                errors.append(data.message)
                ctx.log(f"Copilot error: {data.message}")

        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,  # only this app's tools are available
            tools=tools,
            available_tools=allowed,
            system_message={"mode": "append", "content": common.system_prompt(ctx)},
            working_directory=str(ctx.workspace),
            skip_custom_instructions=True,  # instructions come from the Agents page, not files in the monolith
            on_event=on_event,
            **options,
        )
        ctx.log(f"Copilot agent started as {auth.login} ({model or 'auto model'})")
        checks_left, previous = ctx.settings["max_attempts"], None
        prompt = common.brief(ctx)
        try:
            while True:
                errors.clear()
                await session.send_and_wait(prompt, timeout=SESSION_TIMEOUT)
                problems = common.finish_check(ctx, toolbox.written, previous)
                if not problems:
                    break
                previous = problems
                if errors:
                    raise AgentFailed(f"Copilot: {errors[-1]}")
                if checks_left <= 0:
                    raise AgentFailed("Verification still failing: " + "; ".join(problems[:5]))
                checks_left -= 1
                ctx.log(f"Self-check: {len(problems)} problem(s) to fix, e.g. {problems[0]}")
                prompt = common.verify_message(problems)
        except TimeoutError:
            raise AgentFailed(f"The Copilot agent did not finish within {SESSION_TIMEOUT // 3600} hours")
        finally:
            await session.disconnect()
        return common.artifacts(ctx, toolbox)
