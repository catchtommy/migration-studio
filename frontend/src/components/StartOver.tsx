import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail } from "../lib/api";
import { useIsAdmin, useWorkflows } from "../lib/hooks";

/** Admin only: start the run's feature over with another workflow. Every run of the feature (one that is working is
 * stopped first) is deleted with its documents, approvals and logs, and a new run starts. */
export function StartOver({ run }: { run: RunDetail }) {
  const isAdmin = useIsAdmin();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: workflows = [] } = useWorkflows();
  const [open, setOpen] = useState(false);
  const [workflow, setWorkflow] = useState("");
  const startOver = useMutation({
    mutationFn: () => api.startOver(run.id, workflow),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${created.id}`);
    },
  });
  if (!isAdmin || run.scope !== "operation") return null;
  const choices = workflows.filter((w) => (w.scope ?? "operation") === "operation" && w.key !== run.workflow);
  if (choices.length === 0) return null;

  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-xs font-medium text-bad hover:underline">
        Start over with another workflow…
      </button>
    );

  const chosen = choices.find((w) => w.key === workflow);
  return (
    <section className="mt-2 space-y-3 rounded-lg border border-bad/40 bg-bad/5 p-4 text-sm" aria-label="Start over with another workflow">
      <h3 className="font-semibold text-bad">Start {run.operation} over with another workflow</h3>
      <p className="text-subtle">
        Every run of this feature is deleted with everything recorded for it: steps, documents, approvals, questions, logs and workspace, the
        dependencies its design recorded and the contracts it proposed that no one accepted yet. A run that is working is stopped first. Then a new run
        starts with the chosen workflow. Code pushed and documents published to the repositories stay in git. This can't be undone.
      </p>
      <label className="block">
        <span className="mb-1 block text-xs text-subtle">Workflow</span>
        <select value={workflow} onChange={(e) => setWorkflow(e.target.value)} className="w-full max-w-sm rounded-md border border-border bg-surface px-2 py-1.5">
          <option value="">Choose a workflow…</option>
          {choices.map((w) => (
            <option key={w.key} value={w.key}>
              {w.name}
            </option>
          ))}
        </select>
      </label>
      {startOver.error && <p className="text-bad">{(startOver.error as Error).message}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="rounded-md px-3 py-1.5 hover:bg-muted" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          type="button"
          disabled={!chosen || startOver.isPending}
          onClick={() =>
            window.confirm(`Delete every run of ${run.operation} and its data, and start it over with ${chosen!.name}?`) && startOver.mutate()
          }
          className="rounded-md bg-bad px-3 py-1.5 font-medium text-white disabled:opacity-50"
        >
          {startOver.isPending ? "Stopping and deleting…" : `Delete and start over${chosen ? ` with ${chosen.name}` : ""}`}
        </button>
      </div>
    </section>
  );
}
