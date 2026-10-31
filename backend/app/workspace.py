"""Run workspaces are working copies; the database holds what they contain.

Each completed stage stores its git diffs (StageExecution.diff, and .repos for the run's other repositories) and its
documents (Artifact rows, including reviewers' edits). Documents are not kept on disk: a step gets the documents in
effect written to `.migration/` when it starts (materialize_documents), and the folder is cleared again when it ends
(its new or changed documents are stored first). A finished run's workspace is deleted (release_if_completed).

When a workspace is missing, it is rebuilt: each repository is checked out where the run left it and the code changes
of the stages in effect are replayed. A repository whose changes were committed and pushed is checked out from its
feature branch (with anything else pushed there since), and only later changes are replayed on it.
"""

import time
from pathlib import Path

from sqlalchemy import func, or_, select

from app import repos
from app.agents.base_agent import DOCS_ROOT
from app.db.models import Artifact, Run, StageExecution, utcnow
from app.db.session import emit, session_scope
from app.tools import git_ops


def _effective(executions: list[StageExecution], stage_defs: list[dict],
               before_stage: str | None) -> list[StageExecution]:
    """The completed attempts whose changes are in the workspace now, in the order to replay them. A stage normally
    starts from a tree where it and every later stage are undone (rejections and retries reset to its start), so each
    attempt drops those first. An attempt in place (`rerun`: a restart that updates the documents, or a reviewer's
    amendment) changed the tree as it was, its earlier attempt included: its diff is only what it changed, so it is
    replayed on top of that attempt, not instead of it."""
    position = {d["key"]: i for i, d in enumerate(stage_defs)}
    agents = {d["key"] for d in stage_defs if d["kind"] == "agent"}
    chain: list[StageExecution] = []
    for ex in sorted(executions, key=lambda e: (e.started_at, e.id)):  # as they happened (not ids alone: Oracle)
        if ex.stage not in agents:
            continue
        if ex.rerun:  # in place: the stage's earlier attempt and later stages' changes stay until they re-run
            if ex.status == "completed":  # a failed one was reset to its start, which kept the earlier attempt
                chain.append(ex)
            continue
        chain = [c for c in chain if position[c.stage] < position[ex.stage]]
        if ex.status == "completed":
            chain.append(ex)
    if before_stage is not None:
        chain = [c for c in chain if position[c.stage] < position[before_stage]]
    return chain


def _chain(s, run: Run, before_stage: str | None) -> list[StageExecution]:
    executions = s.scalars(select(StageExecution).where(StageExecution.run_id == run.id)).all()
    return _effective(executions, run.stage_defs, before_stage)


def effective_documents(run_id: str, before_stage: str | None = None) -> dict[str, str]:
    """The run's documents in effect (path: content): the latest version each stage in effect wrote, later stages
    winning, with reviewers' edits."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        chain = [(ex.stage, ex.attempt) for ex in _chain(s, run, before_stage)]
        artifacts = s.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id)).all()
        documents: dict[str, str] = {}
        for stage, attempt in chain:
            documents.update({a.path: a.content for a in artifacts if a.stage == stage and a.attempt == attempt})
        return documents


def materialize_documents(run_id: str, ws: Path) -> int:
    """Write the documents in effect into the workspace for a step (they live in the database)."""
    documents = effective_documents(run_id)
    for path, content in documents.items():
        f = ws / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return len(documents)


def snapshot_documents(ws: Path) -> dict[str, str]:
    """The documents folder's files and their content hashes, to see what a step changed."""
    import hashlib
    root = ws / DOCS_ROOT
    if not root.exists():
        return {}
    return {f.relative_to(ws).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in root.rglob("*") if f.is_file()}


def clear_documents(ws: Path) -> None:
    git_ops.remove_tree(ws / DOCS_ROOT)


