import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail, type WorkflowUpdate as Update } from "../lib/api";

const PARTS: [keyof Update, string][] = [
  ["added", "Adds"],
  ["removed", "Removes"],
  ["changed", "Changes"],
];

/** The run's workflow changed since it started: offer to bring the steps it has not reached yet up to date. */
export function WorkflowUpdate({ run, workflowName, canUpdate }: { run: RunDetail; workflowName: string | null; canUpdate: boolean }) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: () => api.updateRunWorkflow(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  const plan = run.workflow_update;
  if (!plan) return null;
  const name = workflowName ? `The ${workflowName} workflow` : "This run's workflow";
  if (plan.blocked) {
    return <p className="mt-2 rounded-md bg-warn/10 px-3 py-1.5 text-sm text-warn">{name} has changed since this run started. {plan.blocked}.</p>;
  }
  return (
    <div className="mt-2 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">
      <p>
        {name} has changed since this run started. The run keeps the steps it started with unless you update it; updating changes only the steps it hasn't reached
        yet, and the steps it has already run stay as they are.
      </p>
      <ul className="mt-1 list-disc pl-5">
        {PARTS.map(([key, label]) => {
          const titles = plan[key] as string[] | undefined;
          return titles?.length ? (
            <li key={key}>
              {label} {titles.join(", ")}
            </li>
          ) : null;
        })}
        {plan.reordered && <li>Reorders the steps</li>}
        {!!plan.missed?.length && <li>{plan.missed.join(", ")} come before the point this run has reached, so this run won't run them; a new run will</li>}
      </ul>
      {canUpdate && (
        <div className="mt-2 flex items-center gap-3">
          <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={update.isPending} onClick={() => update.mutate()}>
            {update.isPending ? "Updating…" : "Update this run"}
          </button>
          {update.error && <span className="text-bad">{(update.error as Error).message}</span>}
        </div>
      )}
    </div>
  );
}
