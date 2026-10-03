import type { RunDetail, StageDef, StageExecution } from "./api";

export type DisplayStatus = "pending" | StageExecution["status"];

/**
 * Status of each stage for the timeline. A stage's latest execution is superseded (shown as pending)
 * when an earlier stage has run again since — that happens after a rejection loops the workflow back.
 */
export function stageStatuses(stages: StageDef[], run: RunDetail): Map<string, { status: DisplayStatus; latest?: StageExecution; attempts: StageExecution[] }> {
  const result = new Map<string, { status: DisplayStatus; latest?: StageExecution; attempts: StageExecution[] }>();
  let newestEarlierId = -1;
  for (const stage of stages) {
    const attempts = run.stages.filter((e) => e.stage === stage.key);
    const latest = attempts[attempts.length - 1];
    const superseded = latest !== undefined && latest.id < newestEarlierId;
    result.set(stage.key, { status: !latest || superseded ? "pending" : latest.status, latest, attempts });
    for (const e of attempts) newestEarlierId = Math.max(newestEarlierId, e.id);
  }
  return result;
}

/** Coarse progress for list views, which only know the run's current stage. */
export function progressIndex(stages: StageDef[], current: string | null, status: string): number {
  if (status === "completed") return stages.length;
  const i = stages.findIndex((s) => s.key === current);
  return i < 0 ? 0 : i;
}
