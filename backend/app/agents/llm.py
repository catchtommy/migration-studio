"""Stage agent backed by the Claude API (AI provider "claude" in Settings)."""

from app.agents import common
from app.agents.base_agent import AgentContext, ArtifactOut, StageAgent
from app.agents.common import AgentFailed
from app.agents.tools import Toolbox, definitions
from app.config import settings

MAX_TOKENS = 64_000
BETAS = ["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"]
DEFAULT_MODEL = "claude-opus-5-5"  # used when an agent's model is "auto"


def make_client():
    import anthropic  # only needed when Claude is the AI provider

    if not (settings.anthropic_api_key or settings.anthropic_auth_token):
        raise AgentFailed("The AI provider is the Claude API, but the backend has no Anthropic API key. Set "
                          "ANTHROPIC_API_KEY in backend/.env (or the backend's environment) and restart it, or switch "
                          "Settings → AI provider to GitHub Copilot. Then retry the run.")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key or None,
                               auth_token=settings.anthropic_auth_token or None)


class ClaudeAgent(StageAgent):
    def __init__(self, stage: str):
        self.stage = stage

    def run(self, ctx: AgentContext) -> list[ArtifactOut]:
        config = ctx.config
        model = DEFAULT_MODEL if config["model"] in ("", "auto") else config["model"]
        toolbox = Toolbox(ctx, common.toolchain_home(ctx))
        client = make_client()
        tools = definitions(config["tools"])
        system = common.system_prompt(ctx)
        messages: list[dict] = [{"role": "user", "content": common.brief(ctx)}]
        checks_left = ctx.settings["max_attempts"]
        ctx.log(f"Claude agent started ({model}, effort {config['effort']})")

        for _ in range(config["max_turns"]):
            with client.beta.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive", "display": "updates"},
                output_config={"effort": config["effort"]},
                cache_control={"type": "ephemeral"},
                betas=BETAS,
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
            # Append the full content unchanged: thinking blocks must be passed back as they came.
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                common.log_text(ctx, block.thinking if block.type == "thinking" else
                                block.text if block.type == "text" else None)

            if response.stop_reason == "refusal":
                details = response.stop_details
                raise AgentFailed(f"Claude declined to continue ({getattr(details, 'category', None) or 'refusal'})")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if tool_uses:
                truncated = response.stop_reason == "max_tokens"
                messages.append({"role": "user",
                                 "content": [self._tool_result(ctx, toolbox, b, truncated) for b in tool_uses]})
                continue
            if response.stop_reason in ("max_tokens", "pause_turn"):
                messages.append({"role": "user", "content": "Continue."})
                continue

            problems = common.verify(ctx)
            if not problems:
                break
            if checks_left <= 0:
                raise AgentFailed("Verification still failing: " + "; ".join(problems[:5]))
            checks_left -= 1
            ctx.log(f"Self-check: {len(problems)} problem(s) to fix, e.g. {problems[0]}")
            messages.append({"role": "user", "content": common.verify_message(problems)})
        else:
            raise AgentFailed(f"The agent did not finish within {config['max_turns']} turns")

        return common.artifacts(ctx, toolbox)

    @staticmethod
    def _tool_result(ctx: AgentContext, toolbox: Toolbox, block, truncated: bool) -> dict:
        result = {"type": "tool_result", "tool_use_id": block.id}
        if truncated:
            # The input may have been cut off mid-stream; never act on it.
            return {**result, "is_error": True,
                    "content": "Not run: your response hit the output limit, so this input may be incomplete. "
                               "Re-issue it with less content per call (e.g. write large files in parts)."}
        text, is_error = common.run_tool(ctx, toolbox, block.name, block.input)
        return {**result, "content": text, **({"is_error": True} if is_error else {})}
