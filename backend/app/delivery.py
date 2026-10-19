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
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app import git_auth, repos, settings_store, workspace
from app.agents.base_agent import DOCS_ROOT
from app.db.models import Run, StageExecution, User
from app.db.session import emit, session_scope
from app.text import decode
from app.tools import git_ops

DEFAULT_PATTERN = "migration/{domain}/{feature}"
PLACEHOLDERS = ("domain", "feature", "run", "date")
MAX_TEXT = 1_000_000  # larger files are shown as "too large" and can only be taken whole
ROOT = "monolith"  # the key of the workspace root's repository
NESTED_DIR = ".studio/repos"  # where a run's other repositories are checked out, inside the workspace


class DeliveryError(RuntimeError):
    pass


# --- settings -------------------------------------------------------------------------------------------------------

def git_settings(workflow: dict | None) -> dict:
    """A workflow's git settings, with defaults: migrations review and commit their changes; discovery doesn't."""
    stored = (workflow or {}).get("git") or {}
    migration = (workflow or {}).get("scope", "operation") == "operation"
    return {"commit": bool(stored.get("commit", migration)) and migration,
            "branch_pattern": stored.get("branch_pattern", "") or DEFAULT_PATTERN}


def validate_git(values: dict) -> dict:
    """Clean a workflow's git settings, or ValueError."""
    out = {"commit": bool(values.get("commit", True)), "branch_pattern": str(values.get("branch_pattern") or "").strip()}
    if out["branch_pattern"]:
        unknown = set(re.findall(r"{(\w+)}", out["branch_pattern"])) - set(PLACEHOLDERS)
        if unknown:
            raise ValueError(f"Unknown placeholder {{{sorted(unknown)[0]}}}; use {', '.join('{%s}' % p for p in PLACEHOLDERS)}")
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


def code_repositories(stages: list[dict], domain: str) -> list[dict]:
    """The repositories other than the monolith that a workflow's steps change code in, for a domain:
    [{"key", "url", "base_branch", "steps": [titles]}] in workflow order."""
    found: dict[str, dict] = {}
    for st in stages:
        code = st.get("code") or {}
        template = code.get("repository") or ""
        if st.get("kind") != "agent" or not template:
            continue
        url = template.replace("{domain}", domain)
        if url not in found:
            found[url] = {"key": _repo_key(url, {ROOT, *(r["key"] for r in found.values())}), "url": url,
                          "base_branch": code.get("base_branch") or "", "steps": []}
        found[url]["steps"].append(st["title"])
    return list(found.values())


def plan(project: str, workflow: dict, domain: str, feature: str, base_branches: dict | None = None,
         feature_branch: str | None = None) -> dict:
    """Where a new run of the workflow works: each repository with its base branch (the user's choices, else the
    step's or project's), the feature branch cut in all of them, and whether the run ends by committing."""
    base_branches = base_branches or {}
    cfg = git_settings(workflow)
    project_cfg = settings_store.get_project(project)
    stages = workflow.get("stages") or []
    monolith_steps = [st["title"] for st in stages if st.get("kind") == "agent" and st.get("code")
                      and not st["code"].get("repository")]
    listed = []
    if project_cfg["monolith_repo"]:
        listed.append({"key": ROOT, "name": "Monolith", "url": project_cfg["monolith_repo"], "root": True,
                       "base_branch": (base_branches.get(ROOT) or "").strip() or project_cfg["monolith_branch"],
                       "steps": monolith_steps})
    for r in code_repositories(stages, domain):
        listed.append({**r, "name": r["key"], "root": False,
                       "base_branch": (base_branches.get(r["key"]) or "").strip() or r["base_branch"]})
    out = {"commit": cfg["commit"] and bool(listed),
           "feature_branch": (feature_branch or "").strip() or branch_name(cfg["branch_pattern"], domain, feature),
           "repositories": []}
    for r in listed:
        info = {**r, "default_branch": None, "feature_branch_exists": None, "base_branch_exists": None}
        mirror = repos.mirror_dir(project, r["url"])
        if mirror.exists():
            info["default_branch"] = repos.default_branch(mirror)
            info["feature_branch_exists"] = repos.has_branch(project, r["url"], out["feature_branch"])
            base = info["base_branch"] or info["default_branch"]
            info["base_branch_exists"] = bool(base) and git_ops.commit_info(mirror, base) is not None
        out["repositories"].append(info)
    return out


def check_branches(project: str, info: dict) -> None:
    """Refuse branch names a new run can't use (ValueError)."""
    feature = info["feature_branch"]
    if not git_ops.valid_branch(feature):
        raise ValueError("The feature branch must be a valid git branch name, e.g. migration/loan/approve")
    with session_scope() as s:
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
        if r["feature_branch_exists"]:
            raise ValueError(f"{where} already has a branch {feature}; choose another name")
        if r["base_branch_exists"] is False:
            raise ValueError(f"{where} has no branch or tag {r['base_branch'] or r['default_branch']}")
        if r["url"] in busy:
            raise ValueError(f"Another unfinished run works on branch {feature} in {where}; choose another name")


