"""Removing runs with everything recorded for them, for an admin: a project with all its runs, or a feature started
over with another workflow. A run that is working is stopped first: its agent ends at its next turn or tool call
(app/agents/common.py request_stop), a question it waits on is cancelled.

What a run recorded: its stages, documents, approvals, questions and logs, its graph checkpoints and workspace, the
cross-domain dependencies its approved design recorded, and the contracts it proposed that no one accepted yet
(contracts already approved or provided belong to the catalogue other domains rely on, so they stay). Code it pushed
and documents it published to the monolith's repository are in git, outside the studio: they are not undone.
"""

import time
from pathlib import Path

from sqlalchemy import select

from app.agents import common
from app.db.models import Approval, Artifact, AuditEvent, Contract, Dependency, Question, Run, StageExecution
from app.db.session import session_scope
from app.orchestrator import questions
from app.tools import git_ops

BUSY = ("running", "awaiting_input")  # a worker thread is using the run
STOP_WAIT = 90.0  # seconds to wait for an agent to reach its next turn or tool call


class StillWorking(RuntimeError):
    pass


def stop(run_ids: list[str], wait: float = STOP_WAIT) -> None:
    """Stop the runs that are working and wait for them to stop. Raises StillWorking when one hasn't stopped in time
    (an AI agent finishing a long model turn): asking again later continues."""
    from app.orchestrator.runner import get_runner

    runner = get_runner()
    working = [run_id for run_id in run_ids if runner.is_active(run_id)]
    for run_id in working:
        common.request_stop(run_id)
        questions.cancel_open(run_id)
    deadline = time.monotonic() + wait
    while working and time.monotonic() < deadline:
        working = [run_id for run_id in working if runner.is_active(run_id)]
        if working:
            time.sleep(0.2)
    if working:
        raise StillWorking(f"{len(working)} run(s) are still finishing what they were doing; they stop at their next "
                           "step. Try again in a minute")


def delete(project: str, run_ids: list[str]) -> int:
    """Delete these runs (stopped already) and everything recorded for them. Returns how many were deleted."""
    from app.orchestrator.runner import get_runner

    if not run_ids:
        return 0
    with session_scope() as s:
        runs = s.scalars(select(Run).where(Run.project == project, Run.id.in_(run_ids))).all()
        ids = [r.id for r in runs]
        workspaces = [r.workspace for r in runs if r.workspace]
        for model in (StageExecution, Artifact, Approval, AuditEvent, Question):
            s.query(model).filter(model.run_id.in_(ids)).delete(synchronize_session=False)
        s.query(Dependency).filter(Dependency.project == project, Dependency.run_id.in_(ids)).delete(
            synchronize_session=False)
        s.query(Contract).filter(Contract.project == project, Contract.run_id.in_(ids),
                                 Contract.status == "proposed").delete(synchronize_session=False)
        s.query(Run).filter(Run.id.in_(ids)).delete(synchronize_session=False)
    for run_id in ids:
        get_runner().forget(run_id)  # its graph checkpoints
        common.request_stop(run_id, on=False)
    for ws in workspaces:
        git_ops.remove_tree(Path(ws))
    return len(ids)


def stop_and_delete(project: str, run_ids: list[str]) -> int:
    stop(run_ids)
    return delete(project, run_ids)
