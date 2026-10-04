"""What every AI stage agent shares, whichever provider runs it: the opening message, tool execution with logging,
the required-documents check and artifact collection."""

import json
from pathlib import Path

from app.agents.base_agent import AgentContext, ArtifactOut
from app.agents.tools import ToolError, Toolbox

INLINE_LIMIT = 200_000  # characters of earlier documents put in the prompt; larger ones are left to read_file
LOG_LIMIT = 2_000

PREAMBLE = """You are one stage agent in Migration Studio, which migrates a legacy Java monolith to Spring Boot \
microservices one operation at a time. You work in a git checkout of the monolith (the workspace) using the tools \
provided; all paths are relative to the workspace root. Earlier stages' documents are in the workspace, and a \
person reviews your output before the migration moves on.

When you are done, reply with a short summary of what you produced and anything the reviewer should check, \
without calling a tool.

Stage instructions follow."""


class AgentFailed(RuntimeError):
    pass


def system_prompt(ctx: AgentContext) -> str:
    return f"{PREAMBLE}\n\n{ctx.config['instructions']}"


def java_home(ctx: AgentContext) -> str | None:
    return ctx.settings["jdk_modern_home" if ctx.config["jdk"] == "modern" else "jdk_legacy_home"] or None


def kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix in (".yaml", ".yml"):
        return "openapi" if "openapi" in path.lower() else "yaml"
    if suffix == ".java":
        return "java"
    return "text"


def prior_documents(ctx: AgentContext) -> str:
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


def brief(ctx: AgentContext) -> str:
    """The agent's opening message: the task, where to write, earlier documents, guidance, answers and feedback."""
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
        parts.append(prior_documents(ctx))
    guidance = ctx.inputs.as_markdown()
    parts.append(guidance or "## Guidance\nNo source locations or notes were provided for this domain or "
                             "operation. Explore the workspace, and ask the user if you cannot find the code.")
    if ctx.answered:
        parts.append("## Answers you already received\n" +
                     "\n\n".join(f"Q: {q}\nA: {a}" for q, a in ctx.answered))
    if ctx.feedback:
        parts.append("## Reviewer feedback on the previous attempt (address all of it)\n" + ctx.feedback)
    return "\n\n".join(parts)


def log_text(ctx: AgentContext, text: str | None) -> None:
    if text and text.strip():
        text = text.strip()
        ctx.log(text if len(text) <= LOG_LIMIT else text[:LOG_LIMIT] + "…")


def run_tool(ctx: AgentContext, toolbox: Toolbox, name: str, args: object) -> tuple[str, bool]:
    """Run one tool call; returns (result text, is_error). Errors go back to the model so it can correct itself."""
    if name in ("list_files", "read_file", "search_code") and isinstance(args, dict):
        ctx.log(f"→ {name} " + json.dumps({k: (v if len(str(v)) < 120 else str(v)[:117] + "…")
                                           for k, v in args.items()}))
    try:
        return toolbox.run(name, args), False
    except ToolError as e:
        return str(e), True
    except OSError as e:
        return f"{type(e).__name__}: {e}", True


def missing_outputs(ctx: AgentContext) -> list[str]:
    return [o for o in ctx.config["outputs"] if not (ctx.output_dir / o).is_file()]


def missing_outputs_message(ctx: AgentContext, missing: list[str]) -> str:
    out_dir = ctx.rel(ctx.output_dir)
    return ("These required documents have not been written yet: " +
            ", ".join(f"`{out_dir}/{m}`" for m in missing) + ". Write them, then finish.")


def artifacts(ctx: AgentContext, toolbox: Toolbox) -> list[ArtifactOut]:
    """This stage's documents: everything it wrote in the operation folder, plus the required outputs."""
    out_dir = ctx.rel(ctx.output_dir)
    paths = {p for p in toolbox.written if p.startswith(out_dir + "/")}
    paths |= {f"{out_dir}/{o}" for o in ctx.config["outputs"]}
    return [ArtifactOut(name=Path(p).name, path=p, kind=kind(p))
            for p in sorted(paths) if (ctx.workspace / p).is_file()]
