"""What every AI stage agent shares, whichever provider runs it: the opening message, tool execution with logging,
the required-documents check and artifact collection."""

import json
import threading
from pathlib import Path

from app import (boundary_check, contracts_store, discovery_store, module_guardrails, orchestrator_guardrails,
                 orchestrator_service_guardrails, release_guardrails, service_guardrails, test_guardrails)
from app.agents.base_agent import AgentContext, ArtifactOut
from app.agents.tools import ToolError, Toolbox
from app.guardrail_problems import Blocker, blocker, blockers

INLINE_LIMIT = 200_000  # characters of earlier documents put in the prompt; larger ones are left to read_file
LOG_LIMIT = 2_000



class AgentFailed(RuntimeError):
    pass


class RunStopped(AgentFailed):
    """An admin stopped the run (to delete it, or start its feature over with another workflow)."""


# Runs being stopped: their agent ends at its next turn or tool call (app/run_cleanup.py).
_stopping: set[str] = set()
_stopping_lock = threading.Lock()


def request_stop(run_id: str, on: bool = True) -> None:
    with _stopping_lock:
        (_stopping.add if on else _stopping.discard)(run_id)


def stop_requested(run_id: str) -> bool:
    with _stopping_lock:
        return run_id in _stopping


def raise_if_stopped(ctx: AgentContext) -> None:
    if stop_requested(ctx.run_id):
        raise RunStopped("Stopped by an admin")


def system_prompt(ctx: AgentContext) -> str:
    """The preamble and the agent's instructions, with the shared texts they include (app/shared_texts.py)."""
    from app import shared_texts

    from app import custom_guardrails

    values = shared_texts.texts()
    prompt = f"{shared_texts.expand(values['preamble'], values)}\n\n{shared_texts.expand(ctx.config['instructions'], values)}"
    own = custom_guardrails.prompt_section(ctx)
    return f"{prompt}\n\n{own}" if own else prompt


def toolchain_home(ctx: AgentContext) -> str | None:
    """The toolchain this stage builds and tests with: the monolith's (legacy) or the target's (modern)."""
    return ctx.settings.get("jdk_modern_home" if ctx.config["jdk"] == "modern" else "jdk_legacy_home") or None


def project_brief(ctx: AgentContext) -> str:
    """The project's language and stacks, so agents work in it whatever their instructions assume."""
    from app.settings_store import LANGUAGES

    language = ctx.settings.get("language") or "java"
    toolchain = "target" if ctx.config.get("jdk") == "modern" else "legacy (the monolith's)"
    return (f"## Project\nLanguage: {LANGUAGES.get(language, language)}. Monolith: "
            f"{ctx.settings.get('source_stack') or 'not described'}. Target: "
            f"{ctx.settings.get('target_stack') or 'not described'}. Commands you run use the {toolchain} "
            "toolchain.")


def documentation_brief(ctx: AgentContext) -> str:
    """Reviewed AS-IS documentation published in the monolith (docs/<domain>/<feature>/): read it before the code."""
    folder = (ctx.settings.get("monolith_docs_dir") or "docs").strip().strip("/")
    base = ctx.legacy_dir or ctx.workspace
    if not folder or not (base / folder).is_dir():
        return ""
    feature = f"{folder}/{ctx.domain}/{ctx.operation}"
    lines = ["## Existing documentation",
             f"The monolith has reviewed AS-IS documentation of features under `{folder}/<domain>/<feature>/`. Read the "
             "relevant parts before the code: it saves you re-reading code that is already explained."]
    if (base / feature).is_dir():
        lines.append(f"This feature's is in `{feature}/`.")
        if ctx.stage_def is not None and ctx.stage_def.publish_docs:
            lines.append("Start from those documents and update them where the code changed, rather than "
                         "re-analysing everything.")
    return "\n".join(lines)


def repository_brief(ctx: AgentContext) -> str:
    """Where the monolith is, and where this step changes code (when it does, or the run has other repositories)."""
    code = (ctx.stage_def.code if ctx.stage_def else None)
    if ctx.legacy_dir is not None:
        lines = ["## Repositories",
                 f"The monolith's code ({(ctx.legacy or {}).get('branch') or 'default branch'} at "
                 f"{(ctx.legacy or {}).get('sha', '')[:10]}) is read-only in this run: read it at its usual paths "
                 "(e.g. `src/...`) with the file tools; commands can read it under $STUDIO_LEGACY_DIR. Never change "
                 "it. The studio's documents are under `.migration/`."]
    elif not ctx.repositories and not code:
        return ""
    else:
        lines = ["## Repositories",
                 "The workspace root is the monolith's checkout; the studio's documents are under `.migration/`."]
    for r in ctx.repositories:
        lines.append(f"- `{r['path']}/`: {r['url']} (branch cut from {r.get('base_branch') or 'its default branch'})")
    if code:
        target = "the monolith (the workspace root)" if ctx.code_dir == "." else f"`{ctx.code_dir}/`"
        lines.append(f"This step changes code in {target}; commands you run start there. Read the other repositories "
                     "and the documents as you need, but change code only in it.")
    else:
        lines.append("This step writes documents only; don't change code in any repository.")
    return "\n".join(lines)


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


