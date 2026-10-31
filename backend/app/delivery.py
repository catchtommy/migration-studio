"""Getting a run's code changes into git.

Agent steps that change code each name a repository (the workflow step's code settings): the project's monolith, or
another repository such as a domain's microservice (its URL can contain {domain}) or the orchestrator. A run checks
out every repository its steps use on the same feature branch, each cut from its own base branch: the monolith is the
workspace root, the others are cloned inside it (`.studio/repos/<key>`). The studio's documents stay in the workspace
root's `.migration` folder, shared by every step.

When the workflow finishes, the run waits (awaiting_commit) for a person to review and commit each repository it
changed: every changed file against where the run started, what merging the latest base branch does to them, and any
conflicts to resolve. Committing builds one commit on top of the latest base branch (or of the feature branch pushed
before) with the chosen files, as the user and with their git credentials, and pushes the feature branch; merging it
into the base branch is up to the team's pull request process.
"""

import re
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app import git_auth, repos, settings_store, workspace
from app.agents.base_agent import DOCS_ROOT
from app.db.models import Domain, Run, StageExecution, User
from app.db.session import emit, session_scope
from app.text import decode
from app.tools import git_ops

# Every migration runs on its own branch, cut from the base branch and merged into it by hand when done: features never
# share a branch, so one never affects another's run (app/domain_modules.py).
DEFAULT_PATTERN = "migration/{domain}/{feature}"
PLACEHOLDERS = ("domain", "feature", "run", "date")
PER_FEATURE = ("feature", "run")  # a branch pattern names one of them, so each feature (or run) gets its own branch
MAX_TEXT = 1_000_000  # larger files are shown as "too large" and can only be taken whole
ROOT = "monolith"  # the key of the workspace root's repository
NESTED_DIR = ".studio/repos"  # where a run's other repositories are checked out, inside the workspace


_run_locks: dict[str, threading.RLock] = {}
_run_locks_guard = threading.Lock()


def run_lock(run_id: str) -> threading.RLock:
    """One run's git operations happen one at a time (reviewing, committing, bringing in the latest), whoever and
    however many people act on it: they share the run's checkouts."""
    with _run_locks_guard:
        return _run_locks.setdefault(run_id, threading.RLock())


class DeliveryError(RuntimeError):
    pass


# --- settings -------------------------------------------------------------------------------------------------------