def changes_monolith(stages: list[dict]) -> bool:
    """Whether any step of a workflow changes the monolith's code (so a run needs its own checkout of it)."""
    return any(st.get("kind") == "agent" and st.get("code") and not st["code"].get("repository") for st in stages)


def _monolith_url(run: Run) -> str | None:
    """The monolith, when the run changes its code: checked out already, or to be when its first such step starts."""
    if run.repository:
        return run.repository
    return run.legacy["url"] if workspace.monolith_pending(run) and changes_monolith(run.stage_defs) else None


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
    last = ((run.delivery or {}).get("repos", {}).get(repo["key"]) or {}).get("head_sha") or repo["base_sha"]
    head = git_ops.head(path)
    if head == last:
        return False
    changed = git_ops.changed_files(path, last, head)
    return any(not (repo.get("root") and f.startswith(DOCS_ROOT + "/")) for f in changed)


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
    code = stage.get("code") or {}
    if stage.get("kind") != "agent" or not code:
        return None
    if not code.get("repository"):
        return ROOT
    url = code["repository"].replace("{domain}", run.domain)
    return next((r["key"] for r in run.repositories or [] if r["url"] == url), None)


def confirmation_needed(run_id: str, stage_key: str) -> dict | None:
    """Before a step changes code in a repository for the first time in a run, the user confirms (or changes) its
    base branch and the feature branch: what to show them, or None when there is nothing to confirm."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        stage = next((d for d in run.stage_defs if d["key"] == stage_key), None)
        key = _step_target(run, stage or {})
        if key is None or key in (run.delivery or {}).get("confirmed", []):
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
    return {"kind": "branches", "stage": stage_key, "stage_title": title, "feature_branch": feature, "repository": repo}


def check_confirmation(run_id: str, key: str, feature: str, base: str | None, user_id: int | None) -> None:
    """Refuse branches a confirmation can't use (ValueError): fetches the repository first, as the user."""
    if not git_ops.valid_branch(feature):
        raise ValueError("The feature branch must be a valid git branch name, e.g. migration/loan/approve")
    if base and not git_ops.valid_branch(base):
        raise ValueError(f"{base!r} isn't a valid base branch name")
    with session_scope() as s:
        run = s.get(Run, run_id)
        project, current = run.project, run.branch
        urls = [r["url"] for r in run_repos(run)]
        target = next((r for r in run.repositories or [] if r["key"] == key), None)
        if key == ROOT and workspace.monolith_pending(run):  # its own checkout is cut once confirmed
            target = {"url": run.legacy["url"], "base_branch": run.base_branch or run.legacy.get("branch") or None}
        others = s.scalars(select(Run).where(Run.project == project, Run.branch == feature, Run.status != "completed",
                                             Run.id != run_id)).all()
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
        base_branch = base or target.get("base_branch") or repos.default_branch(mirror)
        if git_ops.commit_info(mirror, f"refs/heads/{base_branch}") is None and git_ops.commit_info(mirror, base_branch) is None:
            raise ValueError(f"{target['url']} has no branch or tag {base_branch}")
        if feature == base_branch:
            raise ValueError("The feature branch must differ from the base branch")
    if feature != current or target is not None:
        for url in urls:
            if repos.has_branch(project, url, feature):
                raise ValueError(f"{url} already has a branch {feature}; choose another name")


def apply_confirmation(run_id: str, key: str, answer: dict, log) -> None:
    """Use the confirmed branches: rename the feature branch everywhere if it changed, and check out the repository
    (cut from its base branch) if it isn't yet."""
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
            mirror = repos.latest_for_run(project, r["url"], user_id, log)
            r["base_sha"] = git_ops.init_workspace(ws / r["path"], mirror, feature, source_branch=r["base_branch"])
            r["base_branch"] = r["base_branch"] or repos.default_branch(Path(mirror))
            log(f"Checked out {r['url']}: branch {feature} cut from {r['base_branch']}")
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        delivery["confirmed"] = sorted({*delivery.get("confirmed", []), key})
        run.delivery, run.branch, run.repositories = delivery, feature, nested or None


