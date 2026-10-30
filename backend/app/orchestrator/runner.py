"""Drives each run's graph on a worker thread. One LangGraph thread (thread_id = run id) per run."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.types import Command

from sqlalchemy import delete

from app.db.models import Run, StageExecution
from app.db.session import emit, session_scope
from app import delivery, legacy, projects, repos
from app.orchestrator import questions
from app.orchestrator.checkpointer import SqlAlchemySaver
from app.orchestrator.graph import build_graph, node_before
from app.orchestrator.stages import Workflow
from app.tools import git_ops
from app.workspace import ensure as ensure_workspace
from app.workspace import exclude_nested, recut, release_workspace, start_fresh

log = logging.getLogger(__name__)



def _fetch_for_question(run_id: str, url: str) -> None:
    """A step asks for its branches in a repository the run hasn't checked out: bring the server's copy up to date
    first, so the question can say whether the feature branch already exists there. Best effort: confirming fetches
    again and reports a failure."""
    from app import repos
    with session_scope() as s:
        run = s.get(Run, run_id)
        project, user_id = run.project, run.started_by
    try:
        repos.fetch(project, url, user_id)
    except Exception as e:  # network, credentials: the question shows what the last fetch knew
        log.warning("Could not fetch %s for run %s: %s", url, run_id, e)

class RunBusyError(RuntimeError):
    pass


class Runner:
    # Generous: a run whose agent is waiting for an answer holds its worker thread.
    def __init__(self, max_workers: int = 16):
        self._saver = SqlAlchemySaver()  # checkpoints live in the studio database
        self._graphs: dict[str, object] = {}  # workflow definition (JSON) -> compiled graph
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="run")
        self._active: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def has_active(self) -> bool:
        with self._lock:
            return bool(self._active)

    def graph_for(self, run_id: str):
        """The compiled graph for the workflow snapshot this run started with."""
        with session_scope() as s:
            stages = s.get(Run, run_id).stage_defs
        key = json.dumps(stages, sort_keys=True)
        with self._lock:
            if key not in self._graphs:
                self._graphs[key] = build_graph(Workflow.from_dicts(stages), self._saver)
            return self._graphs[key]

    def pending_gate(self, run_id: str) -> dict | None:
        snapshot = self.graph_for(run_id).get_state(self._config(run_id))
        for task in snapshot.tasks:
            for intr in task.interrupts:
                return intr.value
        return None

    def start(self, run_id: str) -> None:
        self._submit(run_id, None, initial=True)

    def resume(self, run_id: str, decision: dict) -> None:
        self._submit(run_id, Command(resume=decision))

    def restart(self, run_id: str, stage: str, notes: str | None = None, fresh: bool = False,
                new_branch: str | None = None, user_id: int | None = None) -> None:
        """Run the workflow again from a stage that already ran, whatever the run's state (completed, failed, or
        waiting at a gate). That stage and every later agent stage re-run with the agents' current settings; gates
        ask for approval again. By default they update their existing documents in place; `fresh` first rolls the
        workspace back to where the stage started, so they regenerate everything (earlier attempts stay recorded)."""
        with session_scope() as s:
            run = s.get(Run, run_id)
            stages = run.stage_defs
            ran = {e.stage for e in run.stages if e.status == "completed"}
        keys = [d["key"] for d in stages]
        at = keys.index(stage)
        # Steps that ran update their documents; a step the workflow gained since (never run here) runs as new.
        values: dict = {"refresh": [] if fresh else [d["key"] for d in stages[at:] if d["kind"] == "agent"
                                                     and d["key"] in ran],
                        "route": stage, "amend": None}  # a re-run of the workflow, not a reviewer's amendment
        if notes:
            values["feedback"] = {stage: notes}
        # Continue as if the step before had just finished (START for the first step); a pending gate is dropped.
        previous = node_before(Workflow.from_dicts(stages), stage)
        with self._lock:
            if run_id in self._active:
                raise RunBusyError(f"Run {run_id} is already executing")
        with session_scope() as s:
            # The dropped gate never got a decision. Left as it is, the gate would reuse it when the workflow reaches
            # it again, older than the steps re-run since, so the timeline would show the gate as superseded.
            s.execute(delete(StageExecution).where(StageExecution.run_id == run_id,
                                                   StageExecution.status == "awaiting_approval"))
        self.graph_for(run_id).update_state(self._config(run_id), values, as_node=previous)
        if new_branch:  # fresh, on a new feature branch cut from the latest base branch
            prepare = lambda: recut(run_id, stage, new_branch, user_id)  # noqa: E731
        else:
            prepare = (lambda: start_fresh(run_id, stage)) if fresh else None
        self._submit(run_id, None, prepare=prepare)

    def retry(self, run_id: str, notes: str | None = None) -> None:
        """Run the failed step again. `notes` reach its agent with the step (what to do differently), as a reviewer's
        comments do."""
        if not self.graph_for(run_id).get_state(self._config(run_id)).values:
            # It failed before the first step started (e.g. preparing its workspace), so nothing was saved to resume
            # from: start it again, without what a failed checkout may have left behind.
            with session_scope() as s:
                ws = Path(s.get(Run, run_id).workspace)
            git_ops.remove_tree(ws)
            self._submit(run_id, None, initial=True)
            return
        if notes:
            with session_scope() as s:
                run = s.get(Run, run_id)
                stages, stage = run.stage_defs, run.current_stage
            keys = [d["key"] for d in stages]
            if stage in keys and stages[keys.index(stage)]["kind"] == "agent":
                graph = self.graph_for(run_id)
                feedback = {**(graph.get_state(self._config(run_id)).values.get("feedback") or {}), stage: notes}
                # As a restart does: continue as if the step before had just finished, so the failed step runs next.
                graph.update_state(self._config(run_id), {"feedback": feedback, "route": stage},
                                   as_node=node_before(Workflow.from_dicts(stages), stage))
        # Invoking with None continues from the last checkpoint, re-running the node that failed.
        self._submit(run_id, None)

    def _submit(self, run_id: str, inp, initial: bool = False, prepare=None) -> None:
        with self._lock:
            if run_id in self._active:
                raise RunBusyError(f"Run {run_id} is already executing")
            self._active.add(run_id)
        try:  # shown as running at once, so the page that asked (an approval, a re-run) doesn't offer it again
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.status, run.error = "running", None
        except Exception:
            with self._lock:
                self._active.discard(run_id)
            raise
        self._pool.submit(self._drive, run_id, inp, initial, prepare)

    def _drive(self, run_id: str, inp, initial: bool, prepare=None) -> None:
        try:
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.status, run.error = "running", None
                domain, operation, workspace, branch = run.domain, run.operation, run.workspace, run.branch
                repository, source_branch, started_by = delivery.run_repository(run), run.base_branch, run.started_by
                projects.set_current(run.project)  # this worker thread now works for the run's project
            if initial and repository:
                # The run reads the project's shared read-only copy of the monolith, and its own workspace holds only
                # documents (and other repositories it changes). A step that changes the monolith's code gets the
                # run its own checkout just before it starts (workspace.check_out_monolith); the base branch chosen
                # for that is kept until then.
                shared = legacy.snapshot(projects.current(), started_by, source_branch)
                git_ops.init_workspace(Path(workspace), None, branch)
                with session_scope() as s:
                    run = s.get(Run, run_id)
                    run.legacy, run.repository = shared, ""
                exclude_nested(Path(workspace))
                emit(run_id, "status", f"Reading the monolith from the shared read-only copy of "
                                       f"{shared['branch'] or 'its default branch'} at {shared['sha'][:10]}"
                                       f"{': ' + shared['subject'] if shared.get('subject') else ''}")
                inp = {"run_id": run_id, "domain": domain, "operation": operation, "workspace": workspace,
                       "feedback": {}, "stage_shas": {}}
            elif initial:
                emit(run_id, "status", f"Preparing workspace: branch {branch}"
                                       f"{f' from {source_branch}' if source_branch else ''}")
                # The server's copy of the repository, shared by the project's runs, fetched first (as the user who
                # started the run) so the run starts from the latest commit.
                source = repos.latest_for_run(projects.current(), repository, started_by,
                                              lambda m: emit(run_id, "log", m))
                base_sha = git_ops.init_workspace(Path(workspace), source, branch, source_branch=source_branch)
                with session_scope() as s:
                    run = s.get(Run, run_id)
                    run.base_sha = base_sha
                    if source and not source_branch:  # the repository's default branch: record which it is
                        run.base_branch = repos.default_branch(Path(source))
                # The other repositories steps change code in are checked out when their branches are confirmed,
                # just before the first such step (see app/delivery.py).
                exclude_nested(Path(workspace))
                inp = {"run_id": run_id, "domain": domain, "operation": operation, "workspace": workspace,
                       "feedback": {}, "stage_shas": {}}
            else:
                ensure_workspace(run_id)
            if prepare is not None:  # e.g. a fresh restart's rollback, done here as it may clone the monolith
                prepare()

            self.graph_for(run_id).invoke(inp, self._config(run_id))
            gate, error = self.pending_gate(run_id), None
        except Exception as e:
            log.exception("Run %s failed", run_id)
            gate, error = None, e
        finally:
            # Release the run before publishing its new status: a decision or retry sent as soon as the status
            # shows must not be refused as "already executing".
            with self._lock:
                self._active.discard(run_id)
            from app.agents.common import request_stop
            request_stop(run_id, on=False)  # a stop asked for ends with this execution; a retry runs normally

        if gate and gate.get("kind") == "branches" and not gate["repository"].get("checked_out"):
            _fetch_for_question(run_id, gate["repository"]["url"])
        review = error is None and not gate and delivery.wants_review(run_id)
        if error is None and not gate and not review:
            # Finished: nothing on disk is needed any more (documents and diffs are in the database, commits
            # pushed). Removed before the run shows as completed, so a completed run never still has a workspace.
            release_workspace(run_id)
        with session_scope() as s:
            run = s.get(Run, run_id)
            if error is not None:
                run.status, run.error = "failed", str(error)
            elif gate and gate.get("kind") == "branches":  # a step waits for its branches to be confirmed
                run.status, run.current_stage = "awaiting_branches", gate["stage"]
            elif gate and gate.get("kind") == "commit":  # a step's code changes wait to be reviewed and committed
                run.status, run.current_stage = "awaiting_commit", gate["stage"]
            elif gate and gate.get("kind") == "docs":  # approved AS-IS documents wait to be published (or not)
                run.status, run.current_stage = "awaiting_publish", gate["stage"]
            elif gate:
                run.status, run.current_stage = "awaiting_approval", gate["gate"]
            elif review:  # the workflow is done; a person reviews the code changes and commits them
                run.status, run.current_stage = "awaiting_commit", None
            else:
                run.status, run.current_stage = "completed", None
        if error is not None:
            emit(run_id, "error", f"Run failed: {error}")
        elif gate and gate.get("kind") == "branches":
            emit(run_id, "status", f"Confirm the branches for {gate['repository']['name']} before "
                                   f"{gate['stage_title']} changes code", gate["stage"])
        elif gate and gate.get("kind") == "docs":
            emit(run_id, "status", f"Publish the approved AS-IS documents ({len(gate['files'])}) to the monolith's "
                                   "documentation, or skip", gate["stage"])
        elif gate and gate.get("kind") == "commit":
            emit(run_id, "status", f"Review the code {gate['stage_title']} changed in {gate['repository']['name']} "
                                   "and commit it", gate["stage"])
        elif review:
            emit(run_id, "status", "Workflow finished: review the code changes and commit them to git")
        elif not gate:
            emit(run_id, "status", "Migration run completed")

    def forget(self, run_id: str) -> None:
        """Drop a deleted run's checkpoints."""
        self._saver.delete_thread(run_id)

    def shutdown(self) -> None:
        questions.shutting_down.set()  # agents waiting for an answer give up so their threads can finish
        self._pool.shutdown(wait=True, cancel_futures=True)

    def recover_interrupted(self) -> None:
        """Runs left running (or waiting for an answer) by a previous process are failed; the user can retry them
        from the UI."""
        questions.cancel_open()
        with session_scope() as s:
            for run in s.query(Run).filter(Run.status.in_(["running", "awaiting_input"])):
                run.status, run.error = "failed", "Interrupted by a server restart. Retry to resume."


runner: Runner | None = None


def get_runner() -> Runner:
    global runner
    if runner is None:
        runner = Runner()
    return runner
