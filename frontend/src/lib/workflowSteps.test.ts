import type { WorkflowStep } from "./api";
import { normalizeSteps } from "./workflowSteps";

const agent = (key: string): WorkflowStep => ({ kind: "agent", key });
const gate = (...reviews: string[]): WorkflowStep => ({ kind: "gate", key: "g", title: "G", reviews });

test("gates keep reviews of earlier agent steps", () => {
  const steps = [agent("a"), agent("b"), gate("a", "b")];
  expect(normalizeSteps(steps)).toEqual(steps);
});

test("moving a gate above a reviewed step drops that review", () => {
  expect(normalizeSteps([agent("a"), gate("a", "b"), agent("b")])[1].reviews).toEqual(["a"]);
});

test("an agent step only keeps inputs from steps still before it", () => {
  const steps: WorkflowStep[] = [agent("a"), { kind: "agent", key: "c", uses: ["a", "b"] }, agent("b")];
  expect(normalizeSteps(steps)[1].uses).toEqual(["a"]);
  expect(normalizeSteps([agent("a"), { kind: "agent", key: "c", uses: null }])[1].uses).toBeNull();
});

test("removing a reviewed step drops it from the gate", () => {
  expect(normalizeSteps([agent("b"), gate("a", "b")])[1].reviews).toEqual(["b"]);
});