def untrack_documents(ws: Path) -> None:
    """Workspaces from before documents left the disk tracked them in git: stop tracking (once)."""
    if git_ops.git(ws, "ls-files", "--", DOCS_ROOT):
        git_ops.git(ws, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", DOCS_ROOT)
        git_ops.git(ws, "commit", "--quiet", "-m", "[studio] documents are kept in the database")


def _restore(path: Path, project: str, url: str | None, branch: str, base_sha: str | None, pushed: dict | None,
             diffs: list[tuple[int, str, int, str | None]], user_id: int | None, exclude: str | None,
             log) -> str | None:
    """Check out one repository of the run and replay its code changes. A pushed repository starts from its feature
    branch on the remote (the commit pushed, or what is there now); only changes after that push are replayed.
    Returns the commit it started from when that is the feature branch."""
    restored = None
    known = pushed and ((pushed.get("status") == "pushed" and pushed.get("sha")) or pushed.get("restored_sha"))
    if url and known:
        try:
            mirror = repos.fetch(project, url, user_id)
        except git_ops.GitError:
            mirror = Path(repos.ensure(project, url, user_id))
        remote = git_ops.commit_info(mirror, f"refs/heads/{pushed.get('branch') or branch}")
        restored = remote["sha"] if remote else (pushed.get("sha") or pushed["restored_sha"])
        git_ops.init_workspace(path, str(mirror), branch, restored)
        log(f"Checked out {url} from its feature branch {pushed.get('branch') or branch} at {restored[:10]}")
        diffs = [d for d in diffs if d[0] > (pushed.get("exec_id") or 0)]
    else:
        git_ops.init_workspace(path, repos.ensure(project, url, user_id) if url else None, branch, base_sha)
    for _id, stage, attempt, diff in diffs:
        if not diff:
            continue
        try:
            git_ops.apply(path, diff, exclude=exclude)
        except git_ops.GitError as e:
            raise git_ops.GitError(f"Could not replay {stage} (attempt {attempt}) onto {path.name}: {e}") from e
    git_ops.commit_all(path, "[restore] code changes from the database")
    return restored


def rebuild(run_id: str, before_stage: str | None = None, branch: str | None = None,
            bases: dict[str, str] | None = None) -> str:
    """Recreate the run's workspace from the database, as it was after the stages in effect (only those before
    `before_stage`, when given: a roll-back, which replays from where the run started rather than from code it pushed,
    as that may hold the stages rolled back). `branch` and `bases` (repository key -> commit; "monolith" for the
    root) build it on another branch from other base commits instead of the run's (recut). Returns the new HEAD of the
    workspace root."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, branch, base_sha = Path(run.workspace), branch or run.branch, (bases or {}).get("monolith", run.base_sha)
        repository, started_by = (run.repository if run.repository is not None else
                                  repos.project_repository(run.project)) or None, run.started_by
        effective = _chain(s, run, before_stage)
        root_diffs = [(ex.id, ex.stage, ex.attempt, ex.diff) for ex in effective]
        nested_changes = [(ex.id, ex.stage, ex.attempt, ex.repos or {}) for ex in effective]
        missing = [ex.stage for ex in effective if ex.diff is None and repository]
        nested = [{**r, "base_sha": (bases or {}).get(r["key"], r.get("base_sha"))} if r.get("base_sha") else dict(r)
                  for r in run.repositories or []]
        project, pushed = run.project, dict((run.delivery or {}).get("repos") or {})
    if before_stage is not None:
        pushed = {}

    log = lambda msg: emit(run_id, "log", msg)  # noqa: E731
    git_ops.remove_tree(ws)
    restored = {}
    sha_root = _restore(ws, project, repository, branch, base_sha, pushed.get("monolith") if base_sha else None,
                        root_diffs, started_by, f"{DOCS_ROOT}/*", log)
    if sha_root:
        restored["monolith"] = sha_root
    sha = git_ops.head(ws)
    exclude_nested(ws)
    for repo in nested:
        if not repo.get("base_sha"):
            continue
        diffs = [(i, st, at, (changes.get(repo["key"]) or {}).get("diff")) for i, st, at, changes in nested_changes]
        got = _restore(ws / repo["path"], project, repo["url"], branch, repo["base_sha"], pushed.get(repo["key"]),
                       diffs, started_by, None, log)
        if got:
            restored[repo["key"]] = got
    if restored:  # later commits build on the feature branch as it is now; only new changes count as uncommitted
        with session_scope() as s:
            run = s.get(Run, run_id)
            delivery = dict(run.delivery or {})
            done = dict(delivery.get("repos") or {})
            for key, start in restored.items():
                done[key] = {**done.get(key, {}), "restored_sha": start, "head_sha": start}
            run.delivery = {**delivery, "repos": done}
    note = f"; code changes of {', '.join(missing)} were not stored and are missing" if missing else ""
    emit(run_id, "log", f"Workspace rebuilt from the database: {len(effective)} stage(s){note}")
    return sha


def recut(run_id: str, stage: str, branch: str, user_id: int | None) -> None:
    """A fresh re-run on a new feature branch: every repository the run checked out is cut again as `branch` from the
    latest of its base branch, and the code of the steps before `stage` is replayed onto it (a replay that no longer
    applies stops the re-run, leaving the run on its old branch). The old branch stays in git as it is; nothing pushed
    to it is carried over, and the run's next commits go to the new one."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, project, old = Path(run.workspace), run.project, run.branch
        root_url = (run.repository if run.repository is not None else repos.project_repository(run.project)) or None
        targets = ([("monolith", root_url, run.base_branch)] if root_url and run.base_sha else []) + [
            (r["key"], r["url"], r.get("base_branch")) for r in run.repositories or [] if r.get("base_sha")]
    _set_failed_cut(run_id, None)
    bases, base_branches = {}, {}
    for key, url, base_branch in targets:
        mirror = Path(repos.fetch(project, url, user_id))
        base_branch = base_branch or repos.default_branch(mirror)
        if git_ops.commit_info(mirror, f"refs/heads/{branch}") is not None:
            raise git_ops.GitError(f"{url} already has a branch {branch}: choose another name for the new branch")
        bases[key] = git_ops.git(mirror, "rev-parse", f"{base_branch}^{{commit}}")
        base_branches[key] = base_branch
    try:
        rebuild(run_id, before_stage=stage, branch=branch, bases=bases)
    except Exception as e:
        git_ops.remove_tree(ws)  # rebuilt on the old branch when next needed (ensure)
        _set_failed_cut(run_id, {"stage": stage, "branch": branch})  # Retry cuts it again
        raise git_ops.GitError(f"Could not cut {branch} from the latest base branch: {e}. Fix that on the base branch "
                               f"and retry (it cuts the branch again), or re-run from here on {old}") from e
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = dict(run.delivery or {})
        cut_at = s.scalar(select(func.max(StageExecution.id)).where(StageExecution.run_id == run_id)) or 0
        previous = [*delivery.get("previous_branches", []),
                    {"branch": old, "repos": delivery.get("repos") or {}, "at": utcnow().isoformat(),
                     "exec_id": cut_at}]  # steps started up to here ran on the old branch
        run.delivery = {**delivery, "repos": {}, "previous_branches": previous}
        run.branch = branch
        if "monolith" in bases:
            run.base_sha = bases["monolith"]
        run.repositories = [{**r, "base_sha": bases[r["key"]]} if r["key"] in bases else r
                            for r in run.repositories or []]
    emit(run_id, "status", f"Cut a new feature branch {branch} from the latest of "
                           f"{', '.join(sorted(set(base_branches.values()))) or 'the base branch'} for the fresh re-run; "
                           f"{old} stays in git as it is", stage)


def _set_failed_cut(run_id: str, cut: dict | None) -> None:
    """A cut onto a new branch that failed (its stage and branch), for Retry to cut it again; None clears it."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        delivery = {k: v for k, v in (run.delivery or {}).items() if k != "failed_cut"}
        run.delivery = {**delivery, **({"failed_cut": cut} if cut else {})}


def monolith_pending(run: Run) -> bool:
    """Whether the run reads the monolith from the shared read-only copy, so a step that changes its code needs the run's
    own checkout first (check_out_monolith)."""
    return bool(run.legacy and run.legacy.get("url")) and not run.repository and not run.base_sha


def check_out_monolith(run_id: str, branch: str, base_branch: str | None, user_id: int | None, log) -> None:
    """Give a run that has read the monolith from the shared read-only copy (app/legacy.py) its own checkout of it,
    just before its first step that changes the monolith's code: the workspace root becomes a clone of the monolith on
    the run's feature branch. It is cut from the commit the run has read so far when that is still the base branch,
    so the code matches what earlier steps analysed, else from the base branch's latest commit. Anything earlier steps
    wrote at the workspace root is replayed on it; the run's other repositories are moved over as they are."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, project, read = Path(run.workspace), run.project, dict(run.legacy)
        root_diffs = [(ex.id, ex.stage, ex.attempt, ex.diff) for ex in _chain(s, run, None)]
    mirror = Path(repos.latest_for_run(project, read["url"], user_id, log))
    base_branch = base_branch or read.get("branch") or repos.default_branch(mirror)
    same = base_branch == (read.get("branch") or repos.default_branch(mirror))
    base_sha = read["sha"] if same else git_ops.git(mirror, "rev-parse", f"{base_branch}^{{commit}}")
    if branch != base_branch and git_ops.commit_info(mirror, f"refs/heads/{branch}") is not None:
        # The feature branch already exists in the monolith: the run continues on it, from its latest commit.
        base_sha = git_ops.git(mirror, "rev-parse", f"refs/heads/{branch}^{{commit}}")
        log(f"The monolith already has branch {branch}: the run continues on it, at {base_sha[:10]}")

    tmp = ws.with_name(ws.name + ".monolith")  # the workspace keeps working until the checkout is complete
    git_ops.remove_tree(tmp)
    try:
        _restore(tmp, project, read["url"], branch, base_sha, None, root_diffs, user_id, f"{DOCS_ROOT}/*", log)
        nested = ws / ".studio"
        if nested.exists():
            git_ops.rename_with_retry(nested, tmp / ".studio")
        exclude_nested(tmp)
    except Exception:
        git_ops.remove_tree(tmp)
        raise
    _swap_in(tmp, ws, log)
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.repository, run.base_branch, run.base_sha, run.legacy = read["url"], base_branch, base_sha, None
    log(f"Checked out the monolith for the steps that change its code: branch {branch} cut from {base_branch} at "
        f"{base_sha[:10]}")


LEFTOVER = ".old-"  # a workspace moved aside by _swap_in, deleted once nothing holds it any more


def _swap_in(new: Path, ws: Path, log) -> None:
    """Put `new` in place of the workspace `ws`. The old one is moved aside first and deleted after, best effort:
    on Windows a folder can't be deleted while a program still holds a file in it (an Explorer or editor window,
    antivirus or Windows Search scanning it: WinError 145), and that must not stop the step. What is left is deleted
    by cleanup_leftovers, at the next start."""
    old = ws.with_name(f"{ws.name}{LEFTOVER}{time.strftime('%Y%m%d%H%M%S')}") if ws.exists() else None
    if old is not None:
        git_ops.rename_with_retry(ws, old)
    git_ops.rename_with_retry(new, ws)
    if old is not None:
        try:
            git_ops.remove_tree(old)
        except OSError as e:
            log(f"Left {old.name} next to the workspace, to delete later: {e}")


def cleanup_leftovers(root: Path) -> int:
    """Delete workspaces moved aside earlier (_swap_in) and unfinished checkouts, best effort: those still held stay
    for the next time. Returns how many were deleted."""
    if not root.is_dir():
        return 0
    done = 0
    for path in root.iterdir():
        if path.is_dir() and (LEFTOVER in path.name or path.name.endswith(".monolith")):
            try:
                git_ops.remove_tree(path)
                done += 1
            except OSError:
                pass
    return done


def release_workspace(run_id: str) -> None:
    """Delete a run's workspace (it is being completed)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws = Path(run.workspace) if run and run.workspace else None
    if ws is not None and ws.exists():
        git_ops.remove_tree(ws)
        emit(run_id, "log", "Workspace removed from the server now the run is complete (everything is in the "
                            "database or pushed)")


def release_if_completed(run_id: str) -> bool:
    """A finished run's workspace is deleted: its documents and diffs are in the database and its commits pushed;
    re-running a step checks it out again (from the feature branches it pushed)."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None or run.status != "completed" or not run.workspace:
            return False
        ws = Path(run.workspace)
    if ws.exists():
        git_ops.remove_tree(ws)
        emit(run_id, "log", "Workspace removed from the server now the run is complete (everything is in the "
                            "database or pushed)")
    return True


def release_completed() -> int:
    """Delete the workspaces of every completed run (e.g. at startup, for runs finished before this was done)."""
    with session_scope() as s:
        # Not runs with stage changes the database doesn't have (from before diffs were stored): only their
        # workspace has those.
        unstored = set(s.scalars(select(StageExecution.run_id).where(
            StageExecution.status == "completed", StageExecution.diff.is_(None),
            StageExecution.start_sha.is_not(None), StageExecution.start_sha != StageExecution.end_sha)))
        done = [(r.id, r.workspace) for r in s.scalars(select(Run).where(Run.status == "completed"))
                if r.workspace and r.id not in unstored]
    count = 0
    for _run_id, path in done:
        if Path(path).exists():
            git_ops.remove_tree(Path(path))
            count += 1
    return count


def exclude_nested(ws: Path) -> None:
    """Keep the run's other repositories (checked out inside the workspace) out of the root repository."""
    exclude = ws / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    missing = [line for line in ("/.studio/", f"/{DOCS_ROOT}/") if line not in text.splitlines()]
    if missing:  # also the studio's documents, which live in the database
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "\n".join(missing) + "\n", encoding="utf-8")


