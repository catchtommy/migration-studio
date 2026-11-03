import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { useCan } from "../lib/hooks";

const WORKING = ["running", "awaiting_input"];

// What each step builds on: updating a step suggests updating those built on it, and keeping one whose base is
// updated asks for a second look (the lifted service copies the module, the tests run against the service).
export const BUILDS_ON: Record<string, string[]> = {
  microservice_scaffold: ["modular_monolith"],
  independent_tests: ["microservice_scaffold"],
  modernise_and_test: ["microservice_scaffold", "independent_tests"],
  orchestrator_service: ["monolith_orchestrator"],
  orchestrator_tests: ["orchestrator_service"],
};

/** The steps after `key` built on it, directly or through others. */
function dependents(key: string): string[] {
  const out = new Set<string>();
  const walk = (k: string) =>
    Object.entries(BUILDS_ON).forEach(([step, bases]) => {
      if (bases.includes(k) && !out.has(step)) {
        out.add(step);
        walk(step);
      }
    });
  walk(key);
  return [...out];
}

/** "Change this step": an approved documents step (Analysis, Domain dependencies, Modulith design) makes one requested
 * change, and the run fixes forward from it. The already-run steps after it that are ticked update their work in
 * place; the others are kept as they are. Asked for while the run works, the working step stops at its next turn. */
