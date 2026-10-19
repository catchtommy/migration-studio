import type { RunDetail, StageDef, StageExecution } from "./api";

export type DisplayStatus = "pending" | StageExecution["status"];

/**
 * Status of each stage for the timeline. A stage's latest execution is superseded (shown as pending)
 * when an earlier stage has run again since — that happens after a rejection loops the workflow back.
 *
 * The run's own status and current stage come first for the step it is on: a step being re-run or retried shows as
 * running (and every step after it as pending) as soon as the run moves to it, before its agent records an
 * execution; a step waiting for its branches to be confirmed (which happens before it starts) shows as waiting.
 */
export function stageStatuses(stages: StageDef[], run: RunDetail): Map<string, { status: DisplayStatus; latest?: StageExecution; attempts: StageExecution[] }> {
  const result = new Map<string, { status: DisplayStatus; latest?: StageExecution; attempts: StageExecution[] }>();
  const current = stages.findIndex((s) => s.key === run.current_stage);
  const onStep = current >= 0 && stages[current].kind === "agent" && (run.status === "running" || run.status === "awaiting_branches");
  let newestEarlierId = -1;
  stages.forEach((stage, i) => {
    const attempts = run.stages.filter((e) => e.stage === stage.key);
    const latest = attempts[attempts.length - 1];
    const superseded = latest !== undefined && latest.id < newestEarlierId;
    let status: DisplayStatus = !latest || superseded ? "pending" : latest.status;
    if (onStep && i > current) status = "pending";
    else if (onStep && i === current) status = run.status === "running" ? "running" : "awaiting_approval";
    result.set(stage.key, { status, latest, attempts });
    for (const e of attempts) newestEarlierId = Math.max(newestEarlierId, e.id);
  });
  return result;
}

/** Coarse progress for list views, which only know the run's current stage. */
export function progressIndex(stages: StageDef[], current: string | null, status: string): number {
  if (status === "completed" || status === "awaiting_commit") return stages.length;
  const i = stages.findIndex((s) => s.key === current);
  return i < 0 ? 0 : i;
}
