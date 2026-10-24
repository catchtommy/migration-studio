"""Tools the Claude stage agents use on the run workspace. Paths are relative to the workspace and cannot leave it."""

import os
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from app import (boundary_check, module_guardrails, orchestrator_guardrails, orchestrator_service_guardrails,
                 release_guardrails, service_guardrails, test_guardrails)
from app.text import decode, decode_with, encode_like
from app.agents.base_agent import DOCS_ROOT, AgentContext
from app.guardrail_problems import blockers

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
        "description": "Run a shell command from where this step changes code (the workspace root unless it works in another repository), e.g. a build or test run (Maven, Gradle, dotnet, "
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
    "check_module_guardrails": {
        "description": "Check everything you changed since this step started against the step's limits: no UI, DDL, "
                       "version or build changes, only the allowed call-site and wiring edits outside the new "
                       "modules, SQL text identical to the monolith's, only allowed dependencies, no God module, no "
                       "other domain's tables or packages, no existing test weakened. Run it after every step and "
                       "before finishing. Needs module-extraction.yaml.",
        "input_schema": _obj({}, []),
    },
    "check_orchestrator_guardrails": {
        "description": "Check everything you changed since this step started against the step's limits: no database "
                       "access (DAO, iBatis, JDBC, SQL maps) and no transactions in the orchestrator module, the "
                       "domain module unchanged, no UI, DDL, version or build changes, only the allowed call-site and "
                       "wiring edits outside the orchestrator module, no other domain's code or tables, no God module. "
                       "Run it after every step and before finishing. Needs orchestrator-extraction.yaml.",
        "input_schema": _obj({}, []),
    },
    "check_orchestrator_service_guardrails": {
        "description": "Check everything you changed in the orchestrator service's repository since this step started "
                       "against the step's limits: no database of any kind (data access, transactions, SQL, data "
                       "libraries, datasource settings), the monolith untouched, the orchestrator module's code "
                       "copied unchanged apart from package and import lines unless listed under replaced, Java 25 "
                       "on Spring Boot, no secrets, no other domain's code, and exactly the declared operations. Run "
                       "it after every step and before finishing. Needs orchestrator-service-extraction.yaml.",
        "input_schema": _obj({}, []),
    },
    "check_test_guardrails": {
        "description": "Check everything you changed in the service's repository since this step started against the "
                       "test step's limits: the Independent tests step changes only the acceptance suite in "
                       "acceptance-tests/ (black-box: no import of the service's code, a build of its own); the "
                       "Modernisation step leaves acceptance-tests/ unchanged; the Service tests step changes only "
                       "test sources (src/test/) and adds only test-scoped dependencies. The monolith stays untouched "
                       "and no test is disabled. Run it before finishing.",
        "input_schema": _obj({}, []),
    },
    "check_release_guardrails": {
        "description": "Check everything you changed since this step started against the release step's limits. "
                       "Modernisation: the schema unchanged (no DDL, schema scripts, Flyway or Liquibase; Hibernate's "
                       "ddl-auto none or validate), Java 25 on Spring Boot 4, no secrets, no test disabled, the "
                       "monolith untouched. Monolith integration: no UI, DDL or SQL change, only an HTTP client and "
                       "the Azure Service Bus SDK added to the build, the domain and orchestrator modules unchanged, "
                       "no secrets, no test deleted or disabled. Run it before finishing.",
        "input_schema": _obj({}, []),
    },
    "check_service_guardrails": {
        "description": "Check everything you changed in the service repository since this step started against the "
                       "step's limits: the monolith untouched, the copied code equal to the module's apart from "
                       "package and import lines, SQL text identical, only the module's libraries plus the allowed "
                       "infrastructure ones, Java 8, no secrets or DDL, no God module, exactly the approved "
                       "operations, only designed events, an adapter for every context port. Run it after every step "
                       "and before finishing. Needs microservice-extraction.yaml.",
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
# What commands an agent runs inherit from the server's environment: what builds need, never its secrets (API keys,
# database passwords, tokens). Anything not listed here is left out.
COMMAND_ENV = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TEMP", "TMP", "TZ",
               "JAVA_HOME", "MAVEN_HOME", "M2_HOME", "MAVEN_OPTS", "GRADLE_HOME", "GRADLE_USER_HOME", "DOTNET_ROOT",
               "NUGET_PACKAGES", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
               "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE",
               "PROGRAMFILES", "PROGRAMDATA"}
