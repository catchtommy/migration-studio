"""Each project's monolith repository, kept once on the server.

The project's repository is cloned into a bare mirror (data/repos/<project>.git) when it is saved, or by the first run.
Every run makes its workspace with a local clone of the mirror: no network, and git hard-links the objects, so a run
costs little more than its checked-out files. A new run fetches the latest changes first, so it starts from the newest
commit (see latest_for_run); runs already started keep theirs. Like workspaces, the mirror is a cache: it is cloned
again when missing.
"""

import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from app import settings_store
from app.config import settings
from app.tools import git_ops

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_fetching: set[str] = set()
_errors: dict[str, str] = {}  # the last fetch's error per project, until a fetch succeeds


def _lock(project: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())


def mirror_dir(project: str) -> Path:
    return settings.repos_dir / f"{project}.git"


def _fetched_at(path: Path) -> datetime | None:
    marker = path / "FETCH_HEAD" if (path / "FETCH_HEAD").exists() else path / "HEAD"
    return datetime.fromtimestamp(marker.stat().st_mtime, UTC) if marker.exists() else None


def ensure(project: str) -> str | None:
    """The local repository runs of the project clone from (its mirror, cloned now if missing), or None when the
    project has no monolith repository."""
    url = settings_store.get_project(project)["monolith_repo"]
    if not url:
        return None
    path = mirror_dir(project)
    with _lock(project):
        if not path.exists():
            git_ops.clone_mirror(url, path)
        elif git_ops.git(path, "config", "--get", "remote.origin.url") != url:
            git_ops.fetch_mirror(path, url)  # the project's repository changed: the mirror follows it
    return str(path)


def latest_for_run(project: str, log) -> str | None:
    """For a new run: the project's mirror, brought up to date first so the run starts from the latest commit. When
    the fetch fails (network, credentials), the run goes on with the copy last fetched and `log` says so; only a
    missing mirror that can't be cloned fails the run."""
    url = settings_store.get_project(project)["monolith_repo"]
    if not url:
        return None
    path = mirror_dir(project)
    if not path.exists():
        return ensure(project)
    try:
        fetch(project)
    except git_ops.GitError as e:
        fetched = _fetched_at(path)
        when = f" (fetched {fetched:%Y-%m-%d %H:%M} UTC)" if fetched else ""
        log(f"Could not fetch the latest monolith changes, so this run starts from the copy on the server{when}: {e}")
    return str(path)


def fetch(project: str) -> None:
    """Fetch the latest changes of every branch and tag (cloning first if needed). New runs start from them; running
    ones keep the commit they started from."""
    url = settings_store.get_project(project)["monolith_repo"]
    if not url:
        raise ValueError("This project has no monolith repository")
    path = mirror_dir(project)
    with _lock(project):
        _fetching.add(project)
        try:
            if path.exists():
                git_ops.fetch_mirror(path, url)
            else:
                git_ops.clone_mirror(url, path)
            _errors.pop(project, None)
        except git_ops.GitError as e:
            _errors[project] = str(e)
            raise
        finally:
            _fetching.discard(project)


def fetch_in_background(project: str) -> bool:
    """Start a fetch unless one is running; False when one already was."""
    with _locks_guard:
        if project in _fetching:
            return False
        _fetching.add(project)  # visible at once, before the thread starts

    def work():
        try:
            fetch(project)
        except Exception as e:  # noqa: BLE001 - kept for the status
            _errors[project] = str(e)
        finally:
            _fetching.discard(project)

    threading.Thread(target=work, name=f"fetch-{project}", daemon=True).start()
    return True


def status(project: str) -> dict:
    """What the server has of the project's repository: when it was fetched and the base branch's latest commit."""
    cfg = settings_store.get_project(project)
    url, branch = cfg["monolith_repo"], cfg["monolith_branch"]
    path = mirror_dir(project)
    out = {"url": url, "branch": branch, "fetching": project in _fetching, "error": _errors.get(project),
           "cloned": path.exists(), "fetched_at": None, "head": None, "default_branch": None}
    if url and path.exists():
        out["fetched_at"] = _fetched_at(path)
        try:
            out["default_branch"] = git_ops.git(path, "symbolic-ref", "--short", "HEAD")
        except git_ops.GitError:
            pass
        out["head"] = git_ops.commit_info(path, branch or "HEAD")
        if out["head"] is None and branch:
            out["error"] = out["error"] or f"The repository has no branch or tag {branch}"
    return out


def behind(project: str, base_sha: str | None) -> int | None:
    """How many commits the project's base branch has gained (as last fetched) since a run started from base_sha."""
    path = mirror_dir(project)
    branch = settings_store.get_project(project)["monolith_branch"] or "HEAD"
    if not base_sha or not path.exists():
        return None
    return git_ops.count_commits(path, base_sha, branch)


def remove(project: str) -> None:
    shutil.rmtree(mirror_dir(project), ignore_errors=True)
