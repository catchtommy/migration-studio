import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail, type WorkflowUpdate as Update } from "../lib/api";

const PARTS: [keyof Update, string][] = [
  ["added", "Adds"],
  ["removed", "Removes"],
  ["changed", "Changes"],
];

/** The run's workflow or agents changed since it started: offer to bring the steps it hasn't reached up to date, or to
 * re-run from the earliest step a change affects. */
export function WorkflowUpdate({ run, workflowName, canUpdate }: { run: RunDetail; workflowName: string | null; canUpdate: boolean }) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: (rerun: boolean) => api.updateRunWorkflow(run.id, rerun),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      queryClient.invalidateQueries({ queryKey: ["artifacts", run.id] });
    },
  });
  const plan = run.workflow_update;
  if (!plan) return null;
  const name = workflowName ? `The ${workflowName} workflow` : "This run's workflow";
  const upcoming = !plan.blocked && (PARTS.some(([key]) => !!(plan[key] as string[] | undefined)?.length) || !!plan.reordered);
  const workflowChanged = upcoming || !!plan.blocked || !!plan.missed?.length;

  return (
    <div className="mt-2 space-y-2 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">
      {workflowChanged && <p>{name} has changed since this run started.</p>}
      {plan.blocked && <p>{plan.blocked}.</p>}
      {upcoming && (
        <>
          <p>
            The run keeps the steps it started with unless you update it. <strong>Update upcoming steps</strong> changes only the steps it hasn't reached yet:
          </p>
          <ul className="list-disc pl-5">
            {PARTS.map(([key, label]) => {
              const titles = plan[key] as string[] | undefined;
              return titles?.length ? (
                <li key={key}>
                  {label} {titles.join(", ")}
                </li>
              ) : null;
            })}
            {plan.reordered && <li>Reorders the steps</li>}
            {!!plan.missed?.length && <li>{plan.missed.join(", ")} come before the point this run has reached, so only a re-run includes them</li>}
          </ul>
        </>
      )}
      {!!plan.agents_changed?.length && (
        <p>
          Agents changed since their step ran: {plan.agents_changed.join(", ")}. Steps this run hasn't reached use the current agents automatically; the steps above keep
          what they produced unless the run is re-run.
        </p>
      )}
      {plan.rerun && (
        <p>
          <strong>Update and re-run from {plan.rerun.title}</strong> takes the workflow's current steps and agents and runs again from {plan.rerun.title} ({plan.rerun.reasons.join("; ")}).
          That step and the ones after it update their documents; their gates ask for approval again. Earlier steps stay as they ran.
        </p>
      )}
      {canUpdate && (upcoming || plan.rerun) && (
        <div className="flex flex-wrap items-center gap-3">
          {upcoming && (
            <button
              type="button"
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={update.isPending}
              onClick={() => update.mutate(false)}
            >
              {update.isPending && update.variables === false ? "Updating…" : "Update upcoming steps"}
            </button>
          )}
          {plan.rerun && (
            <button
              type="button"
              className="rounded-md border border-accent/60 bg-surface px-3 py-1.5 text-sm font-medium text-accent disabled:opacity-50"
              disabled={update.isPending}
              onClick={() =>
                window.confirm(`Re-run this migration from ${plan.rerun!.title} with the current workflow and agents? Its later steps run again and their gates ask for approval again.`) &&
                update.mutate(true)
              }
            >
              {update.isPending && update.variables === true ? "Re-running…" : `Update and re-run from ${plan.rerun.title}`}
            </button>
          )}
          {update.error && <span className="text-bad">{(update.error as Error).message}</span>}
        </div>
      )}
    </div>
  );
}
