"""Tools the Claude stage agents use on the run workspace. Paths are relative to the workspace and cannot leave it."""

import os
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from app import boundary_check
from app.agents.base_agent import AgentContext

MAX_LIST = 500
MAX_READ_LINES = 1500
MAX_SEARCH_LINES = 300
MAX_COMMAND_OUTPUT = 20_000


class ToolError(Exception):
    """Reported back to the model as an error result; the agent can correct itself."""


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}

SCHEMAS: dict[str, dict] = {
    "list_files": {
        "description": "List files in the workspace (tracked and untracked, honouring .gitignore) whose path matches "
                       "a glob such as `**/*.java` or `src/main/**/Customer*`. Returns at most 500 paths.",
        "input_schema": _obj({"pattern": _STR}, ["pattern"]),
    },
    "read_file": {
        "description": f"Read a text file with line numbers. Reads at most {MAX_READ_LINES} lines per call; use "
                       "start_line/end_line to page through longer files.",
        "input_schema": _obj({"path": _STR, "start_line": _INT, "end_line": _INT}, ["path"]),
    },
    "search_code": {
        "description": "Search file contents with an extended regular expression (git grep -E). Optionally limit to "
                       f"files matching a glob. Returns matching lines as path:line:text, at most {MAX_SEARCH_LINES}.",
        "input_schema": _obj({"pattern": _STR, "glob": _STR}, ["pattern"]),
    },
    "write_file": {
        "description": "Create or overwrite a file with the given content. Parent directories are created.",
        "input_schema": _obj({"path": _STR, "content": _STR}, ["path", "content"]),
    },
    "edit_file": {
        "description": "Replace one exact, unique occurrence of old_text with new_text in an existing file.",
        "input_schema": _obj({"path": _STR, "old_text": _STR, "new_text": _STR}, ["path", "old_text", "new_text"]),
    },
    "run_command": {
        "description": "Run a shell command from the workspace root, e.g. a build or test run (Maven, Gradle, dotnet, "
                       "npm, pytest, go test, ...). The toolchain configured for this stage comes first on PATH "
                       "(and JAVA_HOME / DOTNET_ROOT are set for Java / .NET projects). Returns the exit code and "
                       "the last 20,000 characters of combined output.",
        "input_schema": _obj({"command": _STR, "timeout_seconds": _INT}, ["command"]),
    },
    "check_boundaries": {
        "description": "Check that this domain's microservice only uses its own domain: reports every reference to "
                       "another domain's code packages (namespaces, modules) or tables, with file and line. Run it "
                       "before finishing.",
        "input_schema": _obj({}, []),
    },
    "ask_user": {
        "description": "Ask the person supervising this migration a question and wait for their answer. Use it "
                       "when you cannot find something, the code is ambiguous, or a decision belongs to a person. "
                       "Ask one specific question at a time and include what you already found. Offer short "
                       "suggested answers in `options` when the choice is between a few alternatives.",
        "input_schema": _obj({"question": _STR, "options": {"type": "array", "items": _STR}}, ["question"]),
    },
}

_TYPES = {"string": str, "integer": int, "array": list}


def definitions(names: list[str]) -> list[dict]:
    """Tool definitions for the API, in a fixed order so the cached prompt prefix stays stable."""
    return [{"name": n, **SCHEMAS[n], "eager_input_streaming": True} for n in SCHEMAS if n in names]


def validate(name: str, args: object) -> dict:
    """Streamed tool inputs are not validated by the API, so check them before running anything."""
    schema = SCHEMAS[name]["input_schema"]
    if not isinstance(args, dict):
        raise ToolError("Tool input must be a JSON object")
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        raise ToolError(f"Missing required input(s): {', '.join(missing)}")
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            raise ToolError(f"Unknown input: {key}")
        if not isinstance(value, _TYPES[spec["type"]]) or (spec["type"] == "integer" and isinstance(value, bool)):
            raise ToolError(f"Input {key} must be of type {spec['type']}")
    return args


# Environment variables a language's tools look for, pointed at the configured toolchain.
TOOLCHAIN_VARS = {"java": ["JAVA_HOME"], "csharp": ["DOTNET_ROOT"]}