def refresh_code_settings(run_id: str) -> None:
    """When a run goes on (a restart, an approval, a retry) it picks up the workflow's current code settings (which
    steps change code, and where): the run's workflow snapshot gets them, and repositories new to the run are added,
    to be checked out when first needed. Only the code settings change; the steps stay as the run started with."""
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
        nested = [dict(r) for r in run.repositories or []]
        known = {r["url"] for r in nested}
        for r in code_repositories(stages, run.domain):
            if r["url"] not in known:
                key = _repo_key(r["url"], {ROOT, *(x["key"] for x in nested)})
                nested.append({"key": key, "name": key, "url": r["url"], "base_branch": r["base_branch"] or None,
                               "path": f"{NESTED_DIR}/{key}", "base_sha": None})
        run.workflow_stages = stages
        run.repositories = nested or None
        if run.delivery is None or "commit" not in run.delivery:
            run.delivery = {**(run.delivery or {}), "commit": git_settings(current)["commit"]}


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
    ctx = _sync(run_id, key, user_id)
    ws = ctx["ws"]
    upstream = set() if ctx["latest"] == ctx["merge_base"] else set(
        git_ops.changed_files(ws, ctx["merge_base"], ctx["latest"]))
    stats = _numstat(ws, ctx["merge_base"], ctx["head"])
    files = []
    for status, path in _name_status(ws, ctx["merge_base"], ctx["head"]):
        state = _file_state(ctx, status, path, upstream)
        added, deleted = stats.get(path, (None, None))
        state.pop("merged")
        files.append({**state, "additions": added, "deletions": deleted})
    files.sort(key=lambda f: (f["document"], f["path"]))  # code first
    previous = ctx["delivery"] if ctx["delivery"].get("status") == "pushed" else None
    return {"key": key, "name": ctx["name"], "repository": ctx["url"], "base_branch": ctx["base_branch"],
            "feature_branch": ctx["feature"], "base_sha": ctx["base_sha"], "head_sha": ctx["head"],
            "latest_sha": ctx["latest"], "behind": git_ops.count_commits(ws, ctx["merge_base"], ctx["latest"]) or 0,
            "remote_branch": ctx["remote_feature"], "previous": previous, "warning": ctx["warning"],
            "message": default_message(ctx["domain"], ctx["operation"], previous is not None), "files": files,
            "conflicts": sum(1 for f in files if f["conflict"])}


def default_message(domain: str, feature: str, update: bool) -> str:
    return f"{'Update' if update else 'Migrate'} {domain} {feature}\n\nMade with Migration Studio."


def file_detail(run_id: str, key: str, path: str, latest_sha: str) -> dict:
    """One file for the diff and merge views: where the run started, the run's version, the latest base branch's,
    and the merge of the two (with conflict markers when they conflict)."""
    root = workspace.ensure(run_id)
    with session_scope() as s:
        repo = _repo_of(s.get(Run, run_id), key)
    ws = root / repo["path"]
    if not git_ops.has_commit(ws, latest_sha):
        raise DeliveryError("Reload the changes: the base branch was fetched again")
    base_sha = git_ops.git(ws, "merge-base", "HEAD", latest_sha)
    ctx = {"ws": ws, "head": git_ops.head(ws), "latest": latest_sha, "base_branch": repo["base_branch"] or "base branch",
           "merge_base": base_sha,
           "doc_prefix": DOCS_ROOT + "/" if repo["root"] else None}
    run_files = dict((p, st) for st, p in _name_status(ws, base_sha, ctx["head"]))
    if path not in run_files:
        raise DeliveryError(f"The run didn't change {path}")
    upstream = set(git_ops.changed_files(ws, ctx["merge_base"], latest_sha)) if ctx["merge_base"] != latest_sha else set()
    state = _file_state(ctx, run_files[path], path, upstream)
    original = git_ops.read_blob(ws, base_sha, path)
    ours = git_ops.read_blob(ws, ctx["head"], path)
    theirs = git_ops.read_blob(ws, latest_sha, path) if path in upstream else None
    too_large = any(d is not None and len(d) > MAX_TEXT for d in (original, ours, theirs))
    show = not state["binary"] and not too_large
    return {**state, "too_large": too_large,
            "original": _text(original) if show else None, "ours": _text(ours) if show else None,
            "theirs": _text(theirs) if show else None, "merged": _text(state["merged"]) if show else None}


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
    repository it changed is committed (or left out)."""
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
    unknown = [f for f in files if f not in run_files]
    if unknown:
        raise DeliveryError(f"The run didn't change {unknown[0]}")

    # The branch on the remote: ours from an earlier commit of this run (continue it), or someone else's (refuse).
    # (or, when the checkout was restored from the feature branch, that branch's commit it started from)
    previous = ((ctx["delivery"].get("restored_sha") or ctx["delivery"].get("sha"))
                if ctx["delivery"].get("branch") == branch else None)
    remote = ctx["remote_feature"] if branch == ctx["feature"] else _remote_head(ctx, branch)
    if remote and remote != previous:
        raise DeliveryError(f"The repository's branch {branch} has commits this run didn't make. Choose another "
                            "branch name, or bring those changes into the run first.")

    tmp_index = ws / ".git" / "studio-commit-index"
    env = {"GIT_INDEX_FILE": str(tmp_index), "GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1],
           "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]}
    ctx_merge = {**ctx, "latest": latest_sha, "merge_base": merge_base}
    try:
        git_ops.git(ws, "read-tree", latest_sha, env=env)
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