def refresh_brief(ctx: AgentContext) -> str:
    """For a restart from a completed stage: work from the existing documents rather than starting over."""
    existing = "\n".join(f"- `{p}`" for p in ctx.previous_outputs) or "- (none recorded; check the documents folder)"
    return ("## Re-run of a completed step: update, don't start over\n"
            "This step already ran and its documents are in the workspace. It is being re-run because its "
            "instructions, settings or inputs may have changed. Read your previous documents first:\n"
            f"{existing}\n\n"
            "Keep everything that is still right. Change or add only what the current instructions and the current "
            "inputs (including earlier steps' documents, which may have been updated too) require, and edit the "
            "existing files in place rather than rewriting them. If nothing needs to change, leave the files as "
            "they are and say so.")


def resume_brief(ctx: AgentContext) -> str:
    """For a retry after the step stopped: carry on from the work the stopped attempt kept, rather than redo it."""
    r = ctx.resume or {}
    error = (r.get("error") or "no reason recorded").strip()
    lines = ["## Continue where the last attempt stopped",
             f"Attempt {r.get('attempt')} of this step stopped before it finished: {error[:2000]}",
             "What it did is kept, so don't start over: check what is there, keep what is right, finish what is "
             "missing and fix what made it stop."]
    if r.get("documents"):
        lines.append("Its documents, in place (read them before you write; edit them rather than rewrite them):\n"
                     + "\n".join(f"- `{p}`" for p in r["documents"]))
    if r.get("code_kept") and (r.get("commits") or r.get("files")):
        lines.append("Its code changes, committed on the branch: the checks count them as this step's.")
        if r.get("commits"):
            lines.append("Commits (newest first):\n" + "\n".join(f"- {c}" for c in r["commits"]))
        if r.get("files"):
            more = len(r["files"]) - 50
            lines.append("Files changed:\n" + "\n".join(f"- `{f}`" for f in r["files"][:50])
                         + (f"\n- … and {more} more" if more > 0 else ""))
    elif ctx.stage_def is not None and ctx.stage_def.code and not r.get("code_kept"):
        lines.append("Its code changes are not in the workspace any more (it was rebuilt since): redo them, using "
                     "its documents for what it had decided.")
    return "\n\n".join(lines)


def amend_brief(ctx: AgentContext) -> str:
    """For a rejection that asks for a specific change: make that change, and nothing else."""
    existing = "\n".join(f"- `{p}`" for p in ctx.previous_outputs) or "- (check the documents folder)"
    return ("## Amendment: make only the change the reviewer asked for\n"
            "A reviewer read this step's work and rejected it for the specific change below. Everything else in it "
            "was accepted as it is. Your documents (and code) from before are in place:\n"
            f"{existing}\n\n"
            "Rules for this amendment:\n"
            "- Find exactly what the change concerns: which document, which section, which entries, which code.\n"
            "- Change only that. Every other document, section, entry, contract and line of code stays exactly as it "
            "is: don't rewrite, regenerate, reformat, reorder, rename or \"improve\" anything else.\n"
            "- Edit the specific lines with edit_file; don't rewrite a whole file with write_file.\n"
            "- Read only what the change needs; don't re-analyse the code from scratch.\n"
            "- If the change has to be reflected somewhere else to stay consistent (a renamed contract key that "
            "another file refers to, say), change those lines too, and only those.\n"
            "- If the request is unclear, ask with ask_user rather than guess.\n"
            "- Finish with a short summary that lists exactly what you changed, file by file.")


