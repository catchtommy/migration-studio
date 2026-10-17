import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { useCan } from "../lib/hooks";

const RESTARTABLE = ["completed", "failed", "awaiting_approval", "changes_requested", "awaiting_commit", "awaiting_branches", "awaiting_publish"];

/** "Re-run from here" (a flex item of the step header; the open panel takes a full row): run a completed step and
 * the steps after it again with the agents' current settings, either updating their documents or starting fresh. */
export function RestartPanel({ run, stage, stages, completed }: { run: RunDetail; stage: StageDef; stages: StageDef[]; completed: boolean }) {
  const queryClient = useQueryClient();
  const canRun = useCan("runs.start");
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const [fresh, setFresh] = useState(false);
  const restart = useMutation({
    mutationFn: () => api.restart(run.id, stage.key, notes.trim() || undefined, fresh),
    onSuccess: () => {
      setOpen(false);
      setNotes("");
      queryClient.invalidateQueries({ queryKey: ["run", run.id] });
    },
  });
  if (!canRun || stage.kind !== "agent" || !completed || !RESTARTABLE.includes(run.status)) return null;

  const at = stages.findIndex((s) => s.key === stage.key);
  const later = stages.slice(at + 1).filter((s) => s.kind === "agent");
  const gates = stages.slice(at + 1).filter((s) => s.kind === "gate").length;

  if (!open)
    return (
      <button
        onClick={() => setOpen(true)}
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
            text: "For big changes. The workspace goes back to where this step started and the documents are regenerated. Earlier attempts stay in the history.",
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
        <button className="rounded-md px-3 py-1.5 text-sm hover:bg-muted" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          disabled={restart.isPending}
          onClick={() => restart.mutate()}
        >
          {fresh ? "Start fresh" : "Re-run"} {later.length + 1} step{later.length ? "s" : ""}
        </button>
      </div>
    </section>
  );
}