def toolchain_env(env: dict[str, str], home: str | None, language: str) -> dict[str, str]:
    """The environment for run_command: the toolchain's bin directory (and the home itself, for toolchains that keep
    their executables at the top, like .NET or a Go root) first on PATH."""
    if home:
        for var in TOOLCHAIN_VARS.get(language, []):
            env[var] = home
        env["PATH"] = os.pathsep.join([f"{home}/bin", home, env.get("PATH", "")])
    return env


class Toolbox:
    def __init__(self, ctx: AgentContext, toolchain_home: str | None):
        self.ctx = ctx
        self.root = ctx.workspace.resolve()
        self.toolchain_home = toolchain_home
        self.written: set[str] = set()  # workspace-relative paths written by this agent

    def run(self, name: str, args: dict) -> str:
        if name not in self.ctx.config.get("tools", []):
            raise ToolError(f"Tool {name} is not enabled for this agent")
        return getattr(self, name)(**validate(name, args))

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if p != self.root and not p.is_relative_to(self.root):
            raise ToolError(f"{path} is outside the workspace")
        if ".git" in p.relative_to(self.root).parts:
            raise ToolError("The .git directory is off limits")
        return p

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)

    def list_files(self, pattern: str) -> str:
        files = self._git("ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
        bare = pattern.removeprefix("**/")
        matched = sorted({f for f in files if fnmatch(f, pattern) or fnmatch(f, bare)})
        if not matched:
            return "No files match."
        more = f"\n… and {len(matched) - MAX_LIST} more; use a narrower pattern." if len(matched) > MAX_LIST else ""
        return "\n".join(matched[:MAX_LIST]) + more

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"{path} is not a file")
        lines = p.read_text(errors="replace").splitlines()
        start = max(start_line, 1)
        end = min(end_line or len(lines), len(lines), start + MAX_READ_LINES - 1)
        body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
        header = f"{path}: lines {start}-{end} of {len(lines)}" if lines else f"{path}: empty file"
        return f"{header}\n{body}"

    def search_code(self, pattern: str, glob: str | None = None) -> str:
        args = ["grep", "-n", "-I", "-E", "--untracked", "-e", pattern]
        if glob:
            args += ["--", glob]
        proc = self._git(*args)
        if proc.returncode == 1:
            return "No matches."
        if proc.returncode != 0:
            raise ToolError(proc.stderr.strip())
        lines = proc.stdout.splitlines()
        more = f"\n… and {len(lines) - MAX_SEARCH_LINES} more matches; narrow the search." \
            if len(lines) > MAX_SEARCH_LINES else ""
        return "\n".join(lines[:MAX_SEARCH_LINES]) + more

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self.written.add(self._rel(p))
        self.ctx.log(f"wrote {self._rel(p)}")
        return f"Wrote {len(content.splitlines())} lines to {self._rel(p)}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"{path} is not a file")
        text = p.read_text()
        count = text.count(old_text)
        if count != 1:
            raise ToolError(f"old_text occurs {count} times in {path}; it must occur exactly once")
        p.write_text(text.replace(old_text, new_text))
        self.written.add(self._rel(p))
        self.ctx.log(f"edited {self._rel(p)}")
        return f"Edited {self._rel(p)}"

    def run_command(self, command: str, timeout_seconds: int = 600) -> str:
        env = toolchain_env(dict(os.environ), self.toolchain_home, self.ctx.settings.get("language") or "java")
        self.ctx.log(f"$ {command}")
        try:
            proc = subprocess.run(command, shell=True, cwd=self.root, env=env, capture_output=True, text=True,
                                  timeout=max(1, min(timeout_seconds, 3600)))
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout_seconds}s")
        output = (proc.stdout + proc.stderr)[-MAX_COMMAND_OUTPUT:]
        return f"exit code {proc.returncode}\n{output}"

    def check_boundaries(self) -> str:
        ctx = self.ctx
        return boundary_check.report(boundary_check.check(ctx.workspace, ctx.domain, ctx.service_dir, ctx.ownership))

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        return self.ctx.ask(question, [str(o) for o in options] if options else None)
