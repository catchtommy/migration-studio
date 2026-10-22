import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail } from "../lib/api";
import { useCan, useIsAdmin } from "../lib/hooks";
import { EMPTY_INPUTS, InputsEditor } from "./InputsEditor";

/** Admin only: stop a run whose agent is working, e.g. going round in circles. It ends at its next turn or tool call;
 * the step then fails keeping what it did, to retry with notes or changed inputs. */
export function StopRun({ run }: { run: RunDetail }) {
  const isAdmin = useIsAdmin();
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: () => api.stopRun(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  if (!isAdmin || !["running", "awaiting_input"].includes(run.status)) return null;
  if (run.stopping || stop.isSuccess)
    return <span className="text-sm text-warn">Stopping… the agent ends at its next step</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={stop.isPending}
        onClick={() =>
          window.confirm("Stop this run? The agent ends at its next step; what it did so far is kept, and you can retry it with notes or changed inputs.") &&
          stop.mutate()
        }
        className="rounded-md border border-bad/50 px-3 py-1 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
      >
        ■ Stop run
      </button>
      {stop.error && <span className="text-sm text-bad">{(stop.error as Error).message}</span>}
    </span>
  );
}

/** A failed (or stopped) run: why, and how to go on. Retry continues from what the failed attempt kept, with notes for
 * its agent and the run's inputs as changed here; or starts the step over. */
export function FailedRun({ run }: { run: RunDetail }) {
  const canRetry = useCan("runs.start");
  const canEditAgents = useCan("agents.manage");
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");
  const [inputs, setInputs] = useState(run.inputs ?? EMPTY_INPUTS);
  const clean = (i: typeof inputs) => ({ paths: i.paths.map((p) => p.trim()).filter(Boolean), notes: i.notes.trim() });
  const inputsDirty = JSON.stringify(clean(inputs)) !== JSON.stringify(clean(run.inputs ?? EMPTY_INPUTS));
  const retry = useMutation({
    mutationFn: async (fresh: boolean) => {
      if (inputsDirty) await api.updateRunInputs(run.id, clean(inputs));
      return api.retry(run.id, fresh, notes.trim() || undefined);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  // The failed attempt kept what it did (documents, and commits in its repository): a retry continues from it.
  const last = run.stages[run.stages.length - 1];
  const kept = !!last && last.status === "failed" && !!last.end_sha;
  const step = run.workflow_stages.find((s) => s.key === run.current_stage);

  return (
    <section className="space-y-3 rounded-lg border border-bad/40 bg-bad/5 p-4">
      <p className="text-sm text-bad">{run.error}</p>
      {canRetry && step?.kind === "agent" && (
        <>
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium text-subtle">Notes for {step.title} (optional): what to do differently this time</span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="e.g. Only document the create flow; the batch jobs belong to another feature. Stop exploring once the call chain reaches the DAO."
              className="w-full rounded-md border border-border bg-surface px-2 py-1.5"
            />
          </label>
          <details className="text-sm">
            <summary className="cursor-pointer text-subtle hover:text-fg">Change this run's inputs (where to look, notes for every step){inputsDirty ? " · changed" : ""}</summary>
            <div className="mt-2">
              <InputsEditor
                value={inputs}
                onChange={setInputs}
                pathsLabel="Where to look (this run)"
                notesLabel="Notes (this run)"
                pathsHint="One per line, relative to the repository root: code, documentation (docs/**/*.md), entry points."
              />
            </div>
          </details>
          {canEditAgents && (
            <p className="text-xs text-subtle">
              To change the agent itself (its instructions or guardrails),{" "}
              <Link to={`/agents/${step.key}`} className="text-accent hover:underline">
                edit {step.title}
              </Link>{" "}
              first; the retry uses it as it is then.
            </p>
          )}
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button
          className="rounded-md bg-fg px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
          disabled={!canRetry || retry.isPending}
          title={canRetry ? undefined : "Needs the “Run migrations” permission"}
          onClick={() => retry.mutate(false)}
        >
          {kept ? "Retry, continuing where it stopped" : "Retry from failed stage"}
        </button>
        {kept && (
          <button
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg disabled:opacity-50"
            disabled={!canRetry || retry.isPending}
            onClick={() => window.confirm("Undo what the failed attempt did and start the step over?") && retry.mutate(true)}
          >
            Retry from scratch
          </button>
        )}
        {kept && <span className="text-xs text-subtle">The failed attempt's documents and code are kept, so the retry carries on from them.</span>}
        {retry.error && <span className="text-sm text-bad">{(retry.error as Error).message}</span>}
      </div>
    </section>
  );
}
