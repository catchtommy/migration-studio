"""Bringing a migration run up to date with its workflow and its agents.

A run keeps a copy of its workflow's steps from when it started (Run.workflow_stages), so editing a workflow never
reshapes a run behind its back. Its agents are read when each step starts, so the steps it hasn't reached yet use
their agents as they are then; the steps it has already run keep the output of the agents as they were.

When agents changed after their steps ran, a person can also keep what those steps produced: the notice goes until an
agent changes again (Run.agents_kept_at), and the steps the run hasn't reached use the current agents as always.

A person can update a run in one of two ways:
- the upcoming steps only (a run that is waiting: at a gate, after a failure, ...): everything the run has reached
  stays exactly as it ran, and the steps after that point become the workflow's current ones. Steps the workflow
  gained before that point can't run this way; they are reported as missed.
- with a re-run (also a completed run): the run takes the workflow's current steps, all of them, and runs again from
  the earliest step a change affects (a step added, removed, moved or changed in the workflow before the point the run
  has reached, or a step whose agent, or a shared text its agent uses, changed after the step ran). That step and the
  ones after it update their documents with the current agents; their gates ask for approval again.
"""

from app import agent_store, projects, shared_texts, workflow_store

# Runs that are waiting and will go on: the ones whose upcoming steps an update can still change.
UPDATABLE = ("failed", "awaiting_approval", "changes_requested", "awaiting_commit", "awaiting_branches")
# Runs that can be re-run from a step (a completed one too): the ones a re-run can bring up to date.
RERUNNABLE = (*UPDATABLE, "completed")
# Shared texts every agent gets, whatever its instructions include.
_ALWAYS = ("preamble", "feature_rules")


def _plain(stage: dict) -> dict:
    """A step without its code settings, which every run picks up when it goes on (delivery.refresh_code_settings),
    without a gate's checklist, which a run waiting at the gate reads from the workflow (app/gate_checklist.py), and
    without "publish_docs" (removed: a run that has it publishes nothing)."""
    return {k: v for k, v in stage.items() if k not in ("code", "checklist", "publish_docs")}


def _reached(run, keys: list[str]) -> int:
    """The index of the step the run has reached (-1: none yet)."""
    if run.current_stage in keys:  # where the run is now: after a re-run from an earlier step, the later steps run again
        return keys.index(run.current_stage)
    reached = {e.stage for e in run.stages}
    return max((i for i, k in enumerate(keys) if k in reached), default=-1)


def agents_changed(run) -> dict[str, str]:
    """Steps the run has completed whose agent changed after the step last ran (and after someone last chose to keep
    what the steps produced, Run.agents_kept_at): step key -> what changed."""
    last_run: dict[str, object] = {}
    for e in run.stages:
        if e.status == "completed" and (e.stage not in last_run or e.started_at > last_run[e.stage]):
            last_run[e.stage] = e.started_at
    kept = getattr(run, "agents_kept_at", None)
    if kept is not None:
        last_run = {k: max(ran, kept) for k, ran in last_run.items()}
    if not last_run:
        return {}
    with projects.use(run.project):
        texts = {t["key"]: t for t in shared_texts.list_all()}
        out = {}
        for step in run.stage_defs:
            key, ran = step["key"], last_run.get(step["key"])
            if step["kind"] != "agent" or ran is None:
                continue
            try:
                agent = agent_store.get(key)
            except KeyError:
                continue  # the agent was deleted: nothing newer to use
            used = {*shared_texts.PLACEHOLDER.findall(f"{agent['instructions']}\n{agent['guardrails']}"), *_ALWAYS}
            newer = [texts[k]["title"] for k in sorted(used)
                     if k in texts and texts[k]["updated_at"] and texts[k]["updated_at"] > ran]
            if agent["updated_at"] > ran:
                out[key] = "its agent changed"
            elif newer:
                out[key] = f"the shared text {', '.join(newer)} changed"
    return out


def _rerun(old: list[dict], cut: int, current: list[dict], changed: dict[str, str]) -> dict | None:
    """Where a re-run with the current workflow would start, and why; None when nothing before the point the run has
    reached (or at it) differs."""
    current_keys = [d["key"] for d in current]
    reasons, start = [], None
    if cut >= 0:  # the workflow up to where the run is (nothing reached yet: the upcoming steps cover it all)
        reached_key = old[cut]["key"]
        limit = current_keys.index(reached_key) + 1 if reached_key in current_keys else len(current)
        for i, (was, now) in enumerate(zip(old[:cut + 1], current[:limit])):
            if was["key"] != now["key"] or _plain(was) != _plain(now):
                start, reasons = i, [f"the workflow changed at {now['title']}"]
                break
        else:
            if cut + 1 != limit:  # one is a prefix of the other: steps were added or removed at the end of that part
                start = min(cut + 1, limit)
                reasons = ["the workflow changed before the point this run has reached"]
    for key, why in changed.items():
        if key in current_keys:
            i = current_keys.index(key)
            reasons.append(f"{current[i]['title']}: {why}")
            start = i if start is None else min(start, i)
    if start is None:
        return None
    target = next((d for d in current[start:] if d["kind"] == "agent"), None)
    if target is None:
        return None
    return {"from": target["key"], "title": target["title"], "reasons": reasons}


def plan(run) -> dict | None:
    """What updating the run would change, or None when nothing would:
    - the upcoming steps: {"stages": the new step list, "added", "removed", "changed", "missed": step titles,
      "reordered": bool}, or {"blocked": why} when the run can't follow the workflow that way;
    - "agents_changed": titles of completed steps whose agent changed since they ran;
    - "rerun": {"from": step key, "title", "reasons"} and "rerun_stages" (the workflow's current steps), when a re-run
      can bring the run up to date."""
    if (run.scope or "operation") != "operation" or run.status not in RERUNNABLE:
        return None
    try:
        with projects.use(run.project):
            current = workflow_store.get(run.workflow or workflow_store.STANDARD_KEY)["stages"]
    except KeyError:
        return None  # the workflow was deleted: nothing to update to
    old = run.stage_defs
    keys = [d["key"] for d in old]
    cut = _reached(run, keys)
    out: dict = {}
    if run.status in UPDATABLE:
        out = _upcoming(old, keys, cut, current) or {}
    # Only steps up to the point the run has reached: those after it run again when the run gets there (after a
    # re-run from an earlier step, the later ones are upcoming too), with their agents as they are then. Offering to
    # re-run from one of them would drop the approval the run is waiting for on the way.
    # A step the run is about to run (waiting to confirm its branches, or for an answer) or running uses the current
    # agent already: only steps whose output stays as it was count as changed.
    upcoming = run.current_stage if run.status in ("pending", "running", "awaiting_branches", "awaiting_input") else None
    changed = {k: why for k, why in agents_changed(run).items() if k in keys[:cut + 1] and k != upcoming}
    rerun = _rerun(old, cut, current, changed)
    if rerun:
        out.update(rerun=rerun, rerun_stages=current)
    if changed:
        titles = {d["key"]: d["title"] for d in old}
        out["agents_changed"] = [titles[k] for k in changed]
    return out or None


def _upcoming(old: list[dict], keys: list[str], cut: int, current: list[dict]) -> dict | None:
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