# Git commands that reach beyond the step's own commits: publishing, remotes, configuration, rewriting refs or
# history. The run commits and pushes through the review, never from an agent.
# Steps that change the legacy monolith's code: its modules are large, so their full test suites are never run;
# only the tests of the classes the step changes, and the whole suite of a module the step created.
FOCUSED_TEST_STAGES = {"modular_monolith", "monolith_orchestrator"}
_MAVEN = re.compile(r"(?:^|[\s;&|/\\])(?:mvn|mvnw)(?:\.cmd|\.bat)?(?=\s|$)")
_GRADLE = re.compile(r"(?:^|[\s;&|/\\])(?:gradle|gradlew)(?:\.bat)?(?=\s|$)")
_MAVEN_TEST_GOALS = re.compile(r"(?:^|\s)(?:[\w.-]+:)?(test|verify|install|package|integration-test|deploy)(?=\s|$)")
_GRADLE_TEST_TASKS = re.compile(r"(?:^|\s)(?::?[\w-]+:)*(test|check|build)(?=\s|$)")
_SKIPS_TESTS = re.compile(r"-DskipTests(?:=true)?(?=\s|$)|-Dmaven\.test\.skip(?:=true)?(?=\s|$)|-x\s+(?::?[\w-]+:)*test(?=\s|$)")
_NAMES_TESTS = re.compile(r"-D(?:test|it\.test)=\S+|--tests\s+\S+")
_PROJECTS = re.compile(r"(?:-pl|--projects)[\s=]+(\S+)")
FOCUSED_TESTS_HINT = (
    "Run only the tests of the classes you changed, by name: Maven `mvn -q -o -pl <module> test "
    "-Dtest=LoanServiceTest,LoanServiceRouterTest -Dsurefire.failIfNoSpecifiedTests=false` (add `-am` if it needs "
    "the modules it depends on built), Gradle `gradle :<module>:test --tests '*LoanService*'`. A module this step "
    "created may run all its tests (`-pl <new module>`). To only compile: add `-DskipTests` (Gradle: `-x test`).")


FORBIDDEN_GIT = re.compile(
    r"\bgit\b(?:\s+(?:-[cC]\s+\S+|--\S+))*\s+(?:push|remote|config|filter-branch|filter-repo|update-ref|symbolic-ref|"
    r"replace|reflog|gc|prune|worktree|submodule|credential|send-email|branch\s+(?:-[dDmMcC]|--(?:delete|move|copy))|"
    r"checkout\s+(?:-[bB]|--orphan)|switch\b|rebase|commit\s+(?:.*\s)?--amend|tag\b|"
    r"add\s+(?:.*\s)?(?:-f\b|--force\b|-\w*f\w*\b))")


# The guardrail checks that compare with git from the commit the step started from (and the monolith's, for the
# steps that change another repository).
GIT_CHECK_TOOLS = {"check_module_guardrails", "check_orchestrator_guardrails", "check_service_guardrails",
                   "check_orchestrator_service_guardrails", "check_test_guardrails", "check_release_guardrails"}
GIT_CHECKS = {"module_guardrails", "orchestrator_guardrails", "service_guardrails", "orchestrator_service_guardrails",
              "acceptance_test_guardrails", "acceptance_suite_unchanged", "service_test_guardrails",
              "modernise_guardrails", "integration_guardrails"}
MONOLITH_CHECKS = {"service_guardrails", "orchestrator_service_guardrails", "acceptance_test_guardrails",
                   "service_test_guardrails", "modernise_guardrails"}


# Also kept: certificates and JVM options for builds behind a corporate proxy, and Docker for Testcontainers.
COMMAND_ENV |= {"JAVA_TOOL_OPTIONS", "MAVEN_ARGS", "GRADLE_OPTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "DOCKER_HOST", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
                "DOCKER_CONFIG"}
COMMAND_ENV_PREFIXES = ("LC_", "TESTCONTAINERS_", "GIT_CONFIG_")  # GIT_CONFIG_: core.longpaths on Windows


