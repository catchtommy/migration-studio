"""Guardrails written in plain words: a global text (Agents page) and each project's own (Project settings), which
either adds to the global one or replaces it, for every agent whose step changes code; and each agent's own (its page
on the Agents page), for that agent whatever its step. The agent gets them as hard rules in its system prompt, and
again with each result of its guardrail check tools, so it re-reads them while it works.

Unlike the built-in checks (module_guardrails.py and the others, listed by `builtin()`), these are not checked
against git: the agent is told to follow them and to stop and ask when one can't be kept.
"""

import importlib

# The built-in checks, in the order of the workflow: (key, title, module whose docstring describes it, the agent checks
# it serves). An agent's page shows those of its checks; "agent_limits" applies to every agent that runs commands.
BUILTIN = [
    ("module_guardrails", "Module in monolith", "app.module_guardrails", ("module_guardrails",)),
    ("orchestrator_guardrails", "Orchestrator in monolith", "app.orchestrator_guardrails", ("orchestrator_guardrails",)),
    ("service_guardrails", "Microservice", "app.service_guardrails", ("service_guardrails",)),
    ("orchestrator_service_guardrails", "Orchestrator microservice", "app.orchestrator_service_guardrails",
     ("orchestrator_service_guardrails",)),
    ("test_guardrails", "Independent tests and Service tests", "app.test_guardrails",
     ("acceptance_test_guardrails", "acceptance_suite_unchanged", "service_test_guardrails")),
    ("release_guardrails", "Modernisation and Monolith integration", "app.release_guardrails",
     ("modernise_guardrails", "integration_guardrails")),
    ("boundary", "Domain boundaries", "app.boundary_check", ("boundary",)),
    ("agent_limits", "Every code-changing agent", "app.custom_guardrails._AGENT_LIMITS", ()),
]
_AGENT_LIMITS = """What every agent that changes code is held to, whatever its step.

- Git: no push, remote, config, tag, rebase, amend, switch, branch create/delete/move, history rewriting (filter-branch,
  reflog, gc, prune), worktrees, submodules or credentials, and never `git add -f`. The run pushes, through the review.
- No `sudo`.
- A problem the agent can't fix (settings, an earlier step, git, a person's decision) stops the step with what to
  change, before the agent starts when it is known then; the agent is never sent back to fix it.
- The same check result three times in a row: the agent is told to stop checking, ask the user or finish.
- The step can't finish while a required check fails.
"""


def effective(settings: dict) -> str:
    """The guardrails in effect for a project: the global text, the project's added to it or in its place."""
    own = (settings.get("project_guardrails") or "").strip()
    shared = (settings.get("custom_guardrails") or "").strip()
    if settings.get("project_guardrails_mode") == "replace":
        return own
    return "\n\n".join(t for t in (shared, own) if t)


def applies(ctx) -> bool:
    return ctx.stage_def is not None and bool(ctx.stage_def.code)


def _parts(ctx) -> list[tuple[str, str]]:
    """(heading, text) of the guardrails this agent gets: the project's (global and project, for steps that change
    code) and the agent's own (always)."""
    parts = []
    shared = effective(ctx.settings) if applies(ctx) else ""
    if shared:
        parts.append(("Project", shared))
    own = ((ctx.config or {}).get("guardrails") or "").strip()
    if own:
        parts.append(("This agent", own))
    return parts


def prompt_section(ctx) -> str:
    parts = _parts(ctx)
    if not parts:
        return ""
    return ("## Guardrails\n"
            "These are hard rules, like the ones in your instructions; where they conflict, these win. No tool checks "
            "them for you, so check your own changes against them before each commit and before you finish. If you "
            "can't keep one, stop and ask with ask_user instead of working around it.\n\n" +
            "\n\n".join(f"### {heading}\n{text}" for heading, text in parts))


def reminder(ctx) -> str:
    parts = _parts(ctx)
    if not parts:
        return ""
    return ("\n\nAlso check your changes against these guardrails (not checked by this tool):\n" +
            "\n\n".join(text for _heading, text in parts))


def builtin() -> list[dict]:
    """The built-in checks with what each enforces, from the docstrings of their modules."""
    out = []
    for key, title, where, checks in BUILTIN:
        if where.endswith("._AGENT_LIMITS"):
            text = _AGENT_LIMITS
        else:
            text = importlib.import_module(where).__doc__ or ""
        out.append({"key": key, "title": title, "description": text.strip(), "checks": list(checks)})
    return out
