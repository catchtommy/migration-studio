"""Publishing approved AS-IS documentation into the monolith, for later agents.

Steps marked "publish_docs" (by default AS-IS documentation and Deep dive) document the legacy code as it is. Once the
gate reviewing them approves, the run waits (awaiting_publish) for a person to publish those documents into the
monolith's repository, under <docs folder>/<domain>/<feature>/ on its base branch, or to skip. Later runs then find
reviewed documentation next to the code and read it before the code itself.

The legacy code stays read-only: nothing is written to the shared read-only copy (app/legacy.py) or to any checkout.
The commit is built inside the project's bare mirror (a temporary index, the documents' contents from the database)
and pushed as the user, never forced. The shared copy picks it up at the next fetch, like any change to the branch.
"""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from app import git_auth, repos, settings_store, workspace
from app.agents.base_agent import docs_dir
from app.config import settings
from app.db.models import Run, User
from app.db.session import emit, session_scope
from app.tools import git_ops

MAX_TEXT = 1_000_000


class PublishError(RuntimeError):
    pass


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def docs_folder(project: str) -> str:
    return (settings_store.get_project(project).get("monolith_docs_dir") or "docs").strip().strip("/")


def _publishing(run: Run, upto: str | None = None) -> list[str]:
    """The run's steps that publish their documents (only those up to `upto`, a gate or step key)."""
    stages = run.stage_defs
    keys = [d["key"] for d in stages]
    end = keys.index(upto) if upto in keys else len(keys) - 1
    return [d["key"] for d in stages[:end + 1] if d["kind"] == "agent" and d.get("publish_docs")]


def documents(run_id: str, upto: str | None = None) -> dict[str, str]:
    """What to publish: {repository path: content}, from the approved documents of the publishing steps."""
    from sqlalchemy import select

    from app.db.models import Artifact
    with session_scope() as s:
        run = s.get(Run, run_id)
        steps = set(_publishing(run, upto))
        if not steps:
            return {}
        own = docs_dir(run.domain, run.operation) + "/"
        target = f"{docs_folder(run.project)}/{run.domain}/{run.operation}/"
        paths = {a.path for a in s.scalars(select(Artifact).where(Artifact.run_id == run_id,
                                                                    Artifact.stage.in_(steps)))}
    current = workspace.effective_documents(run_id)  # the approved versions, with reviewers' edits
    return {target + path[len(own):]: current[path] for path in sorted(paths)
            if path.startswith(own) and path in current}


def _repository(run: Run) -> tuple[str, str | None]:
    url = repos.project_repository(run.project)
    if not url:
        raise PublishError("This project has no monolith repository")
    return url, settings_store.get_project(run.project).get("monolith_branch") or None


def publish_needed(run_id: str, gate: str) -> dict | None:
    """After a gate approved publishing steps: what to ask the user to publish, or None (no repository, no documents,
    or exactly what this run last published)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if not repos.project_repository(run.project) or run.scope not in (None, "operation"):
            return None
        last = ((run.delivery or {}).get("docs") or {}).get("files") or {}
        steps = _publishing(run, gate)
        titles = [d["title"] for d in run.stage_defs if d["key"] in steps]
    docs = documents(run_id, gate)
    if not docs or {p: _digest(c) for p, c in docs.items()} == last:
        return None
    return {"kind": "docs", "stage": gate, "steps": titles, "files": sorted(docs),
            "repository": repos.project_repository(_project(run_id))}


def _project(run_id: str) -> str:
    with session_scope() as s:
        return s.get(Run, run_id).project


def _sync(run_id: str, user_id: int | None) -> tuple[Path, str, str, str]:
    """Fetch the mirror (as the user); returns (mirror, url, base branch, its latest commit)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        url, branch = _repository(run)
        project = run.project
    try:
        mirror = repos.fetch(project, url, user_id)
    except git_ops.GitError as e:
        raise PublishError(f"Could not fetch {url}: {git_auth.scrub(str(e))}")
    branch = branch or repos.default_branch(mirror) or "main"
    info = git_ops.commit_info(mirror, f"refs/heads/{branch}")
    if info is None:
        raise PublishError(f"{url} has no branch {branch}")
    return mirror, url, branch, info["sha"]


