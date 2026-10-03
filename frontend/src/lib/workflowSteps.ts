import type { WorkflowStep } from "./api";

export type EditableStep = WorkflowStep & { uid: number };

/** After steps move or are removed, gates can only review, and agent steps only use the output of,
 * agent steps that are still before them. */
export function normalizeSteps<T extends WorkflowStep>(steps: T[]): T[] {
  const before: string[] = [];
  return steps.map((step) => {
    if (step.kind === "agent") {
      const uses = step.uses?.filter((u) => before.includes(u));
      before.push(step.key);
      return step.uses && uses!.length !== step.uses.length ? { ...step, uses } : step;
    }
    const reviews = (step.reviews ?? []).filter((r) => before.includes(r));
    return reviews.length === (step.reviews ?? []).length ? step : { ...step, reviews };
  });
}
