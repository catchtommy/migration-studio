"""Runtime settings editable from the UI, persisted in the `settings` table: global ones (AI provider, network) and
each project's own (its monolith, stack, toolchains, conventions)."""

import re
from typing import Any

from sqlalchemy import select

from app import projects
from app.db.models import GLOBAL, Setting
from app.db.session import session_scope

GLOBAL_DEFAULTS: dict[str, Any] = {
    "ai_provider": "copilot",  # copilot | claude: who runs agents in AI mode
    "github_host": "https://github.com",  # or a GitHub Enterprise Cloud (data residency) host, https://<name>.ghe.com
    # Corporate network (applied to the Copilot runtime; the proxy also to this backend's own downloads)
    "https_proxy": "",  # e.g. http://proxy.corp.example:8080
    "no_proxy": "",
    "extra_ca_file": "",  # PEM file with the company's root CA(s), if not already trusted by the OS
    "copilot_cli_path": "",  # an IT-installed `copilot` binary instead of the SDK's download
    "copilot_disable_websockets": False,  # for proxies that block WebSockets
}
PROJECT_DEFAULTS: dict[str, Any] = {
    "monolith_repo": "",  # git URL or local path; empty = blank workspace
    "monolith_branch": "",  # the branch (or tag) runs start from; empty = the repository's default branch
    "max_attempts": 3,  # self-verification retries per stage
    # What the project migrates from and to; given to every agent, so AI agents work in the project's language.
    "language": "java",  # see LANGUAGES
    "source_stack": "Java 8, Spring MVC 3 and iBatis on Oracle, with JSP views",
    "target_stack": "Java 25 and Spring Boot microservices",
    # Toolchains run_command puts first on PATH; each agent picks legacy (the monolith's) or target on the Agents page.
    "jdk_legacy_home": "",  # e.g. a JDK 8, .NET Framework SDK or Node 14 install
    "jdk_modern_home": "",  # e.g. a JDK 25, .NET 9 SDK or Node 22 install
    "oracle_test_url": "",  # read-only connection to the monolith's database, for schema metadata and tests
    # Cross-domain integration conventions given to agents (no secrets)
    "event_bus_namespace": "",  # Azure Service Bus namespace, e.g. mycompany-migration.servicebus.windows.net
    "event_topic_pattern": "{domain}-events",  # one topic per publishing domain
    "command_queue_pattern": "{domain}-commands",  # saga commands
    "service_dir_pattern": "services/{domain}-service",  # where each extracted service lives in the workspace
}
DEFAULTS = {**GLOBAL_DEFAULTS, **PROJECT_DEFAULTS}
NETWORK_KEYS = {"https_proxy", "no_proxy", "extra_ca_file", "copilot_cli_path", "copilot_disable_websockets"}
BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$")  # a plain git ref name (never an option)
LANGUAGES = {"java": "Java", "csharp": "C# / .NET", "typescript": "TypeScript / Node.js", "python": "Python",
             "go": "Go", "other": "Other"}


def _stored(s, project: str, keys) -> dict[str, Any]:
    return {row.key: row.value for row in s.scalars(select(Setting).where(Setting.project == project))
            if row.key in keys}


def get_global() -> dict[str, Any]:
    with session_scope() as s:
        return {**GLOBAL_DEFAULTS, **_stored(s, GLOBAL, GLOBAL_DEFAULTS)}


def get_project(project: str) -> dict[str, Any]:
    with session_scope() as s:
        return {**PROJECT_DEFAULTS, **_stored(s, project, PROJECT_DEFAULTS)}


def get_all(project: str | None = None) -> dict[str, Any]:
    """Global settings plus those of the project (the current one unless given)."""
    project = project or projects.current_or_none()
    return {**get_global(), **(get_project(project) if project else PROJECT_DEFAULTS)}


def get(key: str, project: str | None = None) -> Any:
    if key in GLOBAL_DEFAULTS:
        return get_global()[key]
    return get_all(project)[key]


def _check(values: dict[str, Any], allowed: dict[str, Any], kind: str) -> None:
    unknown = set(values) - set(allowed)
    if unknown:
        raise KeyError(f"Unknown {kind} settings: {', '.join(sorted(unknown))}")
    if "language" in values and values["language"] not in LANGUAGES:
        raise KeyError(f"language must be one of {', '.join(LANGUAGES)}")
    branch = values.get("monolith_branch")
    if branch is not None and branch != "" and not (isinstance(branch, str) and BRANCH.match(branch)
                                                    and ".." not in branch and not branch.endswith((".lock", "/"))):
        raise KeyError("The base branch must be a git branch or tag name, e.g. main or release/2.4")


def update_global(values: dict[str, Any]) -> dict[str, Any]:
    _check(values, GLOBAL_DEFAULTS, "global")
    with session_scope() as s:
        for key, value in values.items():
            s.merge(Setting(project=GLOBAL, key=key, value=value))
    return get_global()


def update_project(project: str, values: dict[str, Any]) -> dict[str, Any]:
    _check(values, PROJECT_DEFAULTS, "project")
    with session_scope() as s:
        for key, value in values.items():
            s.merge(Setting(project=project, key=key, value=value))
    return get_project(project)