def brief(ctx: AgentContext) -> str:
    """The agent's opening message: the task, where to write, earlier documents, guidance, answers and feedback."""
    title = ctx.stage_def.title if ctx.stage_def else ctx.config.get("title", ctx.stage)
    description = ctx.stage_def.description if ctx.stage_def else ctx.config.get("description", "")
    out_dir = ctx.rel(ctx.output_dir)
    where = f"Write this stage's documents to `{out_dir}/`."
    if ctx.config.get("outputs"):
        where += " Required: " + ", ".join(f"`{out_dir}/{o}`" for o in ctx.config["outputs"]) + "."
    parts = [
        f"# {title}: {discovery_store.subject(ctx.domain, ctx.operation)}",
        f"{description}\n\nThis is attempt {ctx.attempt}.",
        project_brief(ctx),
        where,
    ]
    repos = repository_brief(ctx)
    if repos:
        parts.append(repos)
    docs = documentation_brief(ctx)
    if docs:
        parts.append(docs)
    if ctx.amend:
        parts.append(amend_brief(ctx))
    elif ctx.refresh:
        parts.append(refresh_brief(ctx))
    if ctx.resume:
        parts.append(resume_brief(ctx))
    if ctx.previous_artifacts:
        parts.append(prior_documents(ctx))
    guidance = ctx.inputs.as_markdown()
    parts.append(guidance or "## Guidance\nNo source locations or notes were provided for this domain or "
                             "operation. Explore the workspace, and ask the user if you cannot find the code.")
    if ctx.catalogue:
        parts.append(ctx.catalogue)
    if ctx.answered:
        parts.append("## Answers you already received\n" +
                     "\n\n".join(f"Q: {q}\nA: {a}" for q, a in ctx.answered))
    if ctx.feedback:
        heading = ("## The change the reviewer asked for (the only thing to change)" if ctx.amend else
                   "## Notes for this re-run (what changed, what to update)" if ctx.refresh else
                   "## Reviewer feedback on the previous attempt (address all of it)")
        parts.append(f"{heading}\n{ctx.feedback}")
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
    from app.orchestrator.questions import QuestionAbandoned

    if stop_requested(ctx.run_id):  # an admin is stopping the run: no more work
        return "Not run: an admin is stopping this run. Don't call any more tools; finish now.", True
    try:
        return toolbox.run(name, args), False
    except ToolError as e:
        return str(e), True
    except QuestionAbandoned as e:  # the server stopped (or restarted) while the question waited for an answer
        ctx.log(f"{name}: {e}")
        return (f"Your question could not be answered: {e}. Don't ask it again in this attempt; finish with a summary "
                "that says what you needed to know, and the step can be retried to ask again."), True
    except Exception as e:  # noqa: BLE001 - a tool's own failure goes back to the model, never ends the agent
        ctx.log(f"{name} failed: {type(e).__name__}: {e}")
        return f"The {name} tool failed: {type(e).__name__}: {e}", True


def required_checks(ctx: AgentContext) -> set[str]:
    """The checks an agent must pass: those it is configured with, and always a built-in agent's own guardrail checks
    (they can't be switched off: the agent's page has only its guardrails in plain words)."""
    from app.agents.defaults import _CHECKS

    return set(ctx.config.get("checks") or []) | set(_CHECKS.get(ctx.stage, []))


# The tool that runs each check, for the agent to check its own work while it goes (the check runs again when it
# finishes). An agent gets the tools of the checks it must pass, and no others: they are not chosen on its page.
CHECK_TOOLS = {"boundary": "check_boundaries", "module_guardrails": "check_module_guardrails",
               "orchestrator_guardrails": "check_orchestrator_guardrails",
               "service_guardrails": "check_service_guardrails",
               "orchestrator_service_guardrails": "check_orchestrator_service_guardrails",
               "acceptance_test_guardrails": "check_test_guardrails", "acceptance_suite_unchanged": "check_test_guardrails",
               "service_test_guardrails": "check_test_guardrails", "modernise_guardrails": "check_release_guardrails",
               "integration_guardrails": "check_release_guardrails"}


def tools_of(ctx: AgentContext) -> list[str]:
    """The tools the agent gets: those enabled on its page (the working tools), and the check tools of its checks."""
    own = [t for t in ctx.config.get("tools") or [] if not t.startswith("check_")]
    checks = [CHECK_TOOLS[c] for c in sorted(required_checks(ctx)) if c in CHECK_TOOLS]
    return list(dict.fromkeys([*own, *checks]))


def _tagged(prefix: str, problems: list[str]) -> list[str]:
    """`prefix` before each problem, keeping the ones the agent can't fix marked as such."""
    return [(blocker if isinstance(p, Blocker) else str)(f"{prefix}{p}") for p in problems]


