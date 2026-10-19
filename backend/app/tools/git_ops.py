"""Thin wrapper over the git CLI. Every stage commits its output so it can be diffed and rolled back."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

_AUTHOR = ["-c", "user.name=Migration Studio", "-c", "user.email=studio@localhost"]


def _allow_long_paths() -> None:
    """On Windows, let git create files whose full path is longer than 260 characters (deep Java packages under a
    run's workspace), which otherwise fail with "Filename too long". Set through the environment so it applies to
    every git command the studio and its agents run, clones included."""
    if os.name != "nt":
        return
    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    os.environ[f"GIT_CONFIG_KEY_{n}"], os.environ[f"GIT_CONFIG_VALUE_{n}"] = "core.longpaths", "true"
    os.environ["GIT_CONFIG_COUNT"] = str(n + 1)


_allow_long_paths()


class GitError(RuntimeError):
    pass


_BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$")  # a plain ref name, never an option


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including read-only files and folders (git's object files, which Windows won't delete
    as they are, and the shared legacy copies, made read-only on purpose)."""
    def retry_writable(func, p, _exc):
        os.chmod(os.path.dirname(p), stat.S_IRWXU)  # deleting needs a writable folder (off Windows)
        if os.path.lexists(p) and not os.path.islink(p):
            os.chmod(p, stat.S_IRWXU if os.path.isdir(p) else stat.S_IREAD | stat.S_IWRITE)
        func(p)

    if Path(path).exists():
        shutil.rmtree(path, onexc=retry_writable)


def hash_bytes(repo: Path, data: bytes) -> str:
    """Store content as a blob in the repository; returns its id."""
    proc = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=data, capture_output=True)
    if proc.returncode != 0:
        raise GitError(f"git hash-object failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode().strip()


def valid_branch(name: str) -> bool:
    """A branch or tag name safe to pass to git: no leading dash, spaces, '..', '//', or trailing '/' or '.lock'."""
    return (isinstance(name, str) and bool(_BRANCH.match(name)) and ".." not in name and "//" not in name
            and not name.endswith(("/", ".lock", ".")))


def read_blob(repo: Path, rev: str, path: str) -> bytes | None:
    """A file's bytes at a commit (None when it doesn't exist there)."""
    proc = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=repo, capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def merge_file(ours: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str]) -> tuple[bytes, int]:
    """Three-way merge of one file's contents: (result, number of conflicts; the result then has conflict markers)."""
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, content in (("ours", ours), ("base", base), ("theirs", theirs)):
            f = Path(tmp) / name
            f.write_bytes(content)
            files.append(str(f))
        proc = subprocess.run(["git", "merge-file", "-p", "-L", labels[0], "-L", labels[1], "-L", labels[2], *files],
                              capture_output=True)
    if proc.returncode < 0 or proc.returncode > 127:
        raise GitError(f"git merge-file failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout, proc.returncode


def git(repo: Path, *args: str, stdin: str | None = None, env: dict | None = None) -> str:
    """Run git in `repo`. `env` adds environment variables (e.g. a user's credentials, see app/git_auth.py)."""
    proc = subprocess.run(["git", *_AUTHOR, *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin,
                          env={**os.environ, **env} if env else None)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def init_workspace(path: Path, source_repo: str | None, branch: str, base_sha: str | None = None,
                   source_branch: str | None = None) -> str:
    """Clone the monolith (or start an empty repo when none is configured) and check out the run branch: from
    `source_branch` (the repository's default branch when None), or at `base_sha` when rebuilding an existing run.
    Returns the commit the branch starts from."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if source_repo:
        start = ["--branch", source_branch] if source_branch and not base_sha else []
        proc = subprocess.run(["git", "clone", "--quiet", *start, "--", source_repo, str(path)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            on = f" (branch {source_branch})" if start else ""
            raise GitError(f"git clone {source_repo}{on} failed: {proc.stderr.strip()}")
    else:
        path.mkdir(exist_ok=True)
        git(path, "init", "--quiet")
        (path / "README.md").write_text("# Migration workspace\n\nNo monolith repository configured.\n", encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "--quiet", "-m", "Initialise migration workspace")
        base_sha = None  # a fresh empty repo has new commit ids; its content is always the same
    if base_sha and not has_commit(path, base_sha):
        raise GitError(f"The monolith repository no longer has commit {base_sha[:12]} this run started from")
    git(path, "checkout", "--quiet", "-B", branch, *([base_sha] if base_sha else []))
    return head(path)


def clone_mirror(url: str, path: Path, env: dict | None = None) -> None:
    """A bare mirror of a repository: every branch and tag, updated with fetch_mirror."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")  # a failed or interrupted clone never looks like a mirror
    remove_tree(tmp)
    proc = subprocess.run(["git", "clone", "--quiet", "--mirror", "--", url, str(tmp)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, **env} if env else None)
    if proc.returncode != 0:
        remove_tree(tmp)
        raise GitError(f"git clone {url} failed: {proc.stderr.strip()}")
    git(tmp, "config", "gc.auto", "0")  # never drop commits runs started from, even if upstream rewrites history
    tmp.rename(path)


def fetch_mirror(path: Path, url: str, env: dict | None = None) -> None:
    """Bring the mirror up to date with the repository (at `url`, which may have changed since it was cloned)."""
    if git(path, "config", "--get", "remote.origin.url") != url:
        git(path, "remote", "set-url", "origin", url)
    git(path, "fetch", "--quiet", "--prune", "--tags", "origin", env=env)


def commit_info(repo: Path, ref: str) -> dict | None:
    """The commit `ref` points at: sha, subject, author and date (None when there's no such ref)."""
    proc = subprocess.run(["git", "log", "-1", "--format=%H%x00%s%x00%an%x00%cI", ref, "--"], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    sha, subject, author, date = proc.stdout.strip().split("\x00")
    return {"sha": sha, "subject": subject, "author": author, "date": date}


def count_commits(repo: Path, since: str, until: str) -> int | None:
    proc = subprocess.run(["git", "rev-list", "--count", f"{since}..{until}"], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return int(proc.stdout.strip()) if proc.returncode == 0 else None


def has_commit(repo: Path, sha: str | None) -> bool:
    if not sha or not (repo / ".git").exists():
        return False
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo, capture_output=True).returncode == 0


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain"):
        git(repo, "commit", "--quiet", "-m", message)
    return head(repo)


def reset_hard(repo: Path, sha: str) -> None:
    git(repo, "reset", "--quiet", "--hard", sha)
    git(repo, "clean", "-fdq")


def diff(repo: Path, base: str, target: str, binary: bool = False) -> str:
    return git(repo, "diff", *(["--binary"] if binary else []), f"{base}..{target}")


def changed_files(repo: Path, base: str, target: str) -> list[str]:
    """Files added or modified between two commits (not deleted ones), relative to the repo."""
    out = git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=AM", f"{base}..{target}")
    return [line for line in out.splitlines() if line]


def apply(repo: Path, patch: str, exclude: str | None = None) -> None:
    """Apply a diff made by `diff(..., binary=True)` to the working tree, skipping paths matching `exclude`."""
    if patch.strip():
        git(repo, "apply", "--whitespace=nowarn", *([f"--exclude={exclude}"] if exclude else []), "-",
            stdin=patch if patch.endswith("\n") else patch + "\n")