def command_env() -> dict[str, str]:
    """The server's environment, reduced to what builds need (COMMAND_ENV and its prefixes)."""
    return {k: v for k, v in os.environ.items() if k in COMMAND_ENV or k.startswith(COMMAND_ENV_PREFIXES)}


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
        self._last_check, self._repeats = None, 0  # the last check result, and how often it came back unchanged
        # The shared read-only monolith, overlaid on the workspace: paths not in the workspace are read from it.
        self.legacy = ctx.legacy_dir.resolve() if ctx.legacy_dir else None
        self.toolchain_home = toolchain_home
        self.written: set[str] = set()  # workspace-relative paths written by this agent
        # A step not set to change code (Workflows page) writes its documents and nothing else.
        self.docs_only = ctx.stage_def is not None and not ctx.stage_def.code
        from app.agents.common import required_checks, tools_of
        self.tools = tools = tools_of(ctx)
        checks = required_checks(ctx)
        if ctx.base_sha is None and (GIT_CHECK_TOOLS & set(tools) or GIT_CHECKS & checks):
            ctx.base_sha = module_guardrails.head(self.root / ctx.code_dir)
        if ctx.monolith_sha is None and ctx.code_dir not in (".", "") and (
                {"check_service_guardrails", "check_orchestrator_service_guardrails", "check_test_guardrails",
                 "check_release_guardrails"} & set(tools) or MONOLITH_CHECKS & checks):
            ctx.monolith_sha = module_guardrails.head(self.root)  # the monolith checkout the service copies from

    def run(self, name: str, args: dict) -> str:
        if name not in self.tools:
            raise ToolError(f"Tool {name} is not enabled for this agent")
        return getattr(self, name)(**validate(name, args))

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if p != self.root and not p.is_relative_to(self.root):
            raise ToolError(f"{path} is outside the workspace")
        if ".git" in p.relative_to(self.root).parts:
            raise ToolError("The .git directory is off limits")
        return p

    def _read_path(self, path: str) -> Path:
        """Where to read `path`: the workspace, else the shared legacy copy (read-only)."""
        p = self._resolve(path)
        if p.exists() or self.legacy is None:
            return p
        q = (self.legacy / path).resolve()
        if q.is_relative_to(self.legacy) and ".git" not in q.relative_to(self.legacy).parts and q.exists():
            return q
        return p

    def _write_path(self, path: str) -> Path:
        p = self._resolve(path)
        rel = self._rel(p)
        if rel in self.ctx.locked_documents:
            raise ToolError(f"{path} is a document a gate has already approved; this step may read it but not "
                            "change it. Write your own documents, and raise what should change with ask_user")
        docs = p.is_relative_to(self.root / DOCS_ROOT)
        if not docs and not self.docs_only:
            code = (self.root / self.ctx.code_dir).resolve()
            nested = self.root / ".studio"
            if not p.is_relative_to(code) or (code == self.root and p.is_relative_to(nested)):
                where = "the monolith" if code == self.root else self.ctx.code_dir
                raise ToolError(f"{path} is outside the repository this step changes ({where}); the run's other "
                                "repositories are changed by their own steps")
        if self.docs_only and not docs:
            raise ToolError(f"{path} is code, and this step writes documents only: write under "
                            f"{DOCS_ROOT}/ (your documents folder). To let a step change code, set it to on the "
                            "Workflows page")
        if self.legacy is not None and not p.exists() and (self.legacy / path).exists():
            raise ToolError(f"{path} is the monolith's code, which is read-only in this run: write documents under "
                            ".migration/ and code only in the repository this step changes")
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def _repos(self) -> list[tuple[str, Path]]:
        """(prefix, checkout) of the workspace root and the run's other repositories inside it."""
        out = [("", self.root)]
        for r in self.ctx.repositories:
            path = self.root / r["path"]
            if (path / ".git").exists():
                out.append((r["path"] + "/", path))
        return out

    def list_files(self, pattern: str) -> str:
        files = []
        for prefix, repo in self._repos():
            proc = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=repo,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            files += [prefix + f for f in proc.stdout.splitlines()]
        if self.legacy is not None:
            proc = subprocess.run(["git", "ls-files", "--cached"], cwd=self.legacy, capture_output=True, text=True, encoding="utf-8", errors="replace")
            files += proc.stdout.splitlines()
        bare = pattern.removeprefix("**/")
        matched = sorted({f for f in files if fnmatch(f, pattern) or fnmatch(f, bare)})
        if not matched:
            return "No files match."
        more = f"\n… and {len(matched) - MAX_LIST} more; use a narrower pattern." if len(matched) > MAX_LIST else ""
        return "\n".join(matched[:MAX_LIST]) + more

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        p = self._read_path(path)
        if not p.is_file():
            raise ToolError(f"{path} is not a file")
        lines = decode(p.read_bytes()).splitlines()  # legacy code may not be UTF-8 (app/text.py)
        start = max(start_line, 1)
        end = min(end_line or len(lines), len(lines), start + MAX_READ_LINES - 1)
        body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
        header = f"{path}: lines {start}-{end} of {len(lines)}" if lines else f"{path}: empty file"
        return f"{header}\n{body}"

    def search_code(self, pattern: str, glob: str | None = None) -> str:
        args = ["grep", "-n", "-I", "-E", "--untracked", "-e", pattern]
        if glob:
            args += ["--", glob]
        lines = []
        sources = self._repos() + ([("", self.legacy)] if self.legacy is not None else [])
        for prefix, repo in sources:
            proc = subprocess.run(["git", *args], cwd=repo, capture_output=True)
            if proc.returncode not in (0, 1):
                raise ToolError(proc.stderr.decode("utf-8", errors="replace").strip())
            # each match line is decoded on its own: legacy files may not be UTF-8 (app/text.py)
            lines += [prefix + decode(line) for line in proc.stdout.splitlines()]
        if not lines:
            return "No matches."
        more = f"\n… and {len(lines) - MAX_SEARCH_LINES} more matches; narrow the search." \
            if len(lines) > MAX_SEARCH_LINES else ""
        return "\n".join(lines[:MAX_SEARCH_LINES]) + more

    def write_file(self, path: str, content: str) -> str:
        p = self._write_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # A file being rewritten keeps its encoding (legacy code may not be UTF-8); new files are UTF-8.
        encoding = decode_with(p.read_bytes())[1] if p.is_file() else "utf-8"
        p.write_bytes(encode_like(content, encoding))
        self.written.add(self._rel(p))
        self.ctx.log(f"wrote {self._rel(p)}")
        return f"Wrote {len(content.splitlines())} lines to {self._rel(p)}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        p = self._write_path(path)
        if not p.is_file():
            raise ToolError(f"{path} is not a file")
        text, encoding = decode_with(p.read_bytes())  # kept as it is: legacy code may not be UTF-8
        count = text.count(old_text)
        if count != 1:
            raise ToolError(f"old_text occurs {count} times in {path}; it must occur exactly once")
        p.write_bytes(encode_like(text.replace(old_text, new_text), encoding))
        self.written.add(self._rel(p))
        self.ctx.log(f"edited {self._rel(p)}")
        return f"Edited {self._rel(p)}"

    def run_command(self, command: str, timeout_seconds: int = 600) -> str:
        if self.docs_only:
            raise ToolError("This step writes documents only, so it can't run commands (they could change code). "
                            "Read the code with list_files, read_file and search_code")
        hit = FORBIDDEN_GIT.search(command) or re.search(r"(?:^|[;&|]\s*)sudo\b", command)
        if hit:
            raise ToolError(f"`{hit.group(0).strip()}` is not allowed: an agent commits locally only (git add, commit, "
                            "revert, restore, reset within this step). Pushing, remotes, configuration and rewriting "
                            "branches or history are done by the run, through the review; ignored files are never "
                            "forced in (the studio's documents stay in .migration/ and are never committed)")
        unfocused = self._unfocused_tests(command)
        if unfocused:
            raise ToolError(unfocused)
        env = toolchain_env(command_env(), self.toolchain_home, self.ctx.settings.get("language") or "java")
        if self.legacy is not None:
            env["STUDIO_LEGACY_DIR"] = str(self.legacy)  # the read-only monolith, for commands that read it
        self.ctx.log(f"$ {command}")
        try:
            proc = subprocess.run(command, shell=True, cwd=self._resolve(self.ctx.code_dir), env=env,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=max(1, min(timeout_seconds, 3600)))
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout_seconds}s")
        output = (proc.stdout + proc.stderr)[-MAX_COMMAND_OUTPUT:]
        return f"exit code {proc.returncode}\n{output}"

    def _unfocused_tests(self, command: str) -> str | None:
        """Why a build command would run a legacy module's whole test suite in a step that changes the monolith, or
        None. Allowed: naming the tests (-Dtest=, --tests), skipping them (-DskipTests, -x test), or running a module
        this step created (-pl pointing only at folders that didn't exist when the step started)."""
        if self.ctx.stage not in FOCUSED_TEST_STAGES:
            return None
        maven, gradle = _MAVEN.search(command), _GRADLE.search(command)
        if not (maven and _MAVEN_TEST_GOALS.search(command)) and not (gradle and _GRADLE_TEST_TASKS.search(command)):
            return None  # not a build that runs tests (compile, dependency:tree, ...)
        if _SKIPS_TESTS.search(command) or _NAMES_TESTS.search(command):
            return None
        projects = [p for m in _PROJECTS.finditer(command) for p in m.group(1).split(",") if p]
        if projects and all(self._new_folder(p) for p in projects):
            return None  # only modules this step created: their own (new, small) tests
        return ("Not run: this would run the whole test suite of the legacy code, which is large and slow. "
                + FOCUSED_TESTS_HINT)

    def _new_folder(self, project: str) -> bool:
        """Whether a -pl entry is a folder this step created (absent at the commit it started from)."""
        if project.startswith((":", "!", "-")) or not self.ctx.base_sha:
            return False  # by artifactId, or excluded: can't tell, so not counted as new
        path = project.strip("./").rstrip("/")
        root = self._resolve(self.ctx.code_dir)
        proc = subprocess.run(["git", "cat-file", "-e", f"{self.ctx.base_sha}:{path}"], cwd=root, capture_output=True)
        return proc.returncode != 0 and (root / path).is_dir()

    def _check(self, name: str, problems: list[str], report) -> str:
        """A check tool's result: its report, and what to do when the agent can't fix a problem or keeps getting the
        same result (so it doesn't loop on a check it can't pass)."""
        from app import custom_guardrails

        text = report(problems) + custom_guardrails.reminder(self.ctx)
        stuck = blockers(problems)
        if stuck:
            text += ("\n\nYou can't fix these: they need the workflow's settings, an earlier step, the environment or a "
                     "person, not your work:\n" + "\n".join(f"- {p}" for p in stuck) +
                     "\nDon't try to work around them. Finish with a summary that says what blocks the step.")
        key = (name, tuple(problems))
        self._repeats = self._repeats + 1 if problems and key == self._last_check else 0
        self._last_check = key
        if self._repeats >= 2:
            text += ("\n\nThis is the same result as the last two times. Stop calling the check until you have changed "
                     "something it reports. If you can't fix what is left, ask the user (ask_user), or finish with a "
                     "summary that explains what blocks you.")
        return text

    def _required(self) -> list[str]:
        from app.agents.common import required_checks
        return sorted(required_checks(self.ctx))

    def check_boundaries(self) -> str:
        ctx = self.ctx
        return self._check("boundaries", boundary_check.check(ctx.workspace, ctx.domain, ctx.service_dir, ctx.ownership),
                           boundary_check.report)

    def check_module_guardrails(self) -> str:
        return self._check("module", module_guardrails.for_context(self.ctx), module_guardrails.report)

    def check_orchestrator_guardrails(self) -> str:
        return self._check("orchestrator", orchestrator_guardrails.for_context(self.ctx), orchestrator_guardrails.report)

    def check_orchestrator_service_guardrails(self) -> str:
        return self._check("orchestrator_service", orchestrator_service_guardrails.for_context(self.ctx),
                           orchestrator_service_guardrails.report)

    def check_test_guardrails(self) -> str:
        return self._check("test", test_guardrails.for_context(self.ctx, self._required()), test_guardrails.report)

    def check_release_guardrails(self) -> str:
        return self._check("release", release_guardrails.for_context(self.ctx, self._required()),
                           release_guardrails.report)

    def check_service_guardrails(self) -> str:
        return self._check("service", service_guardrails.for_context(self.ctx), service_guardrails.report)

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        return self.ctx.ask(question, [str(o) for o in options] if options else None)