export function ChangeStepPanel({ run, stage, stages }: { run: RunDetail; stage: StageDef; stages: StageDef[] }) {
  const queryClient = useQueryClient();
  const canStart = useCan("runs.start");
  const canApprove = useCan("runs.approve");
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const at = stages.findIndex((s) => s.key === stage.key);
  const completed = new Set(run.stages.filter((e) => e.status === "completed").map((e) => e.stage));
  const later = stages.slice(at + 1).filter((s) => s.kind === "agent" && completed.has(s.key));
  // Steps that run again whatever is ticked (as the server does): the working step, which is stopped, and those whose
  // work is no longer in effect (their latest attempt failed, or a step before them did, or started over)
  const latestFailed = (k: string) => {
    const mine = run.stages.filter((e) => e.stage === k).sort((a, b) => a.attempt - b.attempt);
    return mine[mine.length - 1]?.status === "failed";
  };
  const mustRun = new Set(
    later
      .filter((s) => latestFailed(s.key) || !!run.rerun_blocked?.[s.key] || (WORKING.includes(run.status) && s.key === run.current_stage))
      .map((s) => s.key),
  );
  const [follow, setFollow] = useState<string[]>(() => later.map((s) => s.key));
  const change = useMutation({
    mutationFn: () => api.changeStep(run.id, stage.key, notes.trim(), [...new Set([...follow, ...mustRun])]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      setOpen(false);
      setNotes("");
    },
  });
  if (!(canStart || canApprove) || !run.changeable?.[stage.key] || run.status === "pending") return null;
  if (run.pending_change)
    return run.pending_change.stage === stage.key ? (
      <span className="text-sm text-warn">Change requested by {run.pending_change.by}: waiting for the working step to stop…</span>
    ) : null;

  const title = (key: string) => stages.find((s) => s.key === key)?.title ?? key;
  const working = WORKING.includes(run.status);
  const toggle = (key: string, on: boolean) =>
    setFollow((f) => (on ? [...new Set([...f, key, ...dependents(key).filter((d) => later.some((s) => s.key === d))])] : f.filter((k) => k !== key)));
  // A kept step whose base updates: it may no longer match what it was built from
  const updating = new Set([...follow, ...mustRun]); // what runs again: ticked, or run again anyway
  const warnings = later
    .filter((s) => !updating.has(s.key) && (BUILDS_ON[s.key] ?? []).some((b) => updating.has(b)))
    .map((s) => `${s.title} builds on ${(BUILDS_ON[s.key] ?? []).filter((b) => updating.has(b)).map(title).join(", ")}, which updates: keep it as it is anyway?`);
  const gates = stages.slice(at + 1).filter((s) => s.kind === "gate");
  const asking = gates.filter((g) => !(g.reviews ?? []).length || (g.reviews ?? []).some((r) => r === stage.key || updating.has(r) || !completed.has(r)));

  if (!open)
    return (
      <button
        onClick={() => {
          setFollow(later.map((s) => s.key));
          setOpen(true);
        }}
        className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50"
        title="Make one change to this approved step, and carry it forward to the steps you choose"
      >
        ✎ Change this step
      </button>
    );

  return (
    <section className="basis-full space-y-3 rounded-lg border border-accent/40 bg-accent/5 p-4" aria-label="Change this step">
      <h3 className="font-semibold">Change {stage.title}</h3>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">What to change</span>
        <textarea
          rows={3}
          className="w-full rounded-md border border-border bg-bg px-2 py-1.5"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="For example: the approval flow also rejects amounts over the branch limit (missed in the analysis)"
        />
      </label>
      {later.length > 0 && (
        <fieldset className="space-y-1 text-sm">
          <legend className="mb-1 font-medium">Steps that already ran: update them too?</legend>
          {later.map((s) => (
            <label key={s.key} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={mustRun.has(s.key) || follow.includes(s.key)}
                disabled={mustRun.has(s.key)}
                onChange={(e) => toggle(s.key, e.target.checked)}
              />
              <span>{s.title}</span>
              {mustRun.has(s.key) ? (
                <span className="text-xs text-subtle">
                  {s.key === run.current_stage && WORKING.includes(run.status) ? "runs again: it is stopped for this change" : "runs again: its earlier work is no longer in effect"}
                </span>
              ) : (
                !follow.includes(s.key) && <span className="text-xs text-subtle">kept as it is</span>
              )}
            </label>
          ))}
          {warnings.map((w) => (
            <p key={w} className="text-xs text-warn">
              {w}
            </p>
          ))}
        </fieldset>
      )}
      <p className="text-sm text-subtle">
        {stage.title} makes only this change and is approved again.
        {updating.size > 0 && ` ${updating.size} step${updating.size === 1 ? "" : "s"} after it update${updating.size === 1 ? "s" : ""} their work in place.`}
        {later.length - updating.size > 0 && ` ${later.length - updating.size} kept as they are.`}
        {asking.length > 0 && ` ${asking.length} approval gate${asking.length === 1 ? "" : "s"} on the way ask again; a gate whose steps are all kept keeps its approval.`}
        {working && ` The working step (${title(run.current_stage ?? "")}) stops at its next turn first.`}
      </p>
      <MonolithNote run={run} />
      <div className="flex justify-end gap-2">
        <button className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          disabled={!notes.trim() || change.isPending}
          onClick={() => change.mutate()}
        >
          {change.isPending ? "Sending…" : working ? "Stop and change" : "Change and carry forward"}
        </button>
      </div>
      {change.error && <p className="text-sm text-bad">{String(change.error)}</p>}
    </section>
  );
}

/** A re-run or change reads the monolith as it was when the run started; say so when its base branch moved on. */
export function MonolithNote({ run }: { run: RunDetail }) {
  if (!run.monolith_behind) return null;
  return (
    <p className="text-xs text-warn">
      Works on the monolith as it was when this run started: its base branch has {run.monolith_behind} newer commit{run.monolith_behind === 1 ? "" : "s"}. To
      work from the latest code, re-run from a step with <em>Start fresh</em> on a new branch.
    </p>
  );
}

/** "Ran before X was changed": a kept step older than a later change to an earlier documents step. */
export function changedSince(run: RunDetail, stages: StageDef[], key: string): string | null {
  const latest = (k: string) =>
    run.stages
      .filter((e) => e.stage === k && e.status === "completed")
      .map((e) => e.started_at)
      .sort()
      .pop();
  const mine = latest(key);
  if (!mine) return null;
  const at = stages.findIndex((s) => s.key === key);
  const newer = stages
    .slice(0, at)
    .filter((s) => s.kind === "agent" && run.changeable?.[s.key])
    .filter((s) => {
      const t = latest(s.key);
      return t && t > mine;
    });
  return newer.length ? newer.map((s) => s.title).join(", ") : null;
}
