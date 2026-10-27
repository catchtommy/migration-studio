import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ChecklistItem, type RerunScope, type StageDef } from "../lib/api";
import { RerunScopeChoice } from "./RerunScopeChoice";
import { useCan, useMe } from "../lib/hooks";

export function ApprovalPanel({
  runId,
  gate,
  stages,
}: {
  runId: string;
  gate: { gate: string; reviews: string[]; checklist?: ChecklistItem[] };
  stages: StageDef[];
}) {
  const queryClient = useQueryClient();
  const { data: auth } = useMe();
  const canApprove = useCan("runs.approve");
  const [comments, setComments] = useState("");
  const [target, setTarget] = useState(gate.reviews[0]);
  const [scope, setScope] = useState<RerunScope>("amend");
  // Approving shows the checklist; rejecting shows the re-run choices. Neither is shown until the reviewer picks one.
  const [mode, setMode] = useState<"choose" | "approve" | "reject">("choose");
  const title = (key: string) => stages.find((s) => s.key === key)?.title ?? key;
  const checklist = gate.checklist ?? [];
  const open = checklist.filter((c) => !c.done).length;

  const tick = useMutation({
    mutationFn: (body: { item: string; done: boolean }) => api.tickChecklist(runId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  });

  const decide = useMutation({
    mutationFn: ({ decision, rerun }: { decision: "approve" | "reject"; rerun?: "now" | "later" }) =>
      api.decide(runId, {
        decision,
        comments: comments || undefined,
        target_stage: decision === "reject" ? target : undefined,
        rerun,
        scope: decision === "reject" ? scope : undefined,
      }),
    onSuccess: () => {
      setComments("");
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
  });

  return (
    <section className="rounded-lg border border-warn/40 bg-warn/5 p-4">
      <h2 className="font-semibold">👤 {title(gate.gate)}</h2>
      <p className="mt-1 text-sm text-subtle">
        Review the output of {gate.reviews.map(title).join(", ")} in the timeline. You can edit artifacts before approving. To change the agent, its inputs or
        the documents before the re-run, choose <em>Reject…</em>, then <em>Reject, amend first</em>.
      </p>

      {!canApprove ? (
        <>
          <p className="mt-4 text-sm text-subtle">Waiting for someone who can approve workflow runs. You can still read the documents in the timeline.</p>
          {checklist.length > 0 && (
            <p className="mt-2 text-sm text-subtle">
              Checklist: {checklist.length - open} of {checklist.length} done.
            </p>
          )}
        </>
      ) : (
        <>
          {mode === "choose" && (
            <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
              <button className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10" onClick={() => setMode("reject")}>
                Reject…
              </button>
              <button className="rounded-md bg-ok px-3 py-1.5 text-sm font-medium text-white" onClick={() => setMode("approve")}>
                Approve…
              </button>
            </div>
          )}

          {mode === "approve" && checklist.length > 0 && (
            <div role="group" aria-labelledby="approval-checklist" className="mt-4 rounded-md border border-border bg-surface p-3 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <h3 id="approval-checklist" className="font-medium">
                  Checklist
                </h3>
                <span className={`text-xs ${open > 0 ? "text-warn" : "text-ok"}`}>
                  {checklist.length - open} of {checklist.length} done
                </span>
              </div>
              {open > 0 && <p className="mt-0.5 text-xs text-subtle">Tick every item to approve.</p>}
              <ul className="mt-2 space-y-1.5 border-t border-border pt-2">
                {checklist.map((c) => (
                  <li key={c.item}>
                    <label className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={c.done}
                        disabled={tick.isPending}
                        onChange={(e) => tick.mutate({ item: c.item, done: e.target.checked })}
                      />
                      <span className={c.done ? "text-subtle line-through" : ""}>{c.item}</span>
                      {c.done && c.by && (
                        <span className="ml-auto shrink-0 text-xs text-subtle" title={c.at ? new Date(c.at).toLocaleString() : undefined}>
                          {c.by}
                        </span>
                      )}
                    </label>
                  </li>
                ))}
              </ul>
              {tick.error && <p className="mt-2 text-sm text-bad">{(tick.error as Error).message}</p>}
            </div>
          )}

          {mode !== "choose" && (
            <>
              <label className="mt-4 block text-sm">
                <span className="mb-1 block text-xs font-medium text-subtle">
                  {mode === "reject" ? "What to change (required; sent to the agent)" : "Comments (optional)"} — recorded as {auth?.user?.display_name}
                </span>
                <textarea
                  value={comments}
                  onChange={(e) => setComments(e.target.value)}
                  rows={3}
                  className="w-full rounded-md border border-border bg-surface px-2 py-1.5"
                />
              </label>
              {mode === "reject" && (
                <div className="mt-3">
                  <RerunScopeChoice value={scope} onChange={setScope} stepTitle={title(target)} />
                </div>
              )}
              {decide.error && <p className="mt-2 text-sm text-bad">{(decide.error as Error).message}</p>}

              <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                {mode === "reject" && gate.reviews.length > 1 && (
                  <label className="flex items-center gap-2 text-sm text-subtle">
                    Re-run from
                    <select
                      value={target}
                      onChange={(e) => setTarget(e.target.value)}
                      className="rounded-md border border-border bg-surface px-2 py-1.5 text-fg"
                    >
                      {gate.reviews.map((k) => (
                        <option key={k} value={k}>
                          {title(k)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  className="rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-muted disabled:opacity-50"
                  disabled={decide.isPending}
                  onClick={() => {
                    setMode("choose");
                    decide.reset();
                  }}
                >
                  Cancel
                </button>
                {mode === "reject" ? (
                  <>
                    <button
                      className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
                      disabled={!comments.trim() || decide.isPending}
                      onClick={() => decide.mutate({ decision: "reject", rerun: "later" })}
                      title="Hold the run so you can change the agent, the inputs or the documents, then re-run when ready"
                    >
                      Reject, amend first
                    </button>
                    <button
                      className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
                      disabled={!comments.trim() || decide.isPending}
                      onClick={() => decide.mutate({ decision: "reject", rerun: "now" })}
                    >
                      Reject &amp; re-run now
                    </button>
                  </>
                ) : (
                  <button
                    className="rounded-md bg-ok px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                    disabled={decide.isPending || open > 0}
                    title={open > 0 ? `Tick the ${open} open checklist item${open === 1 ? "" : "s"} first` : undefined}
                    onClick={() => decide.mutate({ decision: "approve" })}
                  >
                    Approve
                  </button>
                )}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
