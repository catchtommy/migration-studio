import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type PendingBranches } from "../lib/api";
import { useCan } from "../lib/hooks";
import { BranchIcon } from "./icons";

/** A step is about to change code (every code step asks, each time it runs): confirm (or change) the feature branch,
 * and the base branch it is cut from when the repository isn't checked out yet. Says when the branch already exists. */
export function BranchConfirmation({ runId, pending }: { runId: string; pending: PendingBranches }) {
  const queryClient = useQueryClient();
  const canStart = useCan("runs.start");
  const repo = pending.repository;
  const [feature, setFeature] = useState(pending.feature_branch);
  const [base, setBase] = useState(repo.base_branch ?? "");
  const confirm = useMutation({
    mutationFn: () => api.confirmBranches(runId, { feature_branch: feature.trim(), ...(repo.checked_out ? {} : { base_branch: base.trim() || undefined }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const renamed = feature.trim() !== pending.feature_branch;
  return (
    <form
      className="card space-y-3 border-t-4 border-t-violet-500 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        confirm.mutate();
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <BranchIcon className="h-5 w-5 text-violet-600" />
        <h2 className="font-semibold">Confirm branches for {repo.name}</h2>
      </div>
      <p className="text-sm text-subtle">
        <strong className="text-fg">{pending.stage_title}</strong> is about to change code in <span className="font-mono text-xs">{repo.url}</span>. Check where the
        changes go before it starts.
      </p>
      <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Base branch</span>
          {repo.checked_out ? (
            <span className="block py-1.5 font-mono text-xs" title="This repository is already checked out for the run">
              {repo.base_branch ?? repo.default_branch ?? "default branch"} <span className="font-sans text-subtle">(already cut)</span>
            </span>
          ) : (
            <input
              aria-label="Base branch"
              className="w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs"
              placeholder={repo.default_branch ?? "default branch"}
              value={base}
              onChange={(e) => setBase(e.target.value)}
            />
          )}
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Feature branch</span>
          <input aria-label="Feature branch" className="w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs" value={feature} onChange={(e) => setFeature(e.target.value)} />
          {renamed && <span className="mt-1 block text-xs text-subtle">Renames the run's feature branch in every repository it uses.</span>}
        </label>
      </div>
      {pending.existing_branch &&
        (renamed ? (
          <p className="text-xs text-subtle">Whether {feature.trim() || "the new name"} already exists is checked when you confirm.</p>
        ) : (
          <p role="status" className="rounded-md border border-accent/40 bg-accent/5 px-3 py-2 text-sm">
            <span className="font-medium">An existing branch already exists.</span> {pending.existing_branch.message}
          </p>
        ))}
      {confirm.error && <p className="text-sm text-bad">{(confirm.error as Error).message}</p>}
      {canStart ? (
        <button type="submit" disabled={!feature.trim() || confirm.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {confirm.isPending ? "Checking…" : `Confirm and start ${pending.stage_title}`}
        </button>
      ) : (
        <p className="text-sm text-subtle">Someone who can run migrations confirms the branches.</p>
      )}
    </form>
  );
}