def _placeholders(pattern: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", pattern))


def git_settings(workflow: dict | None) -> dict:
    """A workflow's git settings, with defaults: migrations review and commit their changes; discovery doesn't. Each
    feature has its own branch: a pattern saved before (one branch shared by the domain's features, naming neither
    {feature} nor {run}) gives way to the default."""
    stored = (workflow or {}).get("git") or {}
    migration = (workflow or {}).get("scope", "operation") == "operation"
    pattern = stored.get("branch_pattern", "") or ""
    if not _placeholders(pattern) & set(PER_FEATURE):
        pattern = DEFAULT_PATTERN
    return {"commit": bool(stored.get("commit", migration)) and migration, "branch_pattern": pattern}


def validate_git(values: dict) -> dict:
    """Clean a workflow's git settings, or ValueError."""
    out = {"commit": bool(values.get("commit", True)), "branch_pattern": str(values.get("branch_pattern") or "").strip()}
    if out["branch_pattern"]:
        unknown = _placeholders(out["branch_pattern"]) - set(PLACEHOLDERS)
        if unknown:
            raise ValueError(f"Unknown placeholder {{{sorted(unknown)[0]}}}; use {', '.join('{%s}' % p for p in PLACEHOLDERS)}")
        if not _placeholders(out["branch_pattern"]) & set(PER_FEATURE):
            raise ValueError("Each feature runs on its own branch: the pattern must name {feature} (or {run}), e.g. "
                             "migration/{domain}/{feature}")
        if not git_ops.valid_branch(branch_name(out["branch_pattern"], "domain", "feature", "run")):
            raise ValueError("The feature branch pattern doesn't make a valid branch name")
    return out


def branch_name(pattern: str, domain: str, feature: str, run_id: str = "") -> str:
    safe = lambda v: re.sub(r"[^A-Za-z0-9._-]+", "-", v).strip("-.") or "x"  # noqa: E731
    return (pattern or DEFAULT_PATTERN).format(domain=safe(domain), feature=safe(feature), run=safe(run_id[:8] or "run"),
                                               date=datetime.now(UTC).strftime("%Y%m%d"))


def _repo_key(url: str, taken: set[str]) -> str:
    name = re.sub(r"[^a-z0-9._-]+", "-", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
                  .removesuffix(".git")).strip("-.") or "repo"
    key, n = name, 2
    while key in taken:
        key, n = f"{name}-{n}", n + 1
    return key


def domain_inputs(project: str, domain: str) -> dict[str, dict]:
    """The domain's Agent inputs, by agent: where an agent whose step changes code works ("repository",
    "base_branch")."""
    with session_scope() as s:
        row = s.get(Domain, (project, domain))
        return dict(row.agent_inputs or {}) if row else {}


MONOLITH = "<monolith>"  # a step from before steps were a flag that named the monolith, when the project has none


def _same(a: str | None, b: str | None) -> bool:
    """Whether two repository URLs or paths name the same repository: a trailing slash or .git aside, and for a local
    Windows path (C:\\repos\\x, \\\\server\\share\\x) backslashes and letter case aside too."""
    def norm(u: str | None) -> str:
        u = (u or "").strip()
        local = bool(re.match(r"^[A-Za-z]:[\\/]|^\\\\|^//", u))  # a drive, or a UNC share (either slash)
        u = u.replace("\\", "/") if local else u
        u = u.rstrip("/").removesuffix(".git").rstrip("/")
        return u.casefold() if local else u
    return bool(a and b) and norm(a) == norm(b)


def _is_root(url: str | None, monolith: str | None) -> bool:
    """Whether a step's repository is the monolith: the workspace root."""
    return url == MONOLITH or _same(url, monolith)


def stage_repository(stage: dict, domain: str, inputs: dict, monolith: str | None) -> tuple[str | None, str]:
    """Where a step changes code, for a domain: (repository, base branch) from the domain's Agent inputs for the
    step's agent. A step from before steps were a flag (a run's snapshot) keeps what it named: "" the monolith, a URL
    or {domain} template. (None, "") when the step changes no code, or the domain hasn't set the repository."""
    code = stage.get("code") or {}
    if stage.get("kind") != "agent" or not code:
        return None, ""
    own = inputs.get(stage["key"]) or {}
    if own.get("repository"):
        return own["repository"], own.get("base_branch") or ""
    if "changes_code" in code:  # the domain must set it; a project with no monolith works in a blank workspace
        return (None, "") if monolith else (MONOLITH, "")
    named = code.get("repository") or ""
    if not named:
        return monolith or MONOLITH, ""
    if named.startswith("@"):  # a kind (earlier today): the domain's setting, gone
        return None, ""
    return named.replace("{domain}", domain), code.get("base_branch") or ""


def _monolith_repo(project: str) -> str | None:
    return settings_store.get_project(project).get("monolith_repo") or None


def code_repositories(stages: list[dict], domain: str, project: str | None = None,
                      monolith: str | None = None) -> list[dict]:
    """The repositories other than the monolith that a workflow's steps change code in, for a domain (each step's
    agent's repository in the domain's Agent inputs): [{"key", "url", "base_branch", "steps": [titles]}] in workflow
    order. A repository equal to the monolith is the workspace root, not listed."""
    from app import projects
    project = project or projects.current()
    monolith = monolith or _monolith_repo(project)
    inputs = domain_inputs(project, domain)
    found: dict[str, dict] = {}
    for st in stages:
        url, base = stage_repository(st, domain, inputs, monolith)
        if not url or _is_root(url, monolith):
            continue
        if url not in found:
            found[url] = {"key": _repo_key(url, {ROOT, *(r["key"] for r in found.values())}), "url": url,
                          "base_branch": base, "steps": []}
        found[url]["steps"].append(st["title"])
    return list(found.values())


def monolith_steps(stages: list[dict], domain: str, project: str, monolith: str | None = None) -> list[str]:
    """The steps whose repository is the monolith (the workspace root)."""
    monolith = monolith or _monolith_repo(project)
    inputs = domain_inputs(project, domain)
    out = []
    for st in stages:
        url = stage_repository(st, domain, inputs, monolith)[0]
        if _is_root(url, monolith):
            out.append(st["title"])
    return out


def missing_repositories(stages: list[dict], domain: str, project: str, when_needed: bool = False) -> str | None:
    """Why a run can't start: steps that change code whose agent has no repository in the domain's Agent inputs. A step
    skipped for domain-only features (the orchestrator) isn't asked for here: it stops the run itself, with this
    message, if a cross-domain feature reaches it without one (`when_needed`: count those steps too)."""
    from app.modulith_scope import SKIPPABLE
    inputs, monolith = domain_inputs(project, domain), _monolith_repo(project)
    titles = list(dict.fromkeys(st["title"] for st in stages if st.get("kind") == "agent" and st.get("code")
                                and (when_needed or st["key"] not in SKIPPABLE)
                                and stage_repository(st, domain, inputs, monolith)[0] is None))
    if not titles:
        return None
    with session_scope() as s:
        row = s.get(Domain, (project, domain))
        name = row.name if row else domain
    return f"Set the repository of {', '.join(titles)} in {name}'s Agent inputs"


def _missing_prefix(project: str, stages: list[dict]) -> str | None:
    """A workflow with a step that builds the domain's modules or services names their packages and artifacts from the
    project's base package prefix (app/domain_modules.py): it is needed before such a run starts."""
    from app import domain_modules

    if not any(st.get("key") in (*domain_modules.STEPS, *domain_modules.DESIGN_STEPS) for st in stages):
        return None
    if domain_modules.prefix(project):
        return None
    return "Set the base package prefix (Project → Settings, e.g. com.acme): the domain's packages are named from it"


def _fresh_branch(project: str, urls: list[str], name: str) -> str:
    """The feature branch a new run works on: `name`, or, when git already has it (an earlier run of the feature,
    deleted or reset since, left it there) or a run uses it, the next free version (-v2, -v3, ...), so the new run
    starts from the base branch rather than on that earlier work. Naming the old branch when starting continues on it.
    Checked as the repositories were last fetched."""
    with session_scope() as s:
        used = set(s.scalars(select(Run.branch).where(Run.project == project)))
    taken = lambda b: b in used or any(repos.has_branch(project, u, b) for u in urls)  # noqa: E731
    if not taken(name):
        return name
    version = 2
    while taken(f"{name}-v{version}") and version < 1000:
        version += 1
    return f"{name}-v{version}"


def plan(project: str, workflow: dict, domain: str, feature: str, base_branches: dict | None = None,
         feature_branch: str | None = None) -> dict:
    """Where a new run of the workflow works: each repository with its base branch (the user's choices, else the
    step's or project's), the feature branch cut in all of them, and whether the run ends by committing."""
    base_branches = base_branches or {}
    cfg = git_settings(workflow)
    project_cfg = settings_store.get_project(project)
    stages = workflow.get("stages") or []
    listed = []
    if project_cfg["monolith_repo"]:
        listed.append({"key": ROOT, "name": "Monolith", "url": project_cfg["monolith_repo"], "root": True,
                       "base_branch": (base_branches.get(ROOT) or "").strip() or project_cfg["monolith_branch"],
                       "steps": monolith_steps(stages, domain, project, project_cfg["monolith_repo"])})
    for r in code_repositories(stages, domain, project):
        listed.append({**r, "name": r["key"], "root": False,
                       "base_branch": (base_branches.get(r["key"]) or "").strip() or r["base_branch"]})
    default = branch_name(cfg["branch_pattern"], domain, feature)
    chosen = (feature_branch or "").strip() or _fresh_branch(project, [r["url"] for r in listed], default)
    out = {"commit": cfg["commit"] and bool(listed), "domain": domain,
           "missing": missing_repositories(stages, domain, project) or _missing_prefix(project, stages),
           "feature_branch": chosen, "earlier_branch": default if chosen != default else None,
           "repositories": []}
    for r in listed:
        info = {**r, "default_branch": None, "feature_branch_exists": None, "base_branch_exists": None}
        mirror = repos.mirror_dir(project, r["url"])
        if mirror.exists():
            info["default_branch"] = repos.default_branch(mirror)
            info["feature_branch_exists"] = repos.has_branch(project, r["url"], out["feature_branch"])
            base = info["base_branch"] or info["default_branch"]
            info["base_branch_exists"] = bool(base) and git_ops.commit_info(mirror, base) is not None
            info["empty"] = git_ops.is_empty(mirror)
        out["repositories"].append(info)
    return out


def check_branches(project: str, info: dict) -> None:
    """Refuse branch names a new run can't use (ValueError)."""
    feature = info["feature_branch"]
    if not git_ops.valid_branch(feature):
        raise ValueError("The feature branch must be a valid git branch name, e.g. migration/loan/approve")
    if info.get("missing"):
        raise ValueError(info["missing"])
    with session_scope() as s:
        # Each run has its own branch: never one an unfinished run works on.
        unfinished = s.scalars(select(Run).where(Run.project == project, Run.branch == feature,
                                                 Run.status != "completed")).all()
        busy = {url for run in unfinished for url in [_monolith_url(run), *(r["url"] for r in run.repositories or [])]
                if url}
    for r in info["repositories"]:
        where = "the monolith" if r["root"] else r["url"]
        if r["base_branch"] and not git_ops.valid_branch(r["base_branch"]):
            raise ValueError(f"{r['base_branch']!r} isn't a valid base branch for {where}")
        if feature == (r["base_branch"] or r["default_branch"]):
            raise ValueError(f"The feature branch must differ from {where}'s base branch")
        if r.get("empty"):
            raise ValueError(f"{where} {git_ops.EMPTY}")
        if r["base_branch_exists"] is False:
            raise ValueError(f"{where} has no branch or tag {r['base_branch'] or r['default_branch']}")
        if r["url"] in busy:
            raise ValueError(f"Another unfinished run works on branch {feature} in {where}; choose another name")


def changes_monolith(stages: list[dict], domain: str, project: str, monolith: str | None = None) -> bool:
    """Whether any step of a workflow changes the monolith's code, for a domain (so a run needs its own checkout)."""
    return bool(monolith_steps(stages, domain, project, monolith))


def _monolith_url(run: Run) -> str | None:
    """The monolith, when the run changes its code: checked out already, or to be when its first such step starts."""
    if run.repository:
        return run.repository
    return run.legacy["url"] if workspace.monolith_pending(run) and changes_monolith(
        run.stage_defs, run.domain, run.project, run.legacy["url"]) else None


def run_repository(run: Run) -> str | None:
    """The repository at a run's workspace root (runs from before runs recorded it use the project's monolith)."""
    return run.repository if run.repository is not None else repos.project_repository(run.project) or None


def run_repos(run: Run) -> list[dict]:
    """The repositories a run works on that can be committed: {"key", "name", "url", "base_branch", "base_sha",
    "path", "root"}; the workspace root's first."""
    out = []
    url = run_repository(run)
    if url and run.base_sha:
        out.append({"key": ROOT, "name": "Monolith", "url": url, "base_branch": run.base_branch,
                    "base_sha": run.base_sha, "path": ".", "root": True})
    for r in run.repositories or []:
        if r.get("base_sha"):
            out.append({**r, "name": r.get("name") or r["key"], "root": False})
    return out


def _changed(run: Run, repo: dict) -> bool:
    path = Path(run.workspace) / repo["path"]
    return (path / ".git").exists() and git_ops.head(path) != repo["base_sha"]


def _pending(run: Run, repo: dict) -> bool:
    """Whether the repository has code changes not yet committed (or left out): files other than the studio's
    documents that changed since the last commit made from it (or since the run started)."""
    path = Path(run.workspace) / repo["path"]
    if not (path / ".git").exists():
        return False
    record = (run.delivery or {}).get("repos", {}).get(repo["key"]) or {}
    last = record.get("head_sha") or repo["base_sha"]
    head = git_ops.head(path)
    if head == last:
        return False
    changed = [f for f in git_ops.changed_files(path, last, head)
               if not (repo.get("root") and f.startswith(DOCS_ROOT + "/"))]
    in_git = record.get("restored_sha") or record.get("sha")  # the feature branch in git, as last pushed or brought in
    if in_git and git_ops.has_commit(path, in_git):  # a file back to what git has is no change to commit
        changed = [f for f in changed if git_ops.read_blob(path, head, f) != git_ops.read_blob(path, in_git, f)]
    return bool(changed)


def wants_review(run_id: str) -> bool:
    """Whether a finished run waits for changes to be reviewed and committed."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if not (run.delivery or {}).get("commit"):
            return False
        return any(_pending(run, r) for r in run_repos(run))


def commit_needed(run_id: str, stage_key: str) -> dict | None:
    """Right after a step changed code, a person reviews and commits it: what to show them, or None when the step
    left nothing uncommitted (or the run doesn't commit)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if not (run.delivery or {}).get("commit"):
            return None
        stage = next((d for d in run.stage_defs if d["key"] == stage_key), None)
        key = _step_target(run, stage or {})
        repo = next((r for r in run_repos(run) if r["key"] == key), None)
        if repo is None or not _pending(run, repo):
            return None
        return {"kind": "commit", "stage": stage_key, "stage_title": stage["title"],
                "repository": {k: repo[k] for k in ("key", "name", "url")}}


def repositories(run_id: str) -> list[dict]:
    """The run's repositories for the review: which have changes to commit, and what was committed so far."""
    workspace.ensure(run_id)
    with session_scope() as s:
        run = s.get(Run, run_id)
        done = (run.delivery or {}).get("repos", {})
        return [{**{k: r[k] for k in ("key", "name", "url", "base_branch", "root")}, "changed": _changed(run, r),
                 "pending": _pending(run, r), "delivery": done.get(r["key"])} for r in run_repos(run)]


# --- confirming branches before code changes -----------------------------------------------------------------------

def _step_target(run: Run, stage: dict) -> str | None:
    """The key of the repository a step changes code in (None: it writes documents only, or there's nowhere)."""
    monolith = run_repository(run) or (run.legacy or {}).get("url")
    url, _base = stage_repository(stage, run.domain, domain_inputs(run.project, run.domain), monolith)
    if url is None:
        return None
    if _is_root(url, monolith):
        return ROOT
    return next((r["key"] for r in run.repositories or [] if _same(r["url"], url)), None)


def step_checkout(stage: dict, nested: list[dict], domain: str, project: str, monolith: str | None) -> str | None:
    """Where a step changes code, relative to the workspace: "." (the monolith) or the run's checkout of its
    repository; None when the run hasn't checked it out (or the domain hasn't set it)."""
    url, _base = stage_repository(stage, domain, domain_inputs(project, domain), monolith)
    if url is None:
        return None
    if _is_root(url, monolith):
        return "."
    return next((r["path"] for r in nested if _same(r["url"], url)), None)


def confirmation_needed(run_id: str, stage_key: str) -> dict | None:
    """Before every step that changes code (each time it runs), the user confirms (or changes) the feature branch,
    and the base branch of a repository not checked out yet: what to show them, or None when the step writes
    documents only (or there is no repository to cut a branch in). `existing_branch` says when the feature branch
    already exists, and what the step then does."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        stage = next((d for d in run.stage_defs if d["key"] == stage_key), None)
        key = _step_target(run, stage or {})
        if key is None:
            return None
        if key == ROOT:
            url = run_repository(run)
            if workspace.monolith_pending(run):  # read from the shared copy so far: checked out once confirmed
                repo = {"key": ROOT, "name": "Monolith", "url": run.legacy["url"],
                        "base_branch": run.base_branch or run.legacy.get("branch") or None, "checked_out": False}
            elif not url or not run.base_sha:
                return None  # the workspace isn't a clone of the monolith: nothing to cut (or commit)
            else:
                repo = {"key": ROOT, "name": "Monolith", "url": url, "base_branch": run.base_branch,
                        "checked_out": True}
        else:
            r = next(r for r in run.repositories if r["key"] == key)
            repo = {"key": key, "name": r.get("name") or key, "url": r["url"], "base_branch": r.get("base_branch"),
                    "checked_out": bool(r.get("base_sha"))}
        project, feature = run.project, run.branch
        title = stage["title"]
    mirror = repos.mirror_dir(project, repo["url"])
    repo["default_branch"] = repos.default_branch(mirror) if mirror.exists() else None
    repo["base_behind"] = None  # commits of the base branch (as last fetched) the checkout doesn't have
    if repo["checked_out"] and mirror.exists():
        with session_scope() as s:
            checkout = next((r for r in run_repos(s.get(Run, run_id)) if r["key"] == repo["key"]), None)
            ws = Path(s.get(Run, run_id).workspace) / checkout["path"] if checkout else None
        if ws is not None:
            repo["base_behind"] = _base_behind(ws, mirror, repo["base_branch"])
    return {"kind": "branches", "stage": stage_key, "stage_title": title, "feature_branch": feature, "repository": repo,
            "existing_branch": existing_branch(project, repo, feature, title)}


def existing_branch(project: str, repo: dict, feature: str, title: str) -> dict | None:
    """When the feature branch already exists in the repository: {"in_run": the run already works on it, "message"}.
    (For a repository not checked out yet this reads the server's copy as last fetched; confirming fetches it.)"""
    if repo["checked_out"]:
        return {"in_run": True, "message": f"Branch {feature} already exists in {repo['name']}: this run is working on "
                                           f"it. When you confirm, the latest from it in git is fetched, and {title} "
                                           "continues on that."}
    if repos.has_branch(project, repo["url"], feature):
        base = repo.get("base_branch") or repo.get("default_branch") or "the base branch"
        return {"in_run": False, "message": f"Branch {feature} already exists in {repo['url']}. {title} continues on "
                                            f"it, from its latest commit, instead of cutting a new branch from {base}."}
    return None


def check_confirmation(run_id: str, key: str, feature: str, base: str | None, user_id: int | None) -> None:
    """Refuse branches a confirmation can't use (ValueError): fetches the repository first, as the user."""
    if not git_ops.valid_branch(feature):
        raise ValueError("The feature branch must be a valid git branch name, e.g. migration/loan/approve")
    if base and not git_ops.valid_branch(base):
        raise ValueError(f"{base!r} isn't a valid base branch name")
    with session_scope() as s:
        run = s.get(Run, run_id)
        project, current = run.project, run.branch
        checked_out = [r["url"] for r in run_repos(run)]
        urls = list(checked_out)
        target = next((r for r in run.repositories or [] if r["key"] == key), None)
        if key == ROOT and workspace.monolith_pending(run):  # its own checkout is cut once confirmed
            target = {"url": run.legacy["url"], "base_branch": run.base_branch or run.legacy.get("branch") or None}
        others = s.scalars(select(Run).where(Run.project == project, Run.branch == feature, Run.status != "completed",
                                             Run.id != run_id)).all()  # each run has its own branch
        busy = {u for o in others for u in [_monolith_url(o), *(r["url"] for r in o.repositories or [])] if u}
    if target is not None and not target.get("base_sha"):
        urls.append(target["url"])
    for url in urls:
        if url in busy:
            raise ValueError(f"Another unfinished run works on branch {feature} in {url}; choose another name")
    if target is not None and not target.get("base_sha"):  # about to be cut: its base must exist
        try:
            mirror = repos.fetch(project, target["url"], user_id)
        except git_ops.GitError as e:
            raise ValueError(f"Could not fetch {target['url']}: {git_auth.scrub(str(e))}")
        if git_ops.is_empty(Path(mirror)):
            raise ValueError(f"{target['url']} {git_ops.EMPTY}")
        base_branch = base or target.get("base_branch") or repos.default_branch(mirror)
        if git_ops.commit_info(mirror, f"refs/heads/{base_branch}") is None and git_ops.commit_info(mirror, base_branch) is None:
            raise ValueError(f"{target['url']} has no branch or tag {base_branch}")
        if feature == base_branch:
            raise ValueError("The feature branch must differ from the base branch")
    # Renaming the feature branch renames it in every repository the run has checked out, so the new name must be free
    # there (pushing would clash). In the repository about to be checked out, an existing branch is not a clash: the
    # step continues on it (apply_confirmation).
    if feature != current:
        for url in dict.fromkeys(checked_out):
            if repos.has_branch(project, url, feature):
                raise ValueError(f"{url} already has a branch {feature}, and renaming the run's branch there would "
                                 "clash with it; choose another name")


def apply_confirmation(run_id: str, key: str, answer: dict, log) -> None:
    """Use the confirmed branches: rename the feature branch everywhere if it changed, check out the repository
    (cut from its base branch) if it isn't yet, and bring in the latest of the feature branch in git, in every
    repository the run has checked out (the step reads them: the module a service step lifts, the lifted service
    Modernise converts); with `update_base` in the answer, the latest of the repository's base branch too."""
    with run_lock(run_id):
        _apply_confirmation(run_id, key, answer, log)


def _apply_confirmation(run_id: str, key: str, answer: dict, log) -> None:
    feature = (answer.get("feature_branch") or "").strip()
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, project, user_id = Path(run.workspace), run.project, answer.get("user_id") or run.started_by
        current = run.branch
        nested = [dict(r) for r in run.repositories or []]
    feature = feature or current
    if feature != current:
        for path in [ws, *(ws / r["path"] for r in nested if r.get("base_sha"))]:
            if (path / ".git").exists():
                git_ops.git(path, "branch", "-m", current, feature)
        log(f"Feature branch renamed to {feature}")
    if key == ROOT:
        with session_scope() as s:
            pending = workspace.monolith_pending(s.get(Run, run_id))
        if pending:
            workspace.check_out_monolith(run_id, feature, (answer.get("base_branch") or "").strip() or None,
                                         user_id, log)
    for r in nested:
        if r["key"] == key and not r.get("base_sha"):
            r["base_branch"] = (answer.get("base_branch") or "").strip() or r.get("base_branch")
            mirror = _latest(project, r["url"], user_id)
            exists = git_ops.commit_info(Path(mirror), f"refs/heads/{feature}") is not None
            r["base_sha"] = git_ops.init_workspace(ws / r["path"], mirror, feature,
                                                   source_branch=feature if exists else r["base_branch"])
            r["base_branch"] = r["base_branch"] or repos.default_branch(Path(mirror))
            log(f"Checked out {r['url']}: the existing branch {feature}, at its latest commit" if exists else
                f"Checked out {r['url']}: branch {feature} cut from {r['base_branch']}")
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        delivery["confirmed"] = sorted({*delivery.get("confirmed", []), key})
        run.delivery, run.branch, run.repositories = delivery, feature, nested or None
    update_from_git(run_id, key, user_id, log)
    if answer.get("update_base"):
        merge_base_branch(run_id, key, user_id, log)
    # The step reads the run's other repositories too (the monolith's module a service step lifts, the lifted service
    # Modernise converts and its acceptance suite): each gets the latest of its feature branch as well.
    with session_scope() as s:
        others = [r["key"] for r in run_repos(s.get(Run, run_id)) if r["key"] != key]
    for other in others:
        update_from_git(run_id, other, user_id, log)


def _latest(project: str, url: str, user_id: int | None) -> str:
    """The repository's mirror with the latest from git. A step that changes code must not work on a stale copy, so
    when git can't be reached it doesn't start (DeliveryError): retry once git is reachable."""
    try:
        return str(repos.fetch(project, url, user_id))
    except (git_ops.GitError, OSError) as e:
        raise DeliveryError(f"Could not fetch the latest of {url}, so the step didn't start (it would work on an older "
                            f"copy): {git_auth.scrub(str(e))}. Retry once git is reachable")


def _base_behind(ws: Path, mirror: Path, base_branch: str | None) -> int | None:
    """How many commits of the base branch (as last fetched) the checkout `ws` doesn't have; None when unknown."""
    try:
        base = base_branch or repos.default_branch(mirror)
        if not base or git_ops.commit_info(mirror, f"refs/heads/{base}") is None:
            return None
        git_ops.git(ws, "fetch", "--quiet", str(mirror), f"+refs/heads/{base}:refs/studio/base")
        return git_ops.count_commits(ws, "HEAD", "refs/studio/base") or 0
    except (git_ops.GitError, OSError):
        return None


def merge_base_branch(run_id: str, key: str, user_id: int | None, log) -> None:
    """Bring the latest of the repository's base branch into the run's checkout (merged), when the person confirming
    the branches asks: work merged into the base branch since the run started (another feature's, or a fix made by
    hand) is then there for the step. A conflict leaves everything as it was and the step doesn't start."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        repo = next((r for r in run_repos(run) if r["key"] == key), None)
        if repo is None:
            return
        ws, project = Path(run.workspace) / repo["path"], run.project
        base_branch = repo.get("base_branch") if key != ROOT else run.base_branch
    mirror = Path(_latest(project, repo["url"], user_id))
    base = base_branch or repos.default_branch(mirror)
    if not base or git_ops.commit_info(mirror, f"refs/heads/{base}") is None:
        log(f"{repo['name']} has no branch {base or '(default)'} to bring in")
        return
    git_ops.git(ws, "fetch", "--quiet", str(mirror), f"+refs/heads/{base}:refs/studio/base")
    behind = git_ops.count_commits(ws, "HEAD", "refs/studio/base") or 0
    if not behind:
        log(f"{repo['name']} already has the latest of {base}")
        return
    try:
        git_ops.git(ws, "merge", "--quiet", "--no-edit", "-m", f"The latest of {base} from git", "refs/studio/base")
    except git_ops.GitError:
        conflicts = git_ops.git(ws, "diff", "--name-only", "--diff-filter=U").split()
        git_ops.git(ws, "merge", "--abort")
        raise DeliveryError(f"The latest of {base} in {repo['name']} conflicts with this run's work, in "
                            f"{', '.join(conflicts[:10]) or 'some files'}. Merge {base} into the feature branch in git "
                            "and resolve it there (or don't bring it in), then retry")
    log(f"Brought in the latest of {base} in {repo['name']}: {behind} newer commit(s), merged")


def update_from_git(run_id: str, key: str, user_id: int | None, log) -> None:
    """Before a step changes code (when its branches are confirmed), the repository's checkout gets the latest of the
    feature branch in git, whoever pushed it: fast-forwarded when the checkout is behind, otherwise merged. The run's
    own commits are built apart from its checkout (and a fresh re-run rolls the checkout back), so git can hold work
    the checkout lacks even when only this run pushed. Where both changed the same lines, git's version wins when the
    checkout has no work since its last push; when it has unpushed work, a conflict leaves everything as it was and
    the step does not start (DeliveryError names the files)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        repo = next((r for r in run_repos(run) if r["key"] == key), None)
        if repo is None:
            return  # not checked out (nothing to update), or no repository
        ws, project, feature = Path(run.workspace) / repo["path"], run.project, run.branch
        record = ((run.delivery or {}).get("repos") or {}).get(key) or {}
    where = f"{feature} in {repo['name']}"
    mirror = _latest(project, repo["url"], user_id)
    if git_ops.commit_info(Path(mirror), f"refs/heads/{feature}") is None:
        return  # the branch isn't in git yet: the run's checkout is the latest
    git_ops.git(ws, "fetch", "--quiet", str(mirror), f"+refs/heads/{feature}:refs/studio/feature")
    remote, local = git_ops.git(ws, "rev-parse", "refs/studio/feature"), git_ops.head(ws)
    if _replacing(ws, record):
        _update_replacing(run_id, key, ws, record, remote, feature, where, log)
        return
    if git_ops.is_ancestor(ws, remote, local):
        log(f"Fetched {where} from git: this run's copy already has its latest ({remote[:10]})")
        return
    if git_ops.is_ancestor(ws, local, remote):
        count = git_ops.count_commits(ws, local, remote)
        own = record.get("sha") if record.get("branch") == feature else None
        rolled_back = bool(own) and git_ops.has_commit(ws, own) and not git_ops.is_ancestor(ws, own, local)
        git_ops.git(ws, "merge", "--quiet", "--ff-only", "refs/studio/feature")
        log(f"Fetched the latest of {where}: {count} newer commit(s), now at {remote[:10]}"
            + (". That includes code this run committed and pushed before it was rolled back (a fresh re-run or a "
               "redo): git's version stays, and the step works on top of it" if rolled_back else ""))
        _brought_in(run_id, key, feature, remote, remote)
        return
    # Work in the checkout since the run last pushed or brought the branch in (steps gone on without committing):
    # kept, and a conflict with it stops the step. Without any, the checkout holds nothing git doesn't, so git's
    # version wins.
    unpushed = not (record.get("branch") == feature and record.get("head_sha") == local)
    try:
        git_ops.git(ws, "merge", "--quiet", "--no-edit", *([] if unpushed else ["-X", "theirs"]), "-m",
                    f"The latest of {feature} from git ({remote[:10]})", "refs/studio/feature")
    except git_ops.GitError:
        conflicts = git_ops.git(ws, "diff", "--name-only", "--diff-filter=U").split()
        git_ops.git(ws, "merge", "--abort")
        files = ", ".join(conflicts[:10]) or "some files"
        raise DeliveryError(
            f"{where} has changes in git that conflict with this run's work not pushed yet, in {files}. Resolve it in "
            "git (or commit the run's work first), then retry the run" if unpushed else
            f"{where} in git could not be merged into this run's copy automatically, in {files} (for example a file "
            "deleted on one side and changed on the other). Resolve it in git, then retry the run")
    log(f"Fetched the latest of {where} ({remote[:10]}) and merged it into this run's copy"
        + (", keeping this run's work not pushed yet" if unpushed else ""))
    _brought_in(run_id, key, feature, remote, None if unpushed else git_ops.head(ws))


def mark_rolled_back(run_id: str) -> None:
    """A fresh re-run or a redo rolled the run's checkouts back to where a step started. Code the run already pushed
    may be gone from them: until its next commit, the feature branch in git still has that code, and it is not
    brought back in (only what others push on top of it), and the next commit undoes it (see `_undone`)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        done = {k: dict(v) for k, v in (delivery.get("repos") or {}).items()}
        paths = {r["key"]: Path(run.workspace) / r["path"] for r in run_repos(run)}
        changed = False
        for key, rec in done.items():
            if rec.get("status") != "pushed" or rec.get("replaces"):
                continue
            path, pushed_from = paths.get(key), rec.get("head_sha")
            if (path is not None and (path / ".git").exists() and git_ops.has_commit(path, pushed_from)
                    and git_ops.is_ancestor(path, pushed_from, git_ops.head(path))):
                continue  # rolled back to after the push: the checkout still has the pushed code
            rec["replaces"] = rec["seen"] = rec.get("restored_sha") or rec["sha"]
            changed = True
        if changed:
            run.delivery = {**delivery, "repos": done}


def _replacing(ws: Path, record: dict) -> bool:
    return bool(record.get("replaces")) and git_ops.has_commit(ws, record.get("seen") or record["replaces"])


def _update_replacing(run_id: str, key: str, ws: Path, record: dict, remote: str, feature: str, where: str,
                      log) -> None:
    """update_from_git after a roll-back over pushed code: the run's copy gets only what was pushed to the feature
    branch since (by others, or by hand), not the code it rolled back. Changes that clash with the rolled-back copy
    stop the step (DeliveryError), leaving everything as it was."""
    seen = record.get("seen") or record["replaces"]
    if remote == seen:
        log(f"Fetched {where} from git: it still has the code this run pushed before the re-run; that code isn't "
            "brought back in, and the next commit replaces it")
        return
    if not git_ops.is_ancestor(ws, seen, remote):
        raise DeliveryError(f"{where} was rewritten in git since this run rolled back its pushed code, so what to keep "
                            "is unclear. Restore the branch in git (or delete it to start it again), then retry the run")
    if git_ops.git(ws, "status", "--porcelain"):
        raise DeliveryError(f"The run's copy of {where} has changes not committed, so the latest from git can't be "
                            "brought in. Retry the run")
    patch = subprocess.run(["git", "diff", "--binary", seen, remote], cwd=ws, capture_output=True, check=True).stdout
    if patch:
        applied = subprocess.run(["git", "apply", "--3way", "--index", "-"], cwd=ws, input=patch, capture_output=True)
        if applied.returncode != 0:
            conflicts = git_ops.git(ws, "diff", "--name-only", "--diff-filter=U").split()
            git_ops.git(ws, "reset", "--quiet", "--hard", "HEAD")  # clean before (checked above): nothing else lost
            files = ", ".join(conflicts[:10]) or "some files"
            raise DeliveryError(f"{where} has changes in git, pushed since this run rolled back its code, that conflict "
                                f"with the re-run's copy in {files}. Resolve it in git, then retry the run")
        git_ops.git(ws, "commit", "--quiet", "--allow-empty", "-m",
                    f"The latest of {feature} from git ({remote[:10]}), without this run's earlier code")
    count = git_ops.count_commits(ws, seen, remote)
    log(f"Fetched the latest of {where}: {count} newer commit(s) pushed since this run's earlier code, brought in "
        "without that code (the next commit replaces it)")
    _brought_in(run_id, key, feature, remote, None, seen=remote)


def _undone(ws: Path, record: dict, head: str, latest: str, remote: str | None) -> dict[str, str]:
    """After a roll-back over pushed code (mark_rolled_back): the files of that push the run's copy no longer has as
    the feature branch in git does, with their status for the review (D: deleted from git, M: back to the run's
    version). Committing them undoes the code pushed before."""
    replaces = record.get("replaces")
    if not replaces or not remote or not git_ops.has_commit(ws, replaces):
        return {}
    since = git_ops.git(ws, "merge-base", replaces, latest)
    out = {}
    for path in git_ops.changed_files(ws, since, replaces):
        ours = git_ops.read_blob(ws, head, path)
        if ours != git_ops.read_blob(ws, remote, path):
            out[path] = "D" if ours is None else "M"
    return out


def _brought_in(run_id: str, key: str, feature: str, remote: str, head: str | None, **extra) -> None:
    """Record that the run's copy now has the feature branch in git at `remote`: later commits go on top of it. With
    `head` (nothing of the run's was unpushed), the review counts changes from there: what came from git isn't the
    run's to commit."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        done = dict(delivery.get("repos") or {})
        done[key] = {**(done.get(key) or {}), "branch": feature, "restored_sha": remote,
                     **({"head_sha": head} if head else {}), **extra}
        run.delivery = {**delivery, "repos": done}


def refresh_code_settings(run_id: str) -> None:
    """When a run goes on (a restart, an approval, a retry) or waits for its branches to be confirmed, it picks up the
    workflow's current code settings (which steps change code, and where): the run's workflow snapshot gets them,
    repositories new to the run are added, to be checked out when first needed, and a repository not checked out yet
    follows its step's base branch, or is dropped when no step uses it any more. A repository already checked out
    stays as it is. Only the code settings change; the steps stay as the run started with."""
    from app import projects, workflow_store
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run.scope not in (None, "operation"):
            return
        try:
            with projects.use(run.project):
                current = workflow_store.get(run.workflow or "standard")  # runs from before workflows: the standard
        except KeyError:
            return
        code = {st["key"]: st.get("code") for st in current["stages"] if st["kind"] == "agent"}
        stages = [{**d, "code": code.get(d["key"], d.get("code"))} if d["kind"] == "agent" else d
                  for d in run.stage_defs]
        wanted = {r["url"]: r for r in code_repositories(stages, run.domain, run.project,
                                                         run_repository(run) or (run.legacy or {}).get("url"))}
        nested = []
        for r in run.repositories or []:
            if r.get("base_sha"):  # checked out: its branches are set
                nested.append(dict(r))
            elif r["url"] in wanted:
                nested.append({**r, "base_branch": wanted[r["url"]]["base_branch"] or None})
        known = {r["url"] for r in nested}
        for r in wanted.values():
            if r["url"] not in known:
                key = _repo_key(r["url"], {ROOT, *(x["key"] for x in nested)})
                nested.append({"key": key, "name": key, "url": r["url"], "base_branch": r["base_branch"] or None,
                               "path": f"{NESTED_DIR}/{key}", "base_sha": None})
        run.workflow_stages = stages
        run.repositories = nested or None
        # Whether the run reviews and commits its code, as for a new run (plan): the workflow commits and there is a
        # repository to commit to. A run started before any step changed code had none, so it learns it here.
        target = run_repository(run) or (run.legacy or {}).get("url") or nested
        commit = git_settings(current)["commit"] and bool(target)
        if (run.delivery or {}).get("commit") != commit:
            run.delivery = {**(run.delivery or {}), "commit": commit}


# --- reviewing the changes ------------------------------------------------------------------------------------------

def _is_binary(data: bytes | None) -> bool:
    return data is not None and b"\0" in data[:8000]


def _text(data: bytes | None) -> str | None:
    return None if data is None else decode(data)  # the repository's files may not be UTF-8 (app/text.py)


def _name_status(ws: Path, base: str, target: str) -> list[tuple[str, str]]:
    out = git_ops.git(ws, "diff", "--no-renames", "--name-status", base, target)
    return [(line[0], line.split("\t", 1)[1]) for line in out.splitlines() if line]


def _numstat(ws: Path, base: str, target: str) -> dict[str, tuple[int | None, int | None]]:
    out = git_ops.git(ws, "diff", "--no-renames", "--numstat", base, target)
    stats = {}
    for line in out.splitlines():
        added, deleted, path = line.split("\t", 2)
        stats[path] = (None, None) if added == "-" else (int(added), int(deleted))
    return stats


def _repo_of(run: Run, key: str) -> dict:
    repo = next((r for r in run_repos(run) if r["key"] == key), None)
    if repo is None:
        raise DeliveryError(f"This run has no repository {key}")
    return repo


def _sync(run_id: str, key: str, user_id: int | None) -> dict:
    """Fetch a repository of the run (as the user) and bring its latest base branch, and the feature branch if the
    remote has it, into its checkout. Returns what the review and commit work from."""
    root = workspace.ensure(run_id)
    with session_scope() as s:
        run = s.get(Run, run_id)
        repo = _repo_of(run, key)
        project, feature, domain, operation = run.project, run.branch, run.domain, run.operation
        delivery = dict((run.delivery or {}).get("repos", {}).get(key) or {})
    ws = root / repo["path"]
    url, base_sha, base_branch = repo["url"], repo["base_sha"], repo["base_branch"]
    warning = None
    try:
        mirror = repos.fetch(project, url, user_id)
    except git_ops.GitError as e:
        mirror = repos.mirror_dir(project, url)
        if not mirror.exists():
            raise DeliveryError(f"Could not fetch {url}: {git_auth.scrub(str(e))}")
        warning = f"Could not fetch the latest changes, so this compares with the copy on the server: {git_auth.scrub(str(e))}"
    base_branch = base_branch or repos.default_branch(mirror)
    git_ops.git(ws, "fetch", "--quiet", str(mirror), f"+refs/heads/{base_branch}:refs/studio/base")
    remote_feature = None
    if git_ops.commit_info(mirror, f"refs/heads/{feature}"):
        git_ops.git(ws, "fetch", "--quiet", str(mirror), f"+refs/heads/{feature}:refs/studio/feature")
        remote_feature = git_ops.git(ws, "rev-parse", "refs/studio/feature")
    latest = git_ops.git(ws, "rev-parse", "refs/studio/base")
    try:
        # Where the run's work and the base branch part: the run's starting commit, or (when the checkout was
        # restored from the feature branch it pushed) the base branch commit last merged into it.
        merge_base = git_ops.git(ws, "merge-base", "HEAD", latest)
    except git_ops.GitError:
        raise DeliveryError(f"The run's work no longer shares history with {base_branch} (was it rewritten?)")
    return {"ws": ws, "key": key, "name": repo["name"], "url": url, "project": project, "base_sha": base_sha,
            "base_branch": base_branch, "feature": feature, "latest": latest, "merge_base": merge_base,
            "head": git_ops.head(ws), "remote_feature": remote_feature, "delivery": delivery, "warning": warning,
            # the studio's documents (workspace root only): offered, but not ticked, for the commit
            "doc_prefix": DOCS_ROOT + "/" if repo["root"] else None, "domain": domain, "operation": operation}


def _file_state(ctx: dict, status: str, path: str, upstream: set[str]) -> dict:
    """One changed file: the run's version, and how it merges with the latest base branch."""
    ws, labels = ctx["ws"], ("this run", "where the run started", ctx["base_branch"])
    ours = git_ops.read_blob(ws, ctx["head"], path)
    base = git_ops.read_blob(ws, ctx["merge_base"], path)
    theirs = git_ops.read_blob(ws, ctx["latest"], path) if path in upstream else base
    binary = any(_is_binary(d) for d in (ours, base, theirs))
    document = bool(ctx["doc_prefix"]) and path.startswith(ctx["doc_prefix"])
    state = {"path": path, "status": status, "document": document, "binary": binary,
             "upstream_changed": path in upstream, "conflict": None, "merged": None}
    if path not in upstream or ours == theirs:
        return state
    if ours is None and theirs is None:
        return state  # deleted on both sides
    if ours is None or theirs is None:
        state["conflict"] = "deleted_here" if ours is None else "deleted_there"
        return state
    if binary or max(len(ours), len(theirs)) > MAX_TEXT:
        state["conflict"] = "binary"
        return state
    merged, conflicts = git_ops.merge_file(ours, base or b"", theirs, labels)
    state["merged"] = merged
    if conflicts:
        state["conflict"] = "content"
    return state


def changes(run_id: str, key: str, user_id: int | None) -> dict:
    """Everything the review lists for one repository: branches, how far its base branch moved, each changed file."""
    with run_lock(run_id):
        return _changes(run_id, key, user_id)


def _changes(run_id: str, key: str, user_id: int | None) -> dict:
    ctx = _sync(run_id, key, user_id)
    ws = ctx["ws"]
    upstream = set() if ctx["latest"] == ctx["merge_base"] else set(
        git_ops.changed_files(ws, ctx["merge_base"], ctx["latest"]))
    stats = _numstat(ws, ctx["merge_base"], ctx["head"])
    files = []
    remote = ctx["remote_feature"]
    for status, path in _name_status(ws, ctx["merge_base"], ctx["head"]):
        state = _file_state(ctx, status, path, upstream)
        added, deleted = stats.get(path, (None, None))
        state.pop("merged")
        # Already in git exactly as the run has it, on the feature branch or the latest base branch (the commit goes on
        # top of it): nothing to commit.
        ours = git_ops.read_blob(ws, ctx["head"], path)
        same = ((bool(remote) and ours == git_ops.read_blob(ws, remote, path))
                or (path in upstream and ours == git_ops.read_blob(ws, ctx["latest"], path)))
        files.append({**state, "additions": added, "deletions": deleted, "same_in_git": same, "undo": False})
    # Code pushed before a fresh re-run (or a redo) that the run no longer has: committing it undoes it in git.
    listed = {f["path"] for f in files}
    undone = {p: st for p, st in _undone(ws, ctx["delivery"], ctx["head"], ctx["latest"], remote).items() if p not in listed}
    if undone:
        undo_stats = _numstat(ws, remote, ctx["head"])
        for path, status in undone.items():
            state = _file_state(ctx, status, path, upstream)
            state.pop("merged")
            added, deleted = undo_stats.get(path, (None, None))
            files.append({**state, "additions": added, "deletions": deleted, "same_in_git": False, "undo": True})
    # Only what is a change: a file already in git exactly as the run has it is left out (a commit leaves it as it is).
    unchanged = sum(1 for f in files if f["same_in_git"])
    files = sorted((f for f in files if not f["same_in_git"]), key=lambda f: (f["document"], f["path"]))  # code first
    previous = ctx["delivery"] if ctx["delivery"].get("status") == "pushed" else None
    return {"key": key, "name": ctx["name"], "repository": ctx["url"], "base_branch": ctx["base_branch"],
            "feature_branch": ctx["feature"], "base_sha": ctx["base_sha"], "head_sha": ctx["head"],
            "latest_sha": ctx["latest"], "behind": git_ops.count_commits(ws, ctx["merge_base"], ctx["latest"]) or 0,
            "remote_branch": ctx["remote_feature"], "previous": previous, "warning": ctx["warning"],
            "message": default_message(ctx["domain"], ctx["operation"], previous is not None), "files": files,
            "unchanged_in_git": unchanged,
            "conflicts": sum(1 for f in files if f["conflict"])}


def default_message(domain: str, feature: str, update: bool) -> str:
    return f"{'Update' if update else 'Migrate'} {domain} {feature}\n\nMade with Migration Studio."


def file_detail(run_id: str, key: str, path: str, latest_sha: str) -> dict:
    """One file for the diff and merge views: where the run started, the version on the feature branch in git (when
    the run has pushed it: what this commit changes is the difference to that), the run's version, the latest base
    branch's, and the merge of the two (with conflict markers when they conflict)."""
    with run_lock(run_id):
        return _file_detail(run_id, key, path, latest_sha)


def _file_detail(run_id: str, key: str, path: str, latest_sha: str) -> dict:
    root = workspace.ensure(run_id)
    with session_scope() as s:
        run = s.get(Run, run_id)
        repo = _repo_of(run, key)
        record = dict((run.delivery or {}).get("repos", {}).get(key) or {})
    ws = root / repo["path"]
    if not git_ops.has_commit(ws, latest_sha):
        raise DeliveryError("Reload the changes: the base branch was fetched again")
    base_sha = git_ops.git(ws, "merge-base", "HEAD", latest_sha)
    ctx = {"ws": ws, "head": git_ops.head(ws), "latest": latest_sha, "base_branch": repo["base_branch"] or "base branch",
           "merge_base": base_sha,
           "doc_prefix": DOCS_ROOT + "/" if repo["root"] else None}
    run_files = dict((p, st) for st, p in _name_status(ws, base_sha, ctx["head"]))
    in_git = git_ops.commit_info(ws, "refs/studio/feature") is not None
    run_files = {**_undone(ws, record, ctx["head"], latest_sha, "refs/studio/feature" if in_git else None), **run_files}
    if path not in run_files:
        raise DeliveryError(f"The run didn't change {path}")
    upstream = set(git_ops.changed_files(ws, ctx["merge_base"], latest_sha)) if ctx["merge_base"] != latest_sha else set()
    state = _file_state(ctx, run_files[path], path, upstream)
    original = git_ops.read_blob(ws, base_sha, path)
    ours = git_ops.read_blob(ws, ctx["head"], path)
    theirs = git_ops.read_blob(ws, latest_sha, path) if path in upstream else None
    # The feature branch as it is in git (fetched by the review into refs/studio/feature), when the run pushed it.
    pushed = git_ops.read_blob(ws, "refs/studio/feature", path) if in_git else None
    too_large = any(d is not None and len(d) > MAX_TEXT for d in (original, ours, theirs, pushed))
    show = not state["binary"] and not too_large
    return {**state, "too_large": too_large,
            "original": _text(original) if show else None, "ours": _text(ours) if show else None,
            "theirs": _text(theirs) if show else None, "merged": _text(state["merged"]) if show else None,
            "in_git": in_git, "pushed": _text(pushed) if show else None}


# --- committing -----------------------------------------------------------------------------------------------------

_MARKERS = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)


def _compare_url(url: str, base: str, branch: str) -> str | None:
    """A link to open a pull request, for GitHub and GitHub Enterprise repositories."""
    host = git_auth.host_of(url)
    if not host or not ("github" in host or host.endswith(".ghe.com")):
        return None
    m = re.match(r"^[\w.-]+@[\w.-]+:(.+)$", url)
    path = m.group(1) if m else urlsplit(url).path
    path = path.strip("/").removesuffix(".git")
    return f"https://{host}/{path}/compare/{base}...{branch}?expand=1" if path.count("/") == 1 else None


def commit(run_id: str, key: str, user_id: int, message: str, branch: str, files: list[str],
           resolutions: dict[str, dict], latest_sha: str, head_sha: str) -> dict:
    """Commit the chosen files of one repository (with conflicts resolved) on top of its latest base branch as
    reviewed, as the user, and push the feature branch with their credentials. The run is complete once every
    repository it changed is committed (or left out). Two people committing the same run at once go one after the
    other: the second sees the first's commit (and its review is then out of date)."""
    with run_lock(run_id):
        return _commit(run_id, key, user_id, message, branch, files, resolutions, latest_sha, head_sha)


def _commit(run_id: str, key: str, user_id: int, message: str, branch: str, files: list[str],
            resolutions: dict[str, dict], latest_sha: str, head_sha: str) -> dict:
    who = git_auth.identity(user_id)
    if who is None:
        raise DeliveryError("Set your git name and email first (My settings → Git)")
    if not message.strip():
        raise DeliveryError("Write a commit message")
    if not files:
        raise DeliveryError("Choose at least one file to commit")
    ctx = _sync(run_id, key, user_id)
    ws = ctx["ws"]
    if ctx["head"] != head_sha:
        raise DeliveryError("The run's changes have changed since you reviewed them; reload")
    if not git_ops.has_commit(ws, latest_sha):
        raise DeliveryError("Reload the changes: the base branch was fetched again")
    if not git_ops.valid_branch(branch) or branch == ctx["base_branch"]:
        raise DeliveryError("Choose a valid feature branch name, different from the base branch")
    merge_base = git_ops.git(ws, "merge-base", "HEAD", latest_sha)
    upstream = set(git_ops.changed_files(ws, merge_base, latest_sha)) if merge_base != latest_sha else set()
    run_files = dict((p, st) for st, p in _name_status(ws, merge_base, ctx["head"]))
    run_files = {**_undone(ws, ctx["delivery"], ctx["head"], latest_sha, ctx["remote_feature"]), **run_files}
    unknown = [f for f in files if f not in run_files]
    if unknown:
        raise DeliveryError(f"The run didn't change {unknown[0]}")

    # The branch on the remote: ours from an earlier commit of this run (continue it), or someone else's (refuse).
    # (or, when the checkout was restored from the feature branch, that branch's commit it started from)
    # The branch in git: what this run last pushed, or brought into its copy (update_from_git, a rebuild).
    previous = ((ctx["delivery"].get("restored_sha") or ctx["delivery"].get("sha"))
                if ctx["delivery"].get("branch") in (branch, None) else None)
    remote = ctx["remote_feature"] if branch == ctx["feature"] else _remote_head(ctx, branch)
    if remote and remote != previous:
        if not git_ops.is_ancestor(ws, remote, ctx["head"]):
            # Pushed to since: the commit goes on top of it, unless it changed files being committed now (that would
            # overwrite someone's work).
            since = (previous if previous and git_ops.has_commit(ws, previous) and git_ops.is_ancestor(ws, previous, remote)
                     else git_ops.git(ws, "merge-base", "HEAD", remote))
            clash = sorted(set(git_ops.changed_files(ws, since, remote)) & set(files))
            if clash:
                raise DeliveryError(f"{branch} has changed in git, in files you're committing: {', '.join(clash[:10])}. "
                                    "Leave them out, or re-run the step: the latest from git is brought in before it "
                                    "starts")
        previous = remote

    tmp_index = ws / ".git" / f"studio-commit-index-{uuid.uuid4().hex}"
    env = {"GIT_INDEX_FILE": str(tmp_index), "GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1],
           "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]}
    ctx_merge = {**ctx, "latest": latest_sha, "merge_base": merge_base}
    try:
        git_ops.git(ws, "read-tree", latest_sha, env=env)
        if previous:  # what's on the branch in git stays, except the chosen files (next)
            _keep_branch(ws, previous, latest_sha, set(files), env)
        for path in files:
            _stage(ws, ctx_merge, path, run_files[path], upstream, resolutions.get(path), env)
        tree = git_ops.git(ws, "write-tree", env=env)
    finally:
        tmp_index.unlink(missing_ok=True)
    parents = [previous] if previous else []
    if not previous or not _is_ancestor(ws, latest_sha, previous):
        parents.append(latest_sha)
    parent_tree = git_ops.git(ws, "rev-parse", f"{parents[0]}^{{tree}}")
    if tree == parent_tree and len(parents) == 1:
        raise DeliveryError("Nothing to commit: the chosen files already match the branch")
    args = ["commit-tree", tree]
    for p in parents:
        args += ["-p", p]
    sha = git_ops.git(ws, *args, stdin=message.strip() + "\n", env=env)

    try:
        git_ops.git(ws, "push", "--quiet", "--", ctx["url"], f"{sha}:refs/heads/{branch}",
                    env=git_auth.env_for(user_id, ctx["url"]))
    except git_ops.GitError as e:
        text = git_auth.scrub(str(e))
        hint = ("" if git_auth.credential(user_id, ctx["url"]) or not git_auth.uses_https(ctx["url"]) else
                " Add an access token for this repository's host in My settings → Git.")
        raise DeliveryError(f"Pushing to {ctx['url']} failed: {text}{hint}")
    try:
        repos.fetch(ctx["project"], ctx["url"], user_id)  # the server's copy now has the branch
    except git_ops.GitError:
        pass

    with session_scope() as s:
        user = s.get(User, user_id)
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        done = dict(delivery.get("repos") or {})
        before = done.get(key) or {}
        result = {"status": "pushed", "sha": sha, "branch": branch, "base_branch": ctx["base_branch"],
                  "head_sha": ctx["head"],  # the run's commit this was made from: later changes are new
                  # the stages this push covers: a restored checkout replays only stages after it
                  "exec_id": s.scalar(select(func.max(StageExecution.id)).where(StageExecution.run_id == run_id,
                                                                               StageExecution.status == "completed")),
                  "repository": ctx["url"], "name": ctx["name"], "latest_sha": latest_sha, "files": sorted(files),
                  "message": message.strip(), "by": user.display_name, "by_id": user_id,
                  "at": datetime.now(UTC).isoformat(), "compare_url": _compare_url(ctx["url"], ctx["base_branch"], branch),
                  "history": [*before.get("history", []),
                              *([{k: before.get(k) for k in ("sha", "branch", "at", "by")}]
                                if before.get("status") == "pushed" else [])]}
        done[key] = result
        run.delivery = {**delivery, "repos": done}
        # At the end of the workflow, the run is complete once nothing is left to commit; right after a step, the
        # workflow goes on (the API resumes it).
        if run.current_stage is None and not any(_pending(run, r) for r in run_repos(run)):
            run.status = "completed"
        left = run.current_stage is None and run.status != "completed"
    emit(run_id, "status", f"{result['by']} committed {len(files)} file(s) in {ctx['name']} as {sha[:10]} and pushed "
                           f"branch {branch}" + ("; other repositories are still to commit" if left else ""))
    return result


def _keep_branch(ws: Path, branch_sha: str, latest_sha: str, chosen: set[str], env: dict) -> None:
    """Into the commit's index (the latest base branch): every file the branch in git changed on top of the base,
    except the chosen ones, as it is on the branch, so a commit never undoes or deletes what is already in git. Where
    the base branch changed such a file too, the two are merged; when they conflict, the branch's version stays."""
    since = git_ops.git(ws, "merge-base", branch_sha, latest_sha)
    for path in git_ops.changed_files(ws, since, branch_sha):
        if path in chosen:
            continue
        ours = git_ops.read_blob(ws, branch_sha, path)
        if ours is None:  # deleted on the branch: stays deleted
            git_ops.git(ws, "update-index", "--force-remove", "--", path, env=env)
            continue
        theirs, base = git_ops.read_blob(ws, latest_sha, path), git_ops.read_blob(ws, since, path)
        if theirs is not None and theirs != base and not _is_binary(ours) and not _is_binary(theirs):
            merged, conflicts = git_ops.merge_file(ours, base or b"", theirs, ("branch", "base", "latest"))
            if not conflicts:
                _add_bytes(ws, path, merged, branch_sha, env)
                continue
        _add_from(ws, branch_sha, path, env)


def _remote_head(ctx: dict, branch: str) -> str | None:
    mirror = repos.mirror_dir(ctx["project"], ctx["url"])
    info = git_ops.commit_info(mirror, f"refs/heads/{branch}")
    if info is None:
        return None
    git_ops.git(ctx["ws"], "fetch", "--quiet", str(mirror), f"+refs/heads/{branch}:refs/studio/other")
    return info["sha"]


def _is_ancestor(ws: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=ws,
                          capture_output=True).returncode == 0


def _stage(ws: Path, ctx: dict, path: str, status: str, upstream: set[str], resolution: dict | None,
           env: dict) -> None:
    """Put one chosen file into the commit's index (which starts as the latest base branch)."""
    state = _file_state(ctx, status, path, upstream)
    head, latest = ctx["head"], ctx["latest"]
    if state["conflict"]:
        if not resolution:
            raise DeliveryError(f"Resolve the conflict in {path} first")
        if resolution.get("delete"):
            git_ops.git(ws, "update-index", "--force-remove", "--", path, env=env)
            return
        use = resolution.get("use")
        if use in ("ours", "theirs"):
            source = head if use == "ours" else latest
            if git_ops.read_blob(ws, source, path) is None:
                git_ops.git(ws, "update-index", "--force-remove", "--", path, env=env)
            else:
                _add_from(ws, source, path, env)
            return
        content = resolution.get("content")
        if not isinstance(content, str):
            raise DeliveryError(f"Resolve the conflict in {path} first")
        if _MARKERS.search(content):
            raise DeliveryError(f"{path} still has conflict markers")
        _add_bytes(ws, path, content.encode(), head if git_ops.read_blob(ws, head, path) is not None else latest, env)
        return
    if git_ops.read_blob(ws, head, path) is None:  # the run deleted it
        git_ops.git(ws, "update-index", "--force-remove", "--", path, env=env)
    elif state["merged"] is not None:  # both changed it, cleanly merged
        _add_bytes(ws, path, state["merged"], head, env)
    else:
        _add_from(ws, head, path, env)


def _mode(ws: Path, rev: str, path: str) -> str:
    out = git_ops.git(ws, "ls-tree", rev, "--", path)
    return out.split(" ", 1)[0] if out else "100644"


def _add_from(ws: Path, rev: str, path: str, env: dict) -> None:
    blob = git_ops.git(ws, "rev-parse", f"{rev}:{path}")
    git_ops.git(ws, "update-index", "--add", "--cacheinfo", f"{_mode(ws, rev, path)},{blob},{path}", env=env)


def _add_bytes(ws: Path, path: str, data: bytes, mode_from: str, env: dict) -> None:
    proc = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ws, input=data, capture_output=True)
    if proc.returncode != 0:
        raise DeliveryError(f"Could not store {path}: {proc.stderr.decode(errors='replace').strip()}")
    blob = proc.stdout.decode().strip()
    git_ops.git(ws, "update-index", "--add", "--cacheinfo", f"{_mode(ws, mode_from, path)},{blob},{path}", env=env)


def finish_without_commit(run_id: str, user_id: int) -> None:
    """Complete the run, leaving the repositories not committed yet as they are."""
    with session_scope() as s:
        user = s.get(User, user_id)
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        done = dict(delivery.get("repos") or {})
        at = datetime.now(UTC).isoformat()
        for r in run_repos(run):
            if _pending(run, r):
                before = done.get(r["key"]) or {}
                head = git_ops.head(Path(run.workspace) / r["path"])
                done[r["key"]] = ({**before, "head_sha": head, "left_out_at": at} if before.get("status") == "pushed"
                                  else {"status": "skipped", "name": r["name"], "repository": r["url"],
                                        "by": user.display_name, "at": at, "head_sha": head})
        run.delivery = {**delivery, "repos": done}
        run.status, run.current_stage = "completed", None
        name = user.display_name
    emit(run_id, "status", f"{name} finished the run without committing the remaining changes")
