"""Thin wrapper over the git CLI. Every stage commits its output so it can be diffed and rolled back."""

import subprocess
from pathlib import Path

_AUTHOR = ["-c", "user.name=Migration Studio", "-c", "user.email=studio@localhost"]


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *_AUTHOR, *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def init_workspace(path: Path, source_repo: str | None, branch: str) -> None:
    """Clone the monolith (or start an empty repo when none is configured) and check out the run branch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if source_repo:
        proc = subprocess.run(["git", "clone", "--quiet", source_repo, str(path)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise GitError(f"git clone {source_repo} failed: {proc.stderr.strip()}")
    else:
        path.mkdir(exist_ok=True)
        git(path, "init", "--quiet")
        (path / "README.md").write_text("# Migration workspace\n\nNo monolith repository configured.\n")
        git(path, "add", "-A")
        git(path, "commit", "--quiet", "-m", "Initialise migration workspace")
    git(path, "checkout", "--quiet", "-B", branch)


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


def diff(repo: Path, base: str, target: str) -> str:
    return git(repo, "diff", f"{base}..{target}")
