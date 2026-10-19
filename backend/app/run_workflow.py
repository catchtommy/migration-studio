"""Bringing a migration run up to date with its workflow.

A run keeps a copy of its workflow's steps from when it started (Run.workflow_stages), so editing a workflow never
reshapes a run behind its back. A person can choose to update a run that is waiting (at a gate, after a failure, ...)
to the workflow's current steps: everything the run has already reached stays exactly as it ran, and the steps after
that point become the workflow's current ones. Steps the workflow gained before that point can't run in this run;
they are reported as missed.
"""

from app import projects, workflow_store

# Runs that are waiting and will go on: the ones an update can still change. A completed run has nothing left to run.
UPDATABLE = ("failed", "awaiting_approval", "changes_requested", "awaiting_commit", "awaiting_branches",
             "awaiting_publish")


def _plain(stage: dict) -> dict:
    """A step without its code settings, which every run picks up when it goes on (delivery.refresh_code_settings)."""
    return {k: v for k, v in stage.items() if k != "code"}


def plan(run) -> dict | None:
    """What updating the run would change, or None when its upcoming steps already match its workflow:
    {"stages": the new step list, "added", "removed", "changed", "missed": step titles, "reordered": bool}, or
    {"blocked": why} when the run can't be updated."""
    if (run.scope or "operation") != "operation" or run.status not in UPDATABLE:
        return None
    try:
        with projects.use(run.project):
            current = workflow_store.get(run.workflow or workflow_store.STANDARD_KEY)["stages"]
    except KeyError:
        return None  # the workflow was deleted: nothing to update to
    old = run.stage_defs
    keys = [d["key"] for d in old]
    if run.current_stage in keys:  # where the run is now: after a re-run from an earlier step, the later steps run again
        cut = keys.index(run.current_stage)
    else:
        reached = {e.stage for e in run.stages}
        cut = max((i for i, k in enumerate(keys) if k in reached), default=-1)
    new_keys = [d["key"] for d in current]
    if cut >= 0 and keys[cut] not in new_keys:
        return {"blocked": f"The workflow no longer has {old[cut]['title']}, the step this run has reached, so the "
                           "run can't follow it. Start a new run to use the current workflow"}
    start = new_keys.index(keys[cut]) + 1 if cut >= 0 else 0
    stages = [*old[:cut + 1], *current[start:]]
    if [_plain(d) for d in stages] == [_plain(d) for d in old]:
        return None
    before, after = {d["key"]: d for d in old[cut + 1:]}, {d["key"]: d for d in current[start:]}
    return {
        "stages": stages,
        "added": [d["title"] for k, d in after.items() if k not in before],
        "removed": [d["title"] for k, d in before.items() if k not in after],
        "changed": [d["title"] for k, d in after.items() if k in before and _plain(d) != _plain(before[k])],
        "reordered": [k for k in after if k in before] != [k for k in before if k in after],
        "missed": [d["title"] for d in current[:start] if d["key"] not in keys],
    }


def summary(update: dict) -> str:
    """The update in a sentence, for the run's activity log."""
    parts = [f"{what} {', '.join(update[key])}" for key, what in
             (("added", "adds"), ("removed", "removes"), ("changed", "changes"), ("missed", "can't run (before this "
                                                                                       "point)")) if update.get(key)]
    if update.get("reordered"):
        parts.append("reorders the steps")
    return "; ".join(parts) or "no change"
