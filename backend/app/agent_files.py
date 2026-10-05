"""Agents as GitHub Copilot custom agent text (`*.agent.md`): YAML frontmatter followed by the instructions.

The same text is the agent.md editor on the Agents page, the import and the download, so it holds every setting:
- `name` (the title), `description`, `model` and `tools`, as in Copilot. `tools` accepts Copilot aliases (read,
  search, edit, execute, …) as well as this app's tool names, which it uses when rendering so nothing is lost.
- Settings Copilot has no field for go under Copilot's `metadata` (string pairs, ignored by Copilot):
  mode, effort, jdk, max-turns, required-documents, checks (comma-separated lists; "none" for an empty one).
Fields the text leaves out keep the agent's current values.
"""

import re

import yaml

from app.agents.defaults import CHECKS, EFFORTS, TOOLS

# Copilot tool aliases (and their compatible names) → this app's sandboxed tools.
TOOL_ALIASES: dict[str, list[str]] = {
    "read": ["read_file", "list_files"],
    "search": ["search_code", "list_files"],
    "edit": ["write_file", "edit_file"],
    "execute": ["run_command"],
}
_COMPATIBLE = {
    "read": "read", "notebookread": "read",
    "edit": "edit", "multiedit": "edit", "write": "edit", "notebookedit": "edit",
    "search": "search", "grep": "search", "glob": "search",
    "execute": "execute", "shell": "execute", "bash": "execute", "powershell": "execute",
}
_UNSUPPORTED = {"web", "websearch", "webfetch", "agent", "custom-agent", "task", "todo", "todowrite"}
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_METADATA_KEYS = ("mode", "effort", "jdk", "max-turns", "required-documents", "checks")
MODES = ("stub", "llm")
JDKS = ("legacy", "modern")


class InvalidAgentFile(ValueError):
    pass


def looks_like_agent_file(text: str) -> bool:
    return bool(_FRONTMATTER.match(text.lstrip("﻿")))


def _title(name: str) -> str:
    """Copilot-style ids ('test-specialist') become titles ('Test specialist'); anything else is kept as written."""
    name = name.strip()
    if " " in name or not re.search(r"[-_]", name):
        return name
    return " ".join(re.split(r"[-_]+", name)).capitalize()


def _list(value) -> list[str]:
    if value is None or str(value).strip().lower() == "none":
        return []
    items = value.split(",") if isinstance(value, str) else value
    return [str(v).strip() for v in items if str(v).strip()]


def _tools(raw, warnings: list[str]) -> list[str]:
    names = _list(raw)
    own = [n for n in names if n in TOOLS]
    tools: list[str] = [] if own else ["ask_user"]  # a plain Copilot file: agents can always ask a person
    skipped = []
    for name in names:
        alias = _COMPATIBLE.get(name.lower())
        if name in TOOLS:
            tools.append(name)
        elif name == "*":
            tools += [t for group in TOOL_ALIASES.values() for t in group]
        elif alias:
            tools += TOOL_ALIASES[alias]
        else:
            skipped.append(name)
    if not own and "edit_file" in tools:
        tools.append("check_boundaries")
    if skipped:
        kinds = "web, sub-agent and to-do tools" if {n.lower() for n in skipped} <= _UNSUPPORTED else "tools"
        warnings.append(f"Not available to Migration Studio agents ({kinds}): {', '.join(skipped)}.")
    return list(dict.fromkeys(tools))


def _metadata(meta: dict, out: dict, warnings: list[str]) -> None:
    raw = meta.get("metadata")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise InvalidAgentFile("metadata must be key/value pairs")
    for key in raw:
        if key not in _METADATA_KEYS:
            warnings.append(f"metadata '{key}' is not a Migration Studio setting; it was ignored.")

    def value(key: str, allowed: tuple[str, ...]) -> str | None:
        if key not in raw:
            return None
        v = str(raw[key]).strip()
        if v not in allowed:
            raise InvalidAgentFile(f"metadata {key}: {v!r} must be one of {', '.join(allowed)}")
        return v

    if (v := value("mode", MODES)) is not None:
        out["mode"] = v
    if (v := value("effort", EFFORTS)) is not None:
        out["effort"] = v
    if (v := value("jdk", JDKS)) is not None:
        out["jdk"] = v
    if "max-turns" in raw:
        try:
            turns = int(str(raw["max-turns"]).strip())
        except ValueError:
            raise InvalidAgentFile(f"metadata max-turns: {raw['max-turns']!r} must be a whole number")
        if not 1 <= turns <= 500:
            raise InvalidAgentFile("metadata max-turns must be between 1 and 500")
        out["max_turns"] = turns
    if "required-documents" in raw:
        out["outputs"] = _list(raw["required-documents"])
    if "checks" in raw:
        checks = _list(raw["checks"])
        unknown = [c for c in checks if c not in CHECKS]
        if unknown:
            raise InvalidAgentFile(f"metadata checks: {', '.join(unknown)} must be one of {', '.join(CHECKS)}")
        out["checks"] = checks


def parse(text: str) -> dict:
    """Agent fields from agent.md text. Only fields the text sets are returned, plus `warnings` for anything that
    can't be carried over. Raises InvalidAgentFile with a readable reason."""
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if not match:
        raise InvalidAgentFile("agent.md text starts with YAML frontmatter between --- lines")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark else ""  # +1 for 0-based, +1 for the opening ---
        raise InvalidAgentFile(f"The frontmatter is not valid YAML{where}: {getattr(e, 'problem', e)}") from e
    if not isinstance(meta, dict):
        raise InvalidAgentFile("The frontmatter must be YAML key/value pairs")
    instructions = text.lstrip("﻿")[match.end():].strip()
    if not instructions:
        raise InvalidAgentFile("There are no instructions below the frontmatter")

    warnings: list[str] = []
    out: dict = {"instructions": instructions}
    if meta.get("name"):
        out["title"] = _title(str(meta["name"]))
    if meta.get("description"):
        out["description"] = str(meta["description"]).strip()
    if meta.get("model"):
        out["model"] = str(meta["model"]).strip()
        if out["model"] != "auto":
            warnings.append(f"Model {out['model']!r}: check your Copilot account offers it, or use auto.")
    if "tools" in meta:
        out["tools"] = _tools(meta["tools"], warnings)
    _metadata(meta, out, warnings)
    for key in ("mcp-servers", "target", "disable-model-invocation", "user-invocable", "infer"):
        if key in meta:
            warnings.append(f"'{key}' is a Copilot setting with no equivalent here; it was ignored.")
    out["warnings"] = warnings
    return out


def render(agent: dict) -> str:
    """The agent as agent.md text, with every setting, so parse(render(agent)) gives the agent back."""
    meta = {
        "name": agent["title"],
        "description": agent.get("description") or agent["title"],
        "model": agent.get("model") or "auto",
        "tools": [t for t in TOOLS if t in (agent.get("tools") or [])],  # this app's names, in a stable order
        "metadata": {
            "mode": agent["mode"],
            "effort": agent["effort"],
            "jdk": agent["jdk"],
            "max-turns": agent["max_turns"],
            "required-documents": ", ".join(agent.get("outputs") or []) or "none",
            "checks": ", ".join(agent.get("checks") or []) or "none",
        },
    }
    frontmatter = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{frontmatter}\n---\n\n{agent['instructions'].strip()}\n"