def nested_heads(ws: Path, nested: list[dict]) -> dict[str, str]:
    """The current commit of each of the run's other repositories that is checked out."""
    return {r["key"]: git_ops.head(ws / r["path"]) for r in nested if (ws / r["path"] / ".git").exists()}


def ensure(run_id: str) -> Path:
    """The run's workspace, rebuilt from the database first if it (or one of its repositories) is missing."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws, nested = Path(run.workspace), [r for r in run.repositories or [] if r.get("base_sha")]
    # A checkout without a commit (an interrupted clone, or `git init` run in it) is as good as missing
    if not git_ops.has_commits(ws) or any(not git_ops.has_commits(ws / r["path"]) for r in nested):
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


def _not_true(column):
    """`column IS NOT TRUE`, written so Oracle (which has no boolean type: IS NOT 1 is invalid there) accepts it."""
    return or_(column.is_(None), column == False)  # noqa: E712


def _start_of(run_id: str, stage: str) -> StageExecution | None:
    with session_scope() as s:
        ex = s.scalar(select(StageExecution).where(StageExecution.run_id == run_id, StageExecution.stage == stage,
                                                   _not_true(StageExecution.rerun))
                      .order_by(StageExecution.started_at.desc(), StageExecution.id.desc()).limit(1))
        if ex is not None:
            s.expunge(ex)
        return ex


def roll_back(run_id: str, ws: Path, sha: str, stage: str) -> None:
    """Reset the workspace (and the run's other repositories) to where `stage` started: to those commits when the
    checkouts still have them, otherwise by rebuilding from the database without that stage and the ones after it."""
    start = _start_of(run_id, stage)
    if _before_cut(run_id, start) or not _reset_all(run_id, ws, sha, start.repos if start else None):
        rebuild(run_id, before_stage=stage)
    _pushed_code_rolled_back(run_id)


def start_fresh(run_id: str, stage: str) -> None:
    """Roll the workspace back to where `stage` started, undoing it and every later stage (a fresh restart). The git
    commit of its last ordinary start is used when nothing earlier changed since; otherwise (earlier stages were
    re-run in place afterwards, or the commit is gone) the workspace is rebuilt from the database without them."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        ws = Path(run.workspace)
        order = [d["key"] for d in run.stage_defs]
        executions = s.scalars(select(StageExecution).where(StageExecution.run_id == run_id)
                               .order_by(StageExecution.started_at, StageExecution.id)).all()
        start = next((ex for ex in reversed(executions) if ex.stage == stage and not ex.rerun), None)
        later = executions[executions.index(start) + 1:] if start is not None else []
        earlier_since = start is None or any(
            ex.status == "completed" and ex.stage in order
            and order.index(ex.stage) < order.index(stage) for ex in later)
        sha, starts = (start.start_sha, start.repos) if start else (None, None)
    if not earlier_since and sha and not _before_cut(run_id, start) and _reset_all(run_id, ws, sha, starts):
        emit(run_id, "log", "Workspace rolled back to where this step last started fresh", stage)
    else:
        rebuild(run_id, before_stage=stage)
    _pushed_code_rolled_back(run_id)


def _before_cut(run_id: str, start: StageExecution | None) -> bool:
    """Whether the run was cut onto a new branch (recut) after `start` began: its commits are on the old branch, so
    going back to them would leave the new one (a rebuild replays onto the new branch instead)."""
    with session_scope() as s:
        cuts = [c.get("exec_id") or 0 for c in (s.get(Run, run_id).delivery or {}).get("previous_branches") or []]
    return bool(cuts) and (start is None or start.id <= max(cuts))


def _pushed_code_rolled_back(run_id: str) -> None:
    """Code the run pushed may have been rolled back: the next commit undoes it in git, rather than the feature
    branch bringing it back before the next code step (app/delivery.py)."""
    from app import delivery
    delivery.mark_rolled_back(run_id)
