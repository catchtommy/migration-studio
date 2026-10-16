"""The repositories a project's runs work on, each kept once on the server.

A repository (the project's monolith, or one a workflow names) is cloned into a bare mirror under data/repos/<project>/
when it is saved, or by the first run that needs it. Every run makes its workspace with a local clone of the mirror:
no network, and git hard-links the objects, so a run costs little more than its checked-out files. A new run fetches
the latest changes first, so it starts from the newest commit (see latest_for_run); runs already started keep theirs.
Fetches use the acting user's git credentials (app/git_auth.py). Like workspaces, mirrors are a cache: they are cloned
again when missing.
"""

import hashlib
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from app import git_auth, settings_store
from app.config import settings
from app.tools import git_ops

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_fetching: set[str] = set()  # mirror paths being fetched
_errors: dict[str, str] = {}  # the last fetch's error per mirror path, until a fetch succeeds


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


def project_repository(project: str) -> str:
    return settings_store.get_project(project)["monolith_repo"]


def mirror_dir(project: str, url: str | None = None) -> Path:
    """Where the server keeps a repository of the project (its monolith when `url` is None)."""
    url = url or project_repository(project)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))[:40] or "repo"
    return settings.repos_dir / project / f"{name}-{hashlib.sha1(url.encode()).hexdigest()[:8]}.git"


def _fetched_at(path: Path) -> datetime | None:
    marker = path / "FETCH_HEAD" if (path / "FETCH_HEAD").exists() else path / "HEAD"
    return datetime.fromtimestamp(marker.stat().st_mtime, UTC) if marker.exists() else None


def ensure(project: str, url: str | None = None, user_id: int | None = None) -> str | None:
    """The local mirror runs clone from (cloned now if missing), or None when there's no repository."""
    url = url if url is not None else project_repository(project)
    if not url:
        return None
    path = mirror_dir(project, url)
    with _lock(path):
        if not path.exists():
            git_ops.clone_mirror(url, path, git_auth.env_for(user_id, url))
    return str(path)


def fetch(project: str, url: str | None = None, user_id: int | None = None) -> Path:
    """Fetch the latest changes of every branch and tag (cloning first if needed)."""
    url = url if url is not None else project_repository(project)
    if not url:
        raise ValueError("This project has no monolith repository")
    path = mirror_dir(project, url)
    env = git_auth.env_for(user_id, url)
    with _lock(path):
        _fetching.add(str(path))
        try:
            if path.exists():
                git_ops.fetch_mirror(path, url, env)
            else:
                git_ops.clone_mirror(url, path, env)
            _errors.pop(str(path), None)
        except git_ops.GitError as e:
            _errors[str(path)] = git_auth.scrub(str(e))
            raise
        finally:
            _fetching.discard(str(path))
    return path


def latest_for_run(project: str, url: str | None, user_id: int | None, log) -> str | None:
    """For a new run: the repository's mirror, brought up to date first so the run starts from the latest commit.
    When the fetch fails (network, credentials), the run goes on with the copy last fetched and `log` says so; only a
    missing mirror that can't be cloned fails the run."""
    if not url:
        return None
    path = mirror_dir(project, url)
    if not path.exists():
        return ensure(project, url, user_id)
    try:
        fetch(project, url, user_id)
    except git_ops.GitError as e:
        fetched = _fetched_at(path)
        when = f" (fetched {fetched:%Y-%m-%d %H:%M} UTC)" if fetched else ""
        log(f"Could not fetch the latest changes of {url}, so this run starts from the copy on the server{when}: "
            f"{git_auth.scrub(str(e))}")
    return str(path)


def fetch_in_background(project: str, user_id: int | None = None) -> bool:
    """Start a fetch of the project's monolith unless one is running; False when one already was."""
    url = project_repository(project)
    path = str(mirror_dir(project, url))
    with _locks_guard:
        if path in _fetching:
            return False
        _fetching.add(path)  # visible at once, before the thread starts

    def work():
        try:
            fetch(project, url, user_id)
        except Exception as e:  # noqa: BLE001 - kept for the status
            _errors[path] = git_auth.scrub(str(e))
        finally:
            _fetching.discard(path)

    threading.Thread(target=work, name=f"fetch-{project}", daemon=True).start()
    return True


def status(project: str) -> dict:
    """What the server has of the project's monolith: when it was fetched and the base branch's latest commit."""
    cfg = settings_store.get_project(project)
    url, branch = cfg["monolith_repo"], cfg["monolith_branch"]
    out = {"url": url, "branch": branch, "fetching": False, "error": None, "cloned": False, "fetched_at": None,
           "head": None, "default_branch": None}
    if not url:
        return out
    path = mirror_dir(project, url)
    out.update(fetching=str(path) in _fetching, error=_errors.get(str(path)), cloned=path.exists())
    if path.exists():
        out["fetched_at"] = _fetched_at(path)
        out["default_branch"] = default_branch(path)
        out["head"] = git_ops.commit_info(path, branch or "HEAD")
        if out["head"] is None and branch:
            out["error"] = out["error"] or f"The repository has no branch or tag {branch}"
    return out


def default_branch(path: Path) -> str | None:
    try:
        return git_ops.git(path, "symbolic-ref", "--short", "HEAD")
    except git_ops.GitError:
        return None


def has_branch(project: str, url: str, branch: str) -> bool | None:
    """Whether the server's copy of the repository has the branch (None: no copy yet)."""
    path = mirror_dir(project, url)
    if not path.exists():
        return None
    return git_ops.commit_info(path, f"refs/heads/{branch}") is not None


def behind(project: str, url: str | None, base_branch: str | None, base_sha: str | None) -> int | None:
    """How many commits the base branch has gained (as last fetched) since a run started from base_sha."""
    if not url or not base_sha:
        return None
    path = mirror_dir(project, url)
    if not path.exists():
        return None
    return git_ops.count_commits(path, base_sha, base_branch or "HEAD")


def remove(project: str) -> None:
    git_ops.remove_tree(settings.repos_dir / project)
