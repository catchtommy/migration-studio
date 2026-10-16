"""Run workspaces are working copies; the database holds what they contain.

Each completed stage stores its git diff (StageExecution.diff) and its documents (Artifact rows, including reviewers'
edits). When a workspace is missing (another server, a cleared disk), it is rebuilt: the monolith is cloned at the
commit the run started from, the diffs of the stages currently in effect are replayed (code), and the documents are
written from the database.
"""

import shutil
from pathlib import Path

from sqlalchemy import select

from app import projects, repos
from app.agents.base_agent import docs_dir
from app.db.models import Artifact, Run, StageExecution
from app.db.session import emit, session_scope
from app.tools import git_ops


def _effective(executions: list[StageExecution], stage_defs: list[dict],
               before_stage: str | None) -> list[StageExecution]:
    """The completed attempts whose changes are in the workspace now. A stage normally starts from a tree where it and
    every later stage are undone (rejections and retries reset to its start), so each attempt drops those first; a
    restart's re-run (`rerun`) updates the tree as it is, replacing only the stage's own earlier attempt."""
    position = {d["key"]: i for i, d in enumerate(stage_defs)}
    agents = {d["key"] for d in stage_defs if d["kind"] == "agent"}
    chain: list[StageExecution] = []
    for ex in sorted(executions, key=lambda e: e.id):
        if ex.stage not in agents:
            continue
        if ex.rerun:  # a restart updates the stage in place: later stages' changes stay until they re-run
            if ex.status == "completed":  # a failed one was reset to its start, which kept the earlier attempt
                chain = [c for c in chain if c.stage != ex.stage] + [ex]
            continue
        chain = [c for c in chain if position[c.stage] < position[ex.stage]]
        if ex.status == "completed":
            chain.append(ex)
    if before_stage is not None:
        chain = [c for c in chain if position[c.stage] < position[before_stage]]
    return chain


def rebuild(run_id: str, before_stage: str | None = None) -> str:
    """Recreate the run's workspace from the database, as it was after the stages in effect (only those before
    `before_stage`, when given). Returns the new HEAD."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, branch, base_sha = Path(run.workspace), run.branch, run.base_sha
        repository, started_by = (run.repository if run.repository is not None else
                                  repos.project_repository(run.project)) or None, run.started_by
        docs = docs_dir(run.domain, run.operation)
        executions = s.scalars(select(StageExecution).where(StageExecution.run_id == run_id)).all()
        effective = _effective(executions, run.stage_defs, before_stage)
        chain = [(ex.stage, ex.attempt, ex.diff) for ex in effective]
        nested_diffs = [(ex.stage, ex.attempt, ex.repos or {}) for ex in effective]
        nested = [dict(r) for r in run.repositories or []]
        project = run.project
        artifacts = s.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id)).all()
        documents = {}
        for stage, attempt, _ in chain:  # later stages win when they rewrote a document
            documents.update({a.path: a.content for a in artifacts if a.stage == stage and a.attempt == attempt})

    shutil.rmtree(ws, ignore_errors=True)
    git_ops.init_workspace(ws, repos.ensure(projects.current(), repository, started_by), branch, base_sha)
    missing = []
    for stage, attempt, diff in chain:
        if diff is None:
            missing.append(stage)  # completed before diffs were stored; only its documents can be restored
            continue
        try:
            git_ops.apply(ws, diff, exclude=f"{docs}/*")
        except git_ops.GitError as e:
            raise git_ops.GitError(f"Could not replay {stage} (attempt {attempt}) onto the workspace: {e}") from e
    for path, content in documents.items():
        f = ws / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    sha = git_ops.commit_all(ws, f"[restore] {len(chain)} stage(s) from the database")
    exclude_nested(ws)
    for repo in nested:  # the run's other repositories: cloned at their starting commit, code changes replayed
        if not repo.get("base_sha"):
            continue
        path = ws / repo["path"]
        git_ops.init_workspace(path, repos.ensure(project, repo["url"], started_by), branch, repo["base_sha"])
        for stage, attempt, changes in nested_diffs:
            diff = (changes.get(repo["key"]) or {}).get("diff")
            if diff:
                try:
                    git_ops.apply(path, diff)
                except git_ops.GitError as e:
                    raise git_ops.GitError(f"Could not replay {stage} (attempt {attempt}) onto {repo['key']}: {e}") from e
        git_ops.commit_all(path, f"[restore] {repo['key']} from the database")
    note = f"; code changes of {', '.join(missing)} were not stored and are missing" if missing else ""
    emit(run_id, "log", f"Workspace rebuilt from the database: {len(chain)} stage(s), "
                        f"{len(documents)} document(s){note}")
    return sha


def exclude_nested(ws: Path) -> None:
    """Keep the run's other repositories (checked out inside the workspace) out of the root repository."""
    exclude = ws / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    if "/.studio/" not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "/.studio/\n")


