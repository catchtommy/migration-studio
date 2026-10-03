"""The configurable Claude agent that runs a stage: instructions, model and tools come from the stage's agent config."""

import json
from pathlib import Path

import anthropic

from app.agents.base_agent import AgentContext, ArtifactOut, StageAgent
from app.agents.tools import ToolError, Toolbox, definitions

MAX_TOKENS = 64_000
INLINE_LIMIT = 200_000  # characters of earlier documents put in the prompt; larger ones are left to read_file
BETAS = ["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"]
LOG_LIMIT = 2_000

PREAMBLE = """You are one stage agent in Migration Studio, which migrates a legacy Java monolith to Spring Boot \
microservices one operation at a time. You work in a git checkout of the monolith (the workspace) using the tools \
provided; all paths are relative to the workspace root. Earlier stages' documents are in the workspace, and a \
person reviews your output before the migration moves on.

When you are done, reply with a short summary of what you produced and anything the reviewer should check, \
without calling a tool.

Stage instructions follow."""


def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


class AgentFailed(RuntimeError):
    pass


def _kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix in (".yaml", ".yml"):
        return "openapi" if "openapi" in path.lower() else "yaml"
    if suffix == ".java":
        return "java"
    return "text"


def _prior_documents(ctx: AgentContext) -> str:
    """Earlier stages' documents, grouped by stage. Contents are included when the workflow step asks for them,
    up to INLINE_LIMIT; anything beyond that is listed for the agent to read itself rather than cut short."""
    lines = ["## Documents from earlier stages",
             "Your work builds on these. Their contents are included below; read any others with read_file."
             if any(d.content is not None for d in ctx.previous_artifacts) else
             "Your work builds on these. Read them with read_file."]
    budget = INLINE_LIMIT
    stage = None
    for doc in ctx.previous_artifacts:
        if doc.stage != stage:
            stage = doc.stage
            lines.append(f"\n### {doc.stage_title}")
        if doc.content is not None and len(doc.content) <= budget:
            budget -= len(doc.content)
            lines.append(f'<document path="{doc.path}">\n{doc.content}\n</document>')
        elif doc.content is not None:
            lines.append(f"- `{doc.path}` (too large to include here; read it with read_file)")
        else:
            lines.append(f"- `{doc.path}`")
    return "\n".join(lines)


def _brief(ctx: AgentContext) -> str:
    title = ctx.stage_def.title if ctx.stage_def else ctx.config.get("title", ctx.stage)
    description = ctx.stage_def.description if ctx.stage_def else ctx.config.get("description", "")
    out_dir = ctx.rel(ctx.output_dir)
    where = f"Write this stage's documents to `{out_dir}/`."
    if ctx.config.get("outputs"):
        where += " Required: " + ", ".join(f"`{out_dir}/{o}`" for o in ctx.config["outputs"]) + "."
    parts = [
        f"# {title}: {ctx.domain} / {ctx.operation}",
        f"{description}\n\nThis is attempt {ctx.attempt}.",
        where,
    ]
    if ctx.previous_artifacts:
        parts.append(_prior_documents(ctx))
    guidance = ctx.inputs.as_markdown()
    parts.append(guidance or "## Guidance\nNo source locations or notes were provided for this domain or "
                             "operation. Explore the workspace, and ask the user if you cannot find the code.")
    if ctx.answered:
        parts.append("## Answers you already received\n" +
                     "\n\n".join(f"Q: {q}\nA: {a}" for q, a in ctx.answered))
    if ctx.feedback:
        parts.append("## Reviewer feedback on the previous attempt (address all of it)\n" + ctx.feedback)
    return "\n\n".join(parts)


def _short(args: dict) -> str:
    return json.dumps({k: (v if len(str(v)) < 120 else str(v)[:117] + "…") for k, v in args.items()})


class ClaudeAgent(StageAgent):
    def __init__(self, stage: str):
        self.stage = stage

    def run(self, ctx: AgentContext) -> list[ArtifactOut]:
        config = ctx.config
        java_home = ctx.settings["jdk_modern_home" if config["jdk"] == "modern" else "jdk_legacy_home"] or None
        toolbox = Toolbox(ctx, java_home)
        client = make_client()
        tools = definitions(config["tools"])
        system = f"{PREAMBLE}\n\n{config['instructions']}"
        messages: list[dict] = [{"role": "user", "content": _brief(ctx)}]
        checks_left = ctx.settings["max_attempts"]
        ctx.log(f"Claude agent started ({config['model']}, effort {config['effort']})")

        for _ in range(config["max_turns"]):
            with client.beta.messages.stream(
                model=config["model"],
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
            self._log_progress(ctx, response.content)

            if response.stop_reason == "refusal":
                details = response.stop_details
                raise AgentFailed(f"Claude declined to continue ({getattr(details, 'category', None) or 'refusal'})")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if tool_uses:
                truncated = response.stop_reason == "max_tokens"
                messages.append({"role": "user",
                                 "content": [self._run_tool(ctx, toolbox, b, truncated) for b in tool_uses]})
                continue
            if response.stop_reason in ("max_tokens", "pause_turn"):
                messages.append({"role": "user", "content": "Continue."})
                continue

            missing = [o for o in config["outputs"] if not (ctx.output_dir / o).is_file()]
            if not missing:
                break
            if checks_left <= 0:
                raise AgentFailed(f"Required documents still missing: {', '.join(missing)}")
            checks_left -= 1
            ctx.log(f"Self-check: missing {', '.join(missing)}; asking the agent to finish")
            out_dir = ctx.rel(ctx.output_dir)
            messages.append({"role": "user", "content": "These required documents have not been written yet: " +
                             ", ".join(f"`{out_dir}/{m}`" for m in missing) + ". Write them, then finish."})
        else:
            raise AgentFailed(f"The agent did not finish within {config['max_turns']} turns")

        return self._artifacts(ctx, toolbox)

    @staticmethod
    def _log_progress(ctx: AgentContext, content) -> None:
        for block in content:
            text = block.thinking if block.type == "thinking" else block.text if block.type == "text" else ""
            if text and text.strip():
                text = text.strip()
                ctx.log(text if len(text) <= LOG_LIMIT else text[:LOG_LIMIT] + "…")

    @staticmethod
    def _run_tool(ctx: AgentContext, toolbox: Toolbox, block, truncated: bool) -> dict:
        result = {"type": "tool_result", "tool_use_id": block.id}
        if truncated:
            # The input may have been cut off mid-stream; never act on it.
            return {**result, "is_error": True,
                    "content": "Not run: your response hit the output limit, so this input may be incomplete. "
                               "Re-issue it with less content per call (e.g. write large files in parts)."}
        if block.name in ("list_files", "read_file", "search_code"):
            ctx.log(f"→ {block.name} {_short(block.input) if isinstance(block.input, dict) else ''}")
        try:
            return {**result, "content": toolbox.run(block.name, block.input)}
        except ToolError as e:
            return {**result, "is_error": True, "content": str(e)}
        except OSError as e:
            return {**result, "is_error": True, "content": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _artifacts(ctx: AgentContext, toolbox: Toolbox) -> list[ArtifactOut]:
        """This stage's documents: everything it wrote in the operation folder, plus the required outputs."""
        out_dir = ctx.rel(ctx.output_dir)
        paths = {p for p in toolbox.written if p.startswith(out_dir + "/")}
        paths |= {f"{out_dir}/{o}" for o in ctx.config["outputs"]}
        return [ArtifactOut(name=Path(p).name, path=p, kind=_kind(p))
                for p in sorted(paths) if (ctx.workspace / p).is_file()]
