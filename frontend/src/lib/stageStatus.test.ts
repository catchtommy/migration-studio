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