def nested_heads(ws: Path, nested: list[dict]) -> dict[str, str]:
    """The current commit of each of the run's other repositories that is checked out."""
    return {r["key"]: git_ops.head(ws / r["path"]) for r in nested if (ws / r["path"] / ".git").exists()}


def ensure(run_id: str) -> Path:
    """The run's workspace, rebuilt from the database first if it (or one of its repositories) is missing."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, nested = Path(run.workspace), [r for r in run.repositories or [] if r.get("base_sha")]
    if not (ws / ".git").exists() or any(not (ws / r["path"] / ".git").exists() for r in nested):
        rebuild(run_id)
    return ws


def _reset_all(run_id: str, ws: Path, sha: str | None, nested_starts: dict | None) -> bool:
    """Reset the workspace root and the run's other repositories to the given commits, if they all still exist."""
    with session_scope() as s:
        nested = [r for r in s.get(Run, run_id).repositories or [] if r.get("base_sha")]
    starts = {k: v.get("start") for k, v in (nested_starts or {}).items()}
    targets = [(ws, sha)] + [(ws / r["path"], starts.get(r["key"]) or r["base_sha"]) for r in nested]
    if not all(git_ops.has_commit(path, target) for path, target in targets):
        return False
    for path, target in targets:
        git_ops.reset_hard(path, target)
    return True


def _start_of(run_id: str, stage: str) -> StageExecution | None:
    with session_scope() as s:
        ex = s.scalar(select(StageExecution).where(StageExecution.run_id == run_id, StageExecution.stage == stage,
                                                   StageExecution.rerun.is_not(True))
                      .order_by(StageExecution.id.desc()).limit(1))
        if ex is not None:
            s.expunge(ex)
        return ex


def roll_back(run_id: str, ws: Path, sha: str, stage: str) -> None:
    """Reset the workspace (and the run's other repositories) to where `stage` started: to those commits when the
    checkouts still have them, otherwise by rebuilding from the database without that stage and the ones after it."""
    start = _start_of(run_id, stage)
    if not _reset_all(run_id, ws, sha, start.repos if start else None):
        rebuild(run_id, before_stage=stage)


def start_fresh(run_id: str, stage: str) -> None:
    """Roll the workspace back to where `stage` started, undoing it and every later stage (a fresh restart). The git
    commit of its last ordinary start is used when nothing earlier changed since; otherwise (earlier stages were
    re-run in place afterwards, or the commit is gone) the workspace is rebuilt from the database without them."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws = Path(run.workspace)
        order = [d["key"] for d in run.stage_defs]
        executions = s.scalars(select(StageExecution).where(StageExecution.run_id == run_id)
                               .order_by(StageExecution.id)).all()
        start = next((ex for ex in reversed(executions) if ex.stage == stage and not ex.rerun), None)
        earlier_since = start is None or any(
            ex.id > start.id and ex.status == "completed" and ex.stage in order
            and order.index(ex.stage) < order.index(stage) for ex in executions)
        sha, starts = (start.start_sha, start.repos) if start else (None, None)
    if not earlier_since and sha and _reset_all(run_id, ws, sha, starts):
        emit(run_id, "log", "Workspace rolled back to where this step last started fresh", stage)
    else:
        rebuild(run_id, before_stage=stage)
