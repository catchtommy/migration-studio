import type { RunDetail, StageDef, StageExecution } from "./api";
import { progressIndex, stageStatuses } from "./stageStatus";

const stages: StageDef[] = [
  { key: "a", title: "A", kind: "agent", description: "", reviews: [] },
  { key: "b", title: "B", kind: "agent", description: "", reviews: [] },
  { key: "g", title: "G", kind: "gate", description: "", reviews: ["a", "b"] },
];

let nextId = 1;
const ex = (stage: string, status: StageExecution["status"], attempt = 1): StageExecution => ({
  id: nextId++, stage, attempt, status, start_sha: null, end_sha: null, feedback: null, error: null,
  started_at: "", finished_at: null,
});

const run = (executions: StageExecution[]) => ({ stages: executions }) as RunDetail;

test("stages never run are pending", () => {
  const s = stageStatuses(stages, run([ex("a", "running")]));
  expect(s.get("a")!.status).toBe("running");
  expect(s.get("b")!.status).toBe("pending");
});

test("a rejection that loops back marks later stages pending again", () => {
  const s = stageStatuses(stages, run([
    ex("a", "completed"), ex("b", "completed"), ex("g", "rejected"),
    ex("a", "running", 2),
  ]));
  expect(s.get("a")!.status).toBe("running");
  expect(s.get("a")!.attempts).toHaveLength(2);
  expect(s.get("b")!.status).toBe("pending");
  expect(s.get("g")!.status).toBe("pending");
});

test("progress index", () => {
  expect(progressIndex(stages, "b", "running")).toBe(1);
  expect(progressIndex(stages, null, "completed")).toBe(3);
});

test("a step being re-run shows as running, and the steps after it as pending, before its agent starts", () => {
  const s = stageStatuses(stages, { ...run([ex("a", "completed"), ex("b", "completed"), ex("g", "approved")]), status: "running", current_stage: "a" } as RunDetail);
  expect(s.get("a")!.status).toBe("running");
  expect(s.get("b")!.status).toBe("pending");
  expect(s.get("g")!.status).toBe("pending");
});

test("a step waiting for its branches shows as waiting", () => {
  const s = stageStatuses(stages, { ...run([ex("a", "completed")]), status: "awaiting_branches", current_stage: "b" } as RunDetail);
  expect(s.get("a")!.status).toBe("completed");
  expect(s.get("b")!.status).toBe("awaiting_approval");
});

test("an approved gate stays approved while the run moves on", () => {
  const s = stageStatuses(stages, { ...run([ex("a", "completed"), ex("b", "completed"), ex("g", "approved")]), status: "running", current_stage: "g" } as RunDetail);
  expect(s.get("g")!.status).toBe("approved");
});

test("attempts are ordered by their number, whatever order the ids come in (Oracle identities need not grow)", () => {
  const second = { ...ex("a", "completed", 2), id: 3, started_at: "2026-10-23T10:05:00Z" };
  const first = { ...ex("a", "completed", 1), id: 90, started_at: "2026-10-23T09:00:00Z" };
  const gate = { ...ex("g", "awaiting_approval"), id: 5, started_at: "2026-10-23T10:06:00Z" };
  const s = stageStatuses(stages, run([first, second, gate])).get("a")!;
  expect(s.attempts.map((e) => e.attempt)).toEqual([1, 2]);
  expect(s.latest?.attempt).toBe(2); // the one that can be edited at the gate
  expect(s.status).toBe("completed"); // not superseded: nothing earlier ran after it
});
