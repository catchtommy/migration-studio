"""Approval checklists: items a workflow's approval gate asks the approver to tick before approving.

A gate's items are part of its workflow step ("checklist"). Like the code settings, they are read from the workflow
as it is now, so a checklist edited while a run waits at the gate applies to that run too, and it never makes a run
re-run its agents. The ticks belong to the gate's attempt (StageExecution.checklist, [{"item", "by", "at"}]): a gate
reached again (after a rejection or a re-run) starts with nothing ticked, and a decided attempt keeps what was ticked.
Approving needs every item ticked; rejecting doesn't.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import projects, workflow_store
from app.db.models import Run, StageExecution, utcnow

MAX_ITEMS = 30
MAX_LENGTH = 300


class ChecklistError(ValueError):
    pass


def clean(items, where: str) -> list[str]:
    """A gate's checklist as the workflow stores it: trimmed, non-empty, unique items; or ChecklistError."""
    if items is None:
        return []
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise ChecklistError(f"{where}: the checklist must be a list of texts")
    out = list(dict.fromkeys(i.strip() for i in items if i.strip()))
    if len(out) > MAX_ITEMS:
        raise ChecklistError(f"{where}: a checklist has at most {MAX_ITEMS} items")
    long = next((i for i in out if len(i) > MAX_LENGTH), None)
    if long:
        raise ChecklistError(f"{where}: checklist item {long[:40]!r}... is longer than {MAX_LENGTH} characters")
    return out


def items(run: Run, gate: str) -> list[str]:
    """The gate's checklist: from the run's workflow as it is now, else (the workflow or the gate is gone) from the
    run's copy of it."""
    try:
        with projects.use(run.project):
            current = workflow_store.get(run.workflow or workflow_store.STANDARD_KEY)["stages"]
        step = next((d for d in current if d["key"] == gate and d["kind"] == "gate"), None)
    except KeyError:
        step = None
    step = step or next((d for d in run.stage_defs if d["key"] == gate), None) or {}
    return list(step.get("checklist") or [])


def _attempt(db: Session, run_id: str, gate: str) -> StageExecution | None:
    return db.scalar(select(StageExecution).where(StageExecution.run_id == run_id, StageExecution.stage == gate,
                                                  StageExecution.status == "awaiting_approval"))


def state(db: Session, run: Run, gate: str) -> list[dict]:
    """Each item of the gate's checklist and whether it is ticked at the attempt waiting for approval:
    [{"item", "done", "by", "at"}]."""
    attempt = _attempt(db, run.id, gate)
    ticked = {t["item"]: t for t in (attempt.checklist if attempt else None) or []}
    return [{"item": i, "done": i in ticked, "by": ticked.get(i, {}).get("by"), "at": ticked.get(i, {}).get("at")}
            for i in items(run, gate)]


def tick(db: Session, run: Run, gate: str, item: str, done: bool, by: str) -> list[dict]:
    """Tick (or untick) an item at the attempt waiting for approval; returns the checklist's state."""
    if item not in items(run, gate):
        raise ChecklistError(f"{item!r} is not on this gate's checklist")
    attempt = _attempt(db, run.id, gate)
    if attempt is None:
        raise ChecklistError("The gate is not waiting for approval")
    ticked = [t for t in attempt.checklist or [] if t["item"] != item]
    if done:
        ticked.append({"item": item, "by": by, "at": utcnow().isoformat()})
    attempt.checklist = ticked or None
    db.flush()
    return state(db, run, gate)


def missing(db: Session, run: Run, gate: str) -> list[str]:
    """The items not ticked yet: approving waits for them."""
    return [s["item"] for s in state(db, run, gate) if not s["done"]]


def settle(db: Session, run: Run, gate: str) -> None:
    """At the decision: the attempt keeps the ticks of the items still on the checklist, in its order."""
    attempt = _attempt(db, run.id, gate)
    if attempt is not None and attempt.checklist:
        ticked = {t["item"]: t for t in attempt.checklist}
        attempt.checklist = [ticked[i] for i in items(run, gate) if i in ticked] or None