def guardrail_problems(ctx: AgentContext, checks: set[str]) -> list[str]:
    """The problems of the guardrail checks among `checks`, for the agent's work so far."""
    problems = []
    if "boundary" in checks:
        problems += _tagged("Boundary: ", boundary_check.check(ctx.workspace, ctx.domain, ctx.service_dir,
                                                                ctx.ownership))
    if "module_guardrails" in checks:
        problems += _tagged("Guardrail: ", module_guardrails.for_context(ctx))
    if "orchestrator_guardrails" in checks:
        problems += _tagged("Guardrail: ", orchestrator_guardrails.for_context(ctx))
    if "orchestrator_service_guardrails" in checks:
        problems += _tagged("Guardrail: ", orchestrator_service_guardrails.for_context(ctx))
    problems += _tagged("Guardrail: ", test_guardrails.for_context(ctx, sorted(checks)))
    problems += _tagged("Guardrail: ", release_guardrails.for_context(ctx, sorted(checks)))
    if "service_guardrails" in checks:
        problems += _tagged("Guardrail: ", service_guardrails.for_context(ctx))
    return problems


def preflight(ctx: AgentContext) -> list[str]:
    """What stops the step before an AI agent starts, so no tokens go to a step that can't finish: a code agent on a
    step not set to change code, or a guardrail problem that needs the workflow, an earlier step, the environment or
    a person (guardrail_problems.Blocker)."""
    from app.agents.tools import GIT_CHECKS

    checks = required_checks(ctx)
    if checks & GIT_CHECKS and ctx.stage_def is not None and not ctx.stage_def.code:
        return [blocker(f"{ctx.stage_def.title} changes code, but its step isn't set to: set its Changes code on the "
                        "Workflows page (In the project's monolith, or In another repository), then retry")]
    return blockers(guardrail_problems(ctx, checks))


def verify(ctx: AgentContext, written: set[str] | None = None) -> list[str]:
    """Everything that must be fixed before the agent may finish: missing required documents, invalid structured
    documents of its own (the required ones and those it wrote: `written`, workspace paths), and the guardrail checks
    it must pass (required_checks). Earlier steps' documents are not judged again here."""
    out_dir = ctx.rel(ctx.output_dir)
    checks = required_checks(ctx)
    own = None if written is None else set(ctx.config["outputs"]) | {
        p[len(out_dir) + 1:] for p in written if p.startswith(out_dir + "/")}
    problems = [f"Required document `{out_dir}/{o}` has not been written yet."
                for o in ctx.config["outputs"] if not (ctx.output_dir / o).is_file()]
    problems += contracts_store.validate_outputs(ctx.output_dir, own)
    if own is None or contracts_store.DOMAIN_DESIGN_FILE in own:
        problems += contracts_store.domain_design_ownership_problems(ctx.output_dir, ctx.domain, ctx.ownership)
    problems += discovery_store.validate_outputs(ctx.output_dir)
    problems += guardrail_problems(ctx, checks)
    return problems


def finish_check(ctx: AgentContext, written: set[str], previous: list[str] | None) -> list[str]:
    """verify(), for an agent that says it's done: the problems to send it back with (empty: it may finish). Raises
    AgentFailed where sending it back can't help: a problem it can't fix (a Blocker), or the same problems as after
    its last attempt."""
    problems = verify(ctx, written)
    stuck = blockers(problems)
    if stuck:
        raise AgentFailed("This step can't finish until something outside the agent's work changes: "
                          + "; ".join(stuck[:5]))
    if problems and problems == previous:
        raise AgentFailed("The same problems remain after the agent tried to fix them: " + "; ".join(problems[:5]))
    return problems


def preflight_or_fail(ctx: AgentContext) -> None:
    """Fail the step before the agent starts when it can't finish anyway (see preflight)."""
    stuck = preflight(ctx)
    if stuck:
        ctx.log("Not started: " + "; ".join(stuck[:5]))
        raise AgentFailed("This step can't run as it is set up: " + "; ".join(stuck[:5]))


def verify_message(problems: list[str]) -> str:
    return ("Before you finish, fix these:\n" + "\n".join(f"- {p}" for p in problems) +
            "\nThen finish with a short summary.")


def artifacts(ctx: AgentContext, toolbox: Toolbox) -> list[ArtifactOut]:
    """This stage's documents: everything it wrote in the operation folder, plus the required outputs."""
    out_dir = ctx.rel(ctx.output_dir)
    paths = {p for p in toolbox.written if p.startswith(out_dir + "/")}
    paths |= {f"{out_dir}/{o}" for o in ctx.config["outputs"]}
    return [ArtifactOut(name=Path(p).name, path=p, kind=kind(p))
            for p in sorted(paths) if (ctx.workspace / p).is_file()]
