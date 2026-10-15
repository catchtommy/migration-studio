"""Thin wrapper over the git CLI. Every stage commits its output so it can be diffed and rolled back."""

import subprocess
from pathlib import Path

_AUTHOR = ["-c", "user.name=Migration Studio", "-c", "user.email=studio@localhost"]


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(["git", *_AUTHOR, *args], cwd=repo, capture_output=True, text=True, input=stdin)
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
                              text=True)
        if proc.returncode != 0:
            on = f" (branch {source_branch})" if start else ""
            raise GitError(f"git clone {source_repo}{on} failed: {proc.stderr.strip()}")
    else:
        path.mkdir(exist_ok=True)
        git(path, "init", "--quiet")
        (path / "README.md").write_text("# Migration workspace\n\nNo monolith repository configured.\n")
        git(path, "add", "-A")
        git(path, "commit", "--quiet", "-m", "Initialise migration workspace")
        base_sha = None  # a fresh empty repo has new commit ids; its content is always the same
    if base_sha and not has_commit(path, base_sha):
        raise GitError(f"The monolith repository no longer has commit {base_sha[:12]} this run started from")
    git(path, "checkout", "--quiet", "-B", branch, *([base_sha] if base_sha else []))
    return head(path)


def clone_mirror(url: str, path: Path) -> None:
    """A bare mirror of a repository: every branch and tag, updated with fetch_mirror."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")  # a failed or interrupted clone never looks like a mirror
    subprocess.run(["rm", "-rf", str(tmp)], check=False)
    proc = subprocess.run(["git", "clone", "--quiet", "--mirror", "--", url, str(tmp)], capture_output=True, text=True)
    if proc.returncode != 0:
        subprocess.run(["rm", "-rf", str(tmp)], check=False)
        raise GitError(f"git clone {url} failed: {proc.stderr.strip()}")
    git(tmp, "config", "gc.auto", "0")  # never drop commits runs started from, even if upstream rewrites history
    tmp.rename(path)


def fetch_mirror(path: Path, url: str) -> None:
    """Bring the mirror up to date with the repository (at `url`, which may have changed since it was cloned)."""
    if git(path, "config", "--get", "remote.origin.url") != url:
        git(path, "remote", "set-url", "origin", url)
    git(path, "fetch", "--quiet", "--prune", "--tags", "origin")


def commit_info(repo: Path, ref: str) -> dict | None:
    """The commit `ref` points at: sha, subject, author and date (None when there's no such ref)."""
    proc = subprocess.run(["git", "log", "-1", "--format=%H%x00%s%x00%an%x00%cI", ref, "--"], cwd=repo,
                          capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    sha, subject, author, date = proc.stdout.strip().split("\x00")
    return {"sha": sha, "subject": subject, "author": author, "date": date}


def count_commits(repo: Path, since: str, until: str) -> int | None:
    proc = subprocess.run(["git", "rev-list", "--count", f"{since}..{until}"], cwd=repo, capture_output=True, text=True)
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
