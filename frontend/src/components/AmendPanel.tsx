import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, MONOLITH, type RerunScope, type RunDetail } from "../lib/api";
import { RerunScopeChoice } from "./RerunScopeChoice";
import { EMPTY_INPUTS, InputsEditor } from "./InputsEditor";
import { Guarded } from "./Guarded";
import { useCan } from "../lib/hooks";

/** A rejection held back for amendments: change the agent, the inputs or the documents, then re-run. */
export function AmendPanel({ run }: { run: RunDetail }) {
  const canEditAgents = useCan("agents.manage");
  const queryClient = useQueryClient();
  const pending = run.pending_rerun!;
  const reviews = run.pending_gate?.reviews ?? [pending.target_stage];
  const title = (key: string) => run.workflow_stages.find((s) => s.key === key)?.title ?? key;
  const [comments, setComments] = useState(pending.comments ?? "");
  const [target, setTarget] = useState(pending.target_stage);
  const [scope, setScope] = useState<RerunScope>(pending.scope ?? "amend");
  const [inputs, setInputs] = useState(run.inputs ?? EMPTY_INPUTS);
  const clean = (i: typeof inputs) => ({ paths: i.paths.map((p) => p.trim()).filter(Boolean), notes: i.notes.trim() });
  const inputsDirty = JSON.stringify(clean(inputs)) !== JSON.stringify(clean(run.inputs ?? EMPTY_INPUTS));
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["run", run.id] });

  const saveInputs = useMutation({ mutationFn: () => api.updateRunInputs(run.id, clean(inputs)), onSuccess: refresh });
  const rerun = useMutation({
    mutationFn: async () => {
      if (inputsDirty) await api.updateRunInputs(run.id, clean(inputs));
      return api.rerun(run.id, { comments, target_stage: target, scope });
    },
    onSuccess: refresh,
  });
  const back = useMutation({ mutationFn: () => api.backToReview(run.id), onSuccess: refresh });
  const error = (saveInputs.error ?? rerun.error ?? back.error) as Error | null;

  return (
    <Guarded permission="runs.approve">
      <section className="space-y-4 rounded-lg border border-sky-500/40 bg-sky-500/5 p-4" aria-label="Amend before re-running">
        <div>
          <h2 className="font-semibold">✏️ Changes requested: amend, then re-run</h2>
          <p className="mt-1 text-sm text-subtle">
            {title(target)} re-runs with your comments once you click <strong>Re-run</strong>
            {scope === "amend" ? ", making only the change they ask for" : ", starting over from where it began"}. Until then nothing runs, so you can change:
          </p>
        </div>
        <ul className="grid gap-2 text-sm md:grid-cols-3">
          <li className="card p-3">
            <span className="font-medium">The agent</span>
            <span className="block text-xs text-subtle">Instructions (paste a new agent.md), tools, required documents.</span>
            {canEditAgents ? (
              <Link to={`/agents/${target}`} className="mt-1 inline-block text-accent hover:underline">
                Edit {title(target)} →
              </Link>
            ) : (
              <span className="mt-1 block text-xs text-subtle">Ask someone who manages agents to change it.</span>
            )}
          </li>
          <li className="card p-3">
            <span className="font-medium">The documents</span>
            <span className="block text-xs text-subtle">Edit any reviewed document in the timeline below; later stages build on your edits.</span>
          </li>
          <li className="card p-3">
            <span className="font-medium">{run.domain === MONOLITH ? "This run's inputs" : "Domain inputs"}</span>
            <span className="block text-xs text-subtle">Where to look and notes, for this run (below) or every run of the domain.</span>
            {run.domain !== MONOLITH && (
              <Link to={`/domains/${run.domain}?tab=inputs`} className="mt-1 inline-block text-accent hover:underline">
                Domain inputs →
              </Link>
            )}
          </li>
        </ul>

        <InputsEditor
          value={inputs}
          onChange={setInputs}
          pathsLabel="Where to look (this run)"
          notesLabel="Notes (this run)"
          pathsHint="One per line, relative to the repository root: code, documentation (docs/**/*.md), entry points."
        />
        {inputsDirty && (
          <button onClick={() => saveInputs.mutate()} disabled={saveInputs.isPending} className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted">
            Save inputs
          </button>
        )}

        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Comments for the agent (required)</span>
          <textarea value={comments} onChange={(e) => setComments(e.target.value)} rows={3} className="w-full rounded-md border border-border bg-surface px-2 py-1.5" />
        </label>
        <RerunScopeChoice value={scope} onChange={setScope} stepTitle={title(target)} />

        {error && <p className="text-sm text-bad">{error.message}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {reviews.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-subtle">
              Re-run from
              <select value={target} onChange={(e) => setTarget(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1.5 text-fg">
                {reviews.map((k) => (
                  <option key={k} value={k}>
                    {title(k)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button onClick={() => back.mutate()} disabled={back.isPending} className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg">
            Withdraw rejection
          </button>
          <button
            onClick={() => rerun.mutate()}
            disabled={!comments.trim() || rerun.isPending}
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Re-run {title(target)}
          </button>
        </div>
      </section>
    </Guarded>
  );
}
