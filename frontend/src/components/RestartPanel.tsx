import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { useCan } from "../lib/hooks";

/** The next version of a feature branch's name: feature/x -> feature/x-v2, feature/x-v2 -> feature/x-v3. */
export function nextBranch(branch: string): string {
  const m = branch.match(/^(.*)-v(\d+)$/);
  return m ? `${m[1]}-v${Number(m[2]) + 1}` : `${branch}-v2`;
}

const RESTARTABLE = ["completed", "failed", "awaiting_approval", "changes_requested", "awaiting_commit", "awaiting_branches", "awaiting_publish"];

/** "Re-run from here" (a flex item of the step header; the open panel takes a full row): run a completed step and
 * the steps after it again with the agents' current settings, either updating their documents or starting fresh. */
export function RestartPanel({ run, stage, stages, completed }: { run: RunDetail; stage: StageDef; stages: StageDef[]; completed: boolean }) {
  const queryClient = useQueryClient();
  const canRun = useCan("runs.start");
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const [fresh, setFresh] = useState(false);
  // A fresh re-run on a new branch from the latest base, once the run has a repository checked out.
  const canCut = !!run.branch && (!!run.base_sha || !!run.repositories?.some((r) => r.base_sha));
  const [cut, setCut] = useState(false);
  const [newBranch, setNewBranch] = useState(() => nextBranch(run.branch ?? ""));
  const onNewBranch = fresh && canCut && cut;
  const restart = useMutation({
    mutationFn: () => api.restart(run.id, stage.key, notes.trim() || undefined, fresh, onNewBranch ? newBranch.trim() : undefined),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["run", run.id] }); // closes once the page shows it running
      setOpen(false);
      setNotes("");
    },
  });
  if (!canRun || stage.kind !== "agent" || !completed || !RESTARTABLE.includes(run.status)) return null;

  const at = stages.findIndex((s) => s.key === stage.key);
  const later = stages.slice(at + 1).filter((s) => s.kind === "agent");
  const gates = stages.slice(at + 1).filter((s) => s.kind === "gate").length;

  if (!open)
    return (
      <button
        onClick={() => {
          setNewBranch(nextBranch(run.branch ?? "")); // from the run's branch now (a cut may have moved it)
          setCut(false);
          setOpen(true);
        }}
        className="ml-auto rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50"
        title="Run this step and the ones after it again with the agents' current settings"
      >
        ↻ Re-run from here
      </button>
    );

  return (
    <section className="basis-full space-y-3 rounded-lg border border-accent/40 bg-accent/5 p-4" aria-label="Re-run from this step">
      <h3 className="font-semibold">Re-run from {stage.title}</h3>
      <p className="text-sm text-subtle">
        {stage.title}
        {later.length ? ` and the ${later.length} step${later.length === 1 ? "" : "s"} after it (${later.map((s) => s.title).join(", ")})` : ""} run again
        with the agents' <strong>current</strong> instructions and settings.
        {gates > 0 && ` The ${gates} approval gate${gates === 1 ? "" : "s"} on the way ask for approval again.`}
        {run.status === "awaiting_approval" && " The approval waiting now is replaced."}
      </p>
      <fieldset className="grid gap-2 sm:grid-cols-2">
        <legend className="sr-only">How to re-run</legend>
        {[
          {
            value: false,
            title: "Update existing documents",
            text: "Recommended for small changes. Each step starts from what it already wrote and changes or adds only what's needed.",
          },
          {
            value: true,
            title: "Start these steps fresh",
            text: "For big changes. The workspace goes back to where this step started and the documents are regenerated. Earlier attempts stay in the history. Code these steps already pushed isn't brought back from git; their next commit undoes it on the feature branch (anything others pushed there stays).",
          },
        ].map((o) => (
          <label
            key={o.title}
            className={`flex cursor-pointer gap-2 rounded-md border p-3 text-sm ${fresh === o.value ? "border-accent bg-surface ring-1 ring-accent" : "border-border bg-surface hover:border-accent/50"}`}
          >
            <input type="radio" name={`restart-mode-${stage.key}`} className="mt-1" checked={fresh === o.value} onChange={() => setFresh(o.value)} />
            <span>
              <span className="block font-medium">{o.title}</span>
              <span className="block text-xs text-subtle">{o.text}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {fresh && canCut && (
        <fieldset className="space-y-2 rounded-md border border-border bg-surface p-3 text-sm">
          <legend className="px-1 text-xs font-medium text-subtle">Which branch</legend>
          <label className="flex cursor-pointer gap-2">
            <input type="radio" name={`restart-branch-${stage.key}`} className="mt-1" checked={!cut} onChange={() => setCut(false)} />
            <span>
              <span className="block font-medium">
                Continue on <span className="font-mono text-xs">{run.branch}</span>
              </span>
              <span className="block text-xs text-subtle">The next commit undoes the code pushed before; what teammates pushed there stays.</span>
            </span>
          </label>
          <label className="flex cursor-pointer gap-2">
            <input type="radio" name={`restart-branch-${stage.key}`} className="mt-1" checked={cut} onChange={() => setCut(true)} />
            <span className="min-w-0 flex-1">
              <span className="block font-medium">New branch from the latest base branch</span>
              <span className="block text-xs text-subtle">
                Every repository the run uses is cut again from the latest of its base branch, and the code of the steps before {stage.title} is
                replayed onto it (a replay that no longer applies stops the re-run). {run.branch} stays in git as it is, with its pull request; open
                a new one for the new branch.
              </span>
              {cut && (
                <input
                  aria-label="New branch"
                  className="mt-2 w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs"
                  value={newBranch}
                  onChange={(e) => setNewBranch(e.target.value)}
                />
              )}
            </span>
          </label>
        </fieldset>
      )}
      <label className="block text-sm">
        <span className="mb-1 block text-xs font-medium text-subtle">Notes for {stage.title} (optional): what changed, what to update</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="e.g. The agent now also documents batch jobs; add them, keep the rest."
          className="w-full rounded-md border border-border bg-surface px-2 py-1.5"
        />
      </label>
      {restart.error && <p className="text-sm text-bad">{(restart.error as Error).message}</p>}
      <div className="flex justify-end gap-2">
        <button className="rounded-md px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50" disabled={restart.isPending} onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          disabled={restart.isPending || (onNewBranch && !newBranch.trim())}
          onClick={() => restart.mutate()}
        >
          {restart.isPending ? "Starting…" : `${fresh ? "Start fresh" : "Re-run"} ${later.length + 1} step${later.length ? "s" : ""}`}
        </button>
      </div>
    </section>
  );
}
