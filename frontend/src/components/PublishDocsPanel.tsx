import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type RunDetail } from "../lib/api";
import { useCan } from "../lib/hooks";
import { DiffView } from "./CommitReview";
import { BranchIcon } from "./icons";

const STATUS: Record<string, string> = { new: "text-ok", modified: "text-warn", unchanged: "text-subtle" };

/** The gate approved the AS-IS documents: publish them to the monolith's docs/<domain>/<feature>/ on its base branch
 * (as the user, by hand), so later agents read them instead of the code; or skip. */
export function PublishDocsPanel({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const canPublish = useCan("runs.approve");
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["docsPreview", run.id, run.current_stage],
    queryFn: () => api.docsPreview(run.id),
    staleTime: Infinity,
    retry: false,
  });
  const { data: account } = useQuery({ queryKey: ["accountGit"], queryFn: api.accountGit });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!data) return;
    setSelected(new Set(data.files.filter((f) => f.status !== "unchanged").map((f) => f.path)));
    setActive((a) => a ?? data.files[0]?.path ?? null);
  }, [data]);
  const done = () => queryClient.invalidateQueries({ queryKey: ["run", run.id] });
  const publish = useMutation({
    mutationFn: () => api.publishDocs(run.id, { message: (message ?? data!.message).trim(), paths: [...selected], latest_sha: data!.latest_sha }),
    onSuccess: done,
  });
  const skip = useMutation({ mutationFn: () => api.skipDocs(run.id), onSuccess: done });
  const current = data?.files.find((f) => f.path === active);
  const short = (path: string) => (data && path.startsWith(data.folder) ? path.slice(data.folder.length) : path);

  return (
    <section className="card border-t-4 border-t-violet-500">
      <div className="space-y-1 border-b border-border p-4">
        <h2 className="font-semibold">Publish AS-IS documentation to the monolith</h2>
        <p className="text-sm text-subtle">
          {run.pending_publish?.steps.join(" and ")} {run.pending_publish && run.pending_publish.steps.length > 1 ? "were" : "was"} approved. Publishing commits these documents
          to {data ? <code className="text-xs">{data.folder}</code> : "the docs folder"} on the monolith's{" "}
          {data ? <code className="text-xs">{data.branch}</code> : "base branch"}, so later agents read them instead of the code. The legacy code itself isn't touched.
        </p>
        {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
      </div>
      {!data && !error && <p className="p-4 text-sm text-subtle">Fetching the monolith and comparing…</p>}
      {data && (
        <>
          <div className="grid min-h-[20rem] lg:grid-cols-[18rem_1fr]">
            <ul className="border-b border-border py-1 text-sm lg:border-b-0 lg:border-r" aria-label="Documents to publish">
              {data.files.map((f) => (
                <li key={f.path} className={`flex items-center gap-2 px-2 py-1 ${f.path === active ? "bg-accent/10" : "hover:bg-muted"}`}>
                  <input
                    type="checkbox"
                    aria-label={`Publish ${short(f.path)}`}
                    checked={selected.has(f.path)}
                    onChange={(e) => {
                      const next = new Set(selected);
                      if (e.target.checked) next.add(f.path);
                      else next.delete(f.path);
                      setSelected(next);
                    }}
                  />
                  <button type="button" onClick={() => setActive(f.path)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <span className="min-w-0 flex-1 truncate" title={f.path}>
                      {short(f.path)}
                    </span>
                    {f.changed_upstream && (
                      <span className="text-[10px] text-warn" title="Changed in git since this run last published it; publishing replaces that change">
                        changed in git
                      </span>
                    )}
                    <span className={`text-[10px] ${STATUS[f.status]}`}>{f.status}</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="min-w-0">
              {current && (
                <>
                  <div className="border-b border-border px-3 py-2">
                    <code className="break-all text-sm font-medium">{current.path}</code>
                  </div>
                  {current.new === null ? (
                    <p className="p-4 text-sm text-subtle">This document is too large to show; it's published as a whole.</p>
                  ) : (
                    <DiffView before={current.old} after={current.new} leftTitle={`${data.branch} now`} rightTitle="Approved document" />
                  )}
                </>
              )}
            </div>
          </div>
          <form
            className="space-y-3 border-t border-border p-4"
            onSubmit={(e) => {
              e.preventDefault();
              publish.mutate();
            }}
          >
            <label className="block text-sm">
              <span className="mb-1 block text-xs font-medium text-subtle">Commit message</span>
              <textarea rows={3} className="w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs" value={message ?? data.message} onChange={(e) => setMessage(e.target.value)} />
            </label>
            <p className="flex flex-wrap items-center gap-1 text-xs text-subtle">
              <BranchIcon className="h-3.5 w-3.5" />
              Commits {selected.size} document{selected.size === 1 ? "" : "s"} directly to <code>{data.branch}</code> of <span className="break-all font-mono">{data.repository}</span>
              {account?.email ? (
                <>
                  {" "}
                  as{" "}
                  <strong>
                    {account.name || account.default_name} &lt;{account.email}&gt;
                  </strong>
                  .
                </>
              ) : (
                <Link to="/account" className="text-accent hover:underline">
                  {" "}
                  Set your git name and email first →
                </Link>
              )}
              <button type="button" className="ml-auto text-accent hover:underline disabled:opacity-50" disabled={isFetching} onClick={() => refetch()}>
                {isFetching ? "Fetching…" : "Compare again"}
              </button>
            </p>
            {(publish.error || skip.error) && <p className="text-sm text-bad">{((publish.error || skip.error) as Error).message}</p>}
            {canPublish ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="submit"
                  disabled={selected.size === 0 || !account?.email || publish.isPending}
                  className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {publish.isPending ? "Publishing…" : `Publish to ${data.branch}`}
                </button>
                <button type="button" disabled={skip.isPending} className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg" onClick={() => skip.mutate()}>
                  Skip for now
                </button>
              </div>
            ) : (
              <p className="text-sm text-subtle">Someone who can approve workflow runs publishes these documents.</p>
            )}
          </form>
        </>
      )}
    </section>
  );
}