def preview(run_id: str, user_id: int | None) -> dict:
    """The files to publish against the base branch now: new, modified or unchanged, the old and new contents, and
    whether someone changed a file in git since this run last published it."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        gate = run.current_stage if run.status == "awaiting_publish" else None
        last = dict(((run.delivery or {}).get("docs") or {}).get("files") or {})
        domain, feature = run.domain, run.operation
    docs = documents(run_id, gate)
    mirror, url, branch, latest = _sync(run_id, user_id)
    files = []
    for path, content in docs.items():
        old_bytes = git_ops.read_blob(mirror, latest, path)
        old = old_bytes.decode("utf-8", errors="replace") if old_bytes is not None else None
        status = "new" if old is None else "unchanged" if old == content else "modified"
        changed_upstream = old is not None and path in last and _digest(old) != last[path] and old != content
        big = len(content) > MAX_TEXT or (old is not None and len(old) > MAX_TEXT)
        files.append({"path": path, "status": status, "changed_upstream": changed_upstream,
                      "old": None if big else old, "new": None if big else content})
    return {"repository": url, "branch": branch, "latest_sha": latest, "folder": f"{docs_folder(_project(run_id))}/"
            f"{domain}/{feature}/", "message": f"Document {domain} {feature} (AS-IS)\n\nApproved in Migration Studio.",
            "files": files}


def publish(run_id: str, user_id: int, message: str, paths: list[str], latest_sha: str) -> dict:
    """Commit the chosen documents on top of the base branch as previewed, as the user, and push it (never forced)."""
    who = git_auth.identity(user_id)
    if who is None:
        raise PublishError("Set your git name and email first (My settings → Git)")
    if not message.strip():
        raise PublishError("Write a commit message")
    with session_scope() as s:
        run = s.get(Run, run_id)
        gate = run.current_stage if run.status == "awaiting_publish" else None
    docs = documents(run_id, gate)
    unknown = [p for p in paths if p not in docs]
    if unknown or not paths:
        raise PublishError(f"{unknown[0]} isn't one of this run's documents to publish" if unknown else
                           "Choose at least one document to publish")
    mirror, url, branch, latest = _sync(run_id, user_id)
    if latest != latest_sha:
        raise PublishError(f"{branch} has new commits since you reviewed the documents; reload and check again")

    index = settings.data_dir / f"publish-index-{run_id}"
    env = {"GIT_INDEX_FILE": str(index), "GIT_AUTHOR_NAME": who[0], "GIT_AUTHOR_EMAIL": who[1],
           "GIT_COMMITTER_NAME": who[0], "GIT_COMMITTER_EMAIL": who[1]}
    try:
        git_ops.git(mirror, "read-tree", latest, env=env)
        for path in paths:
            blob = git_ops.hash_bytes(mirror, docs[path].encode())
            git_ops.git(mirror, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git_ops.git(mirror, "write-tree", env=env)
    finally:
        if index.exists():
            os.remove(index)
    if tree == git_ops.git(mirror, "rev-parse", f"{latest}^{{tree}}"):
        raise PublishError(f"Nothing to publish: {branch} already has these documents")
    sha = git_ops.git(mirror, "commit-tree", tree, "-p", latest, stdin=message.strip() + "\n", env=env)
    try:
        git_ops.git(mirror, "push", "--quiet", "--", url, f"{sha}:refs/heads/{branch}",
                    env=git_auth.env_for(user_id, url))
    except git_ops.GitError as e:
        raise PublishError(f"Pushing to {branch} of {url} failed: {git_auth.scrub(str(e))}")
    try:
        repos.fetch(_project(run_id), url, user_id)
    except git_ops.GitError:
        pass

    with session_scope() as s:
        run = s.get(Run, run_id)
        user = s.get(User, user_id)
        record = {"sha": sha, "branch": branch, "repository": url, "message": message.strip(),
                  "files": {p: _digest(docs[p]) for p in docs},  # all of them: unchanged ones count as published
                  "published": sorted(paths), "by": user.display_name, "at": datetime.now(UTC).isoformat()}
        run.delivery = {**(run.delivery or {}), "docs": record}
    emit(run_id, "status", f"{record['by']} published {len(paths)} AS-IS document(s) to {branch} of {url} as "
                           f"{sha[:10]}", gate)
    return record


def skipped(run_id: str, user_id: int) -> None:
    with session_scope() as s:
        name = s.get(User, user_id).display_name
        gate = s.get(Run, run_id).current_stage
    emit(run_id, "status", f"{name} didn't publish the AS-IS documents this time", gate)
