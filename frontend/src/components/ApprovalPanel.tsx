import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RerunScope, type StageDef } from "../lib/api";
import { RerunScopeChoice } from "./RerunScopeChoice";
import { useCan, useMe } from "../lib/hooks";

export function ApprovalPanel({ runId, gate, stages }: { runId: string; gate: { gate: string; reviews: string[] }; stages: StageDef[] }) {
  const queryClient = useQueryClient();
  const { data: auth } = useMe();
  const canApprove = useCan("runs.approve");
  const [comments, setComments] = useState("");
  const [target, setTarget] = useState(gate.reviews[0]);
  const [scope, setScope] = useState<RerunScope>("amend");
  const title = (key: string) => stages.find((s) => s.key === key)?.title ?? key;

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
        the documents before the re-run, choose <em>Reject, amend first</em>.
      </p>

      {!canApprove ? (
        <p className="mt-4 text-sm text-subtle">Waiting for someone who can approve workflow runs. You can still read the documents in the timeline.</p>
      ) : (
        <>
          <label className="mt-4 block text-sm">
            <span className="mb-1 block text-xs font-medium text-subtle">
              Comments (required to reject; sent to the agent) — recorded as {auth?.user?.display_name}
            </span>
            <textarea
              value={comments}
              onChange={(e) => setComments(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-border bg-surface px-2 py-1.5"
            />
          </label>
          {comments.trim() && (
            <div className="mt-3">
              <RerunScopeChoice value={scope} onChange={setScope} stepTitle={title(target)} />
            </div>
          )}
          {decide.error && <p className="mt-2 text-sm text-bad">{(decide.error as Error).message}</p>}

          <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
            {gate.reviews.length > 1 && (
              <label className="flex items-center gap-2 text-sm text-subtle">
                Re-run from
                <select value={target} onChange={(e) => setTarget(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1.5 text-fg">
                  {gate.reviews.map((k) => (
                    <option key={k} value={k}>
                      {title(k)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
              disabled={!comments || decide.isPending}
              onClick={() => decide.mutate({ decision: "reject", rerun: "later" })}
              title="Hold the run so you can change the agent, the inputs or the documents, then re-run when ready"
            >
              Reject, amend first
            </button>
            <button
              className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
              disabled={!comments || decide.isPending}
              onClick={() => decide.mutate({ decision: "reject", rerun: "now" })}
            >
              Reject &amp; re-run now
            </button>
            <button
              className="rounded-md bg-ok px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ decision: "approve" })}
            >
              Approve
            </button>
          </div>
        </>
      )}
    </section>
  );
}
