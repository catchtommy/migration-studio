"""The legacy code (the project's monolith) checked out once on the server, read-only, for every run that only reads it.

A large monolith checked out into every run's workspace costs its full size each time. Instead, each project keeps
one shared copy of its base branch at a fixed commit, data/repos/<project>/legacy/<commit>/: a git worktree of the
project's mirror (app/repos.py), with its files made read-only. Runs read it through the agents' file tools at the
same paths as a checkout (src/..., pom.xml); their own workspace holds only their documents and the other repositories
they change. Only a run reaching a step that changes the monolith's code gets its own checkout, just before that step
(app/workspace.py check_out_monolith). Fetching the latest (Project page) makes a new copy current
for new runs; runs keep the copy they started with, and copies no unfinished run uses are deleted.

Like the mirrors, the copies are a cache: the commit a run reads is recorded in the database (Run.legacy), and its
copy is made again from the mirror when it is missing.
"""

import json
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app import repos, settings_store
from app.config import settings
from app.db.models import Run
from app.db.session import session_scope
from app.tools import git_ops

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock(project: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(project, threading.Lock())


def root(project: str) -> Path:
    return settings.repos_dir / project / "legacy"


def copy_dir(project: str, sha: str) -> Path:
    return root(project) / sha[:12]


def _current_file(project: str) -> Path:
    return root(project) / "CURRENT.json"


def current(project: str) -> dict | None:
    """The copy new runs of the project read: {"url", "branch", "sha", "subject", "at"}, or None."""
    f = _current_file(project)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except ValueError:
        return None


def _target(project: str) -> tuple[str, str]:
    cfg = settings_store.get_project(project)
    return cfg["monolith_repo"], cfg["monolith_branch"]


def _make(project: str, mirror: Path, sha: str) -> Path:
    """Check out `sha` read-only (once)."""
    path = copy_dir(project, sha)
    if (path / ".git").exists():
        return path
    if path.exists():
        _remove(project, path)  # a half-made copy
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        git_ops.git(mirror, "worktree", "add", "--detach", "--force", str(path), sha)
    except git_ops.GitError:
        _remove(project, path)
        raise
    _read_only(path)
    return path


def _read_only(path: Path) -> None:
    """Make every file (and, off Windows, every folder) read-only, so nothing changes the legacy code."""
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        if ".git" in Path(dirpath).relative_to(path).parts[:1]:
            continue
        for name in filenames:
            if name == ".git" and Path(dirpath) == path:
                continue
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, stat.S_IREAD | stat.S_IRGRP)
        if os.name != "nt" and Path(dirpath) != path:
            os.chmod(dirpath, stat.S_IREAD | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
    if os.name != "nt":
        os.chmod(path, stat.S_IREAD | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)


def _remove(project: str, path: Path) -> None:
    for dirpath, _dirs, _files in os.walk(path):  # make the folders writable again, so their files can go
        try:
            os.chmod(dirpath, stat.S_IRWXU)
        except OSError:
            pass
    git_ops.remove_tree(path)
    url, _branch = _target(project)
    mirror = repos.mirror_dir(project, url) if url else None
    if mirror and mirror.exists():
        try:
            git_ops.git(mirror, "worktree", "prune")
        except git_ops.GitError:
            pass


def _record(project: str, url: str, branch: str, mirror: Path, sha: str) -> dict:
    info = git_ops.commit_info(mirror, sha) or {}
    data = {"url": url, "branch": branch, "sha": sha, "subject": info.get("subject", ""),
            "at": datetime.now(UTC).isoformat()}
    f = _current_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def snapshot(project: str, user_id: int | None = None, branch: str | None = None) -> dict | None:
    """The copy a new run reads: the current one, made (cloning the mirror if needed) when there is none or the
    project's repository or base branch changed since. None when the project has no monolith repository. A run started
    from another `branch` than the project's reads that branch's latest commit instead (a copy of its own, shared with
    any other run at that commit)."""
    url, project_branch = _target(project)
    if not url:
        return None
    if branch and branch != project_branch:
        with _lock(project):
            mirror = Path(repos.ensure(project, url, user_id))
            sha = git_ops.git(mirror, "rev-parse", f"{branch}^{{commit}}")
            _make(project, mirror, sha)
            info = git_ops.commit_info(mirror, sha) or {}
            return {"url": url, "branch": branch, "sha": sha, "subject": info.get("subject", ""),
                    "at": datetime.now(UTC).isoformat()}
    branch = project_branch
    with _lock(project):
        cur = current(project)
        if cur and cur["url"] == url and cur["branch"] == branch and (copy_dir(project, cur["sha"]) / ".git").exists():
            return cur
        mirror = Path(repos.ensure(project, url, user_id))
        branch_ref = branch or repos.default_branch(mirror) or "HEAD"
        sha = git_ops.git(mirror, "rev-parse", f"{branch_ref}^{{commit}}")
        _make(project, mirror, sha)
        return _record(project, url, branch, mirror, sha)


def refresh(project: str) -> dict | None:
    """After the mirror was fetched: make the base branch's latest commit the current copy, and delete copies no
    unfinished run reads."""
    url, branch = _target(project)
    if not url:
        return None
    mirror = repos.mirror_dir(project, url)
    if not mirror.exists():
        return None
    with _lock(project):
        branch_ref = branch or repos.default_branch(mirror) or "HEAD"
        sha = git_ops.git(mirror, "rev-parse", f"{branch_ref}^{{commit}}")
        _make(project, mirror, sha)
        cur = current(project)
        data = cur if cur and cur["sha"] == sha and cur["url"] == url else _record(project, url, branch, mirror, sha)
        _collect(project, keep={sha})
        return data


def _collect(project: str, keep: set[str]) -> None:
    with session_scope() as s:
        for run in s.scalars(select(Run).where(Run.project == project, Run.status != "completed")):
            if run.legacy and run.legacy.get("sha"):
                keep.add(run.legacy["sha"])
    names = {sha[:12] for sha in keep}
    base = root(project)
    if not base.exists():
        return
    for child in base.iterdir():
        if child.is_dir() and child.name not in names:
            _remove(project, child)


def path_for(project: str, legacy: dict | None, user_id: int | None = None) -> Path | None:
    """Where a run reads the legacy code (made again from the mirror if the copy is missing)."""
    if not legacy or not legacy.get("sha"):
        return None
    path = copy_dir(project, legacy["sha"])
    if (path / ".git").exists():
        return path
    with _lock(project):
        mirror = Path(repos.ensure(project, legacy["url"], user_id))
        return _make(project, mirror, legacy["sha"])
