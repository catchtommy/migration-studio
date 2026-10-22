import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useIsAdmin } from "../lib/hooks";
import { DangerDialog } from "./DangerDialog";

const WORKING = ["running", "awaiting_input"];

/** Admin only, for clean-ups (on the domain's feature list): set a started feature back to not started. Every
 * migration run of it is deleted with everything recorded for it (one that is working is stopped first); the feature
 * stays, ready to migrate again. A warning spells out what happens, and the feature's key must be typed to go on. */
export function ResetFeature({ domain, feature, name, status }: { domain: string; feature: string; name: string; status?: string }) {
  const isAdmin = useIsAdmin();
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const reset = useMutation({
    mutationFn: () => api.resetFeature(domain, feature),
    onSuccess: () => {
      for (const key of [["runs"], ["domains"], ["features", domain], ["domainDocs", domain]]) queryClient.invalidateQueries({ queryKey: key });
      setOpen(false);
    },
  });
  if (!isAdmin) return null;
  const working = !!status && WORKING.includes(status);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Set this feature back to not started (deletes its runs)"
        className="rounded-md px-2 py-1 text-xs text-subtle hover:bg-bad/10 hover:text-bad"
      >
        Reset
      </button>
      {open && (
        <DangerDialog
          title={`Reset ${name} to not started`}
          sections={[
            {
              tone: "bad",
              title: "Deleted for good",
              items: [
                <>
                  Every migration run of <strong>{name}</strong>
                  {status ? ` (the latest is ${status.replace(/_/g, " ")})` : ""}
                </>,
                "All their steps, documents, approvals, questions and activity logs",
                "Their workspaces on the server and their saved progress",
                "The cross-domain dependencies their design recorded",
                "Contracts they proposed that no one has approved yet",
              ],
            },
            ...(working
              ? [{ tone: "warn" as const, title: "Stopped first", text: "A run of this feature is working right now. Its agent is stopped at its next step before anything is deleted." }]
              : []),
            {
              tone: "ok",
              title: "Kept",
              items: [
                "The feature itself (its entry points and tables), back to not started, ready to migrate again",
                "Contracts already approved in the catalogue, which other domains rely on",
              ],
            },
            {
              tone: "neutral",
              title: "Not undone in git",
              text: "Code already pushed to a feature branch and AS-IS documents published to the monolith stay there; remove them in git if needed.",
            },
          ]}
          confirmText={feature}
          actionLabel="Delete its runs and reset"
          pendingLabel={working ? "Stopping and deleting…" : "Deleting…"}
          pending={reset.isPending}
          error={reset.error}
          onConfirm={() => reset.mutate()}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
