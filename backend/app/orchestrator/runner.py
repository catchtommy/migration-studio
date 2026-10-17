"""Drives each run's graph on a worker thread. One LangGraph thread (thread_id = run id) per run."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.graph import START
from langgraph.types import Command

from app.db.models import Run
from app.db.session import emit, session_scope
from app import delivery, legacy, projects, repos
from app.orchestrator import questions
from app.orchestrator.checkpointer import SqlAlchemySaver
from app.orchestrator.graph import build_graph
from app.orchestrator.stages import Workflow
from app.tools import git_ops
from app.workspace import ensure as ensure_workspace
from app.workspace import exclude_nested, release_workspace, start_fresh

log = logging.getLogger(__name__)


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

    def restart(self, run_id: str, stage: str, notes: str | None = None, fresh: bool = False) -> None:
        """Run the workflow again from a stage that already ran, whatever the run's state (completed, failed, or
        waiting at a gate). That stage and every later agent stage re-run with the agents' current settings; gates
        ask for approval again. By default they update their existing documents in place; `fresh` first rolls the
        workspace back to where the stage started, so they regenerate everything (earlier attempts stay recorded)."""
        with session_scope() as s:
            stages = s.get(Run, run_id).stage_defs
        keys = [d["key"] for d in stages]
        at = keys.index(stage)
        values: dict = {"refresh": [] if fresh else [d["key"] for d in stages[at:] if d["kind"] == "agent"],
                        "route": stage}
        if notes:
            values["feedback"] = {stage: notes}
        # Continue as if the step before had just finished (START for the first step); a pending gate is dropped.
        previous = keys[at - 1] if at else START
        with self._lock:
            if run_id in self._active:
                raise RunBusyError(f"Run {run_id} is already executing")
        self.graph_for(run_id).update_state(self._config(run_id), values, as_node=previous)
        self._submit(run_id, None, prepare=(lambda: start_fresh(run_id, stage)) if fresh else None)

    def retry(self, run_id: str) -> None:
        # Invoking with None continues from the last checkpoint, re-running the node that failed.
        self._submit(run_id, None)

    def _submit(self, run_id: str, inp, initial: bool = False, prepare=None) -> None:
        with self._lock:
            if run_id in self._active:
                raise RunBusyError(f"Run {run_id} is already executing")
            self._active.add(run_id)
        self._pool.submit(self._drive, run_id, inp, initial, prepare)

    def _drive(self, run_id: str, inp, initial: bool, prepare=None) -> None:
        try:
            with session_scope() as s:
                run = s.get(Run, run_id)
                run.status, run.error = "running", None
                domain, operation, workspace, branch = run.domain, run.operation, run.workspace, run.branch
                repository, source_branch, started_by = delivery.run_repository(run), run.base_branch, run.started_by
                own_checkout = delivery.changes_monolith(run.stage_defs)
                projects.set_current(run.project)  # this worker thread now works for the run's project
            if initial and repository and not own_checkout:
                # Nothing in this run changes the monolith: it reads the project's shared read-only copy, and its
                # own workspace holds only documents (and other repositories it changes).
                shared = legacy.snapshot(projects.current(), started_by)
                git_ops.init_workspace(Path(workspace), None, branch)
                with session_scope() as s:
                    run = s.get(Run, run_id)
                    run.legacy, run.repository, run.base_branch = shared, "", None
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
