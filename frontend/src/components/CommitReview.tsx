import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ChangedFile, type ChangedFileDetail, type RepoDelivery, type Resolution, type RunChanges, type RunDetail, type RunRepository } from "../lib/api";
import { useCan } from "../lib/hooks";
import { fold, hasMarkers, parseConflicts, resolve, sideBySide, type Choice, type Row } from "../lib/lineDiff";
import { BranchIcon } from "./icons";

const STATUS: Record<string, { label: string; className: string }> = {
  A: { label: "A", className: "text-ok" },
  M: { label: "M", className: "text-warn" },
  D: { label: "D", className: "text-bad" },
};

/** Side-by-side diff with unchanged stretches folded. */
export function DiffView({ before, after, leftTitle, rightTitle }: { before: string | null; after: string | null; leftTitle: string; rightTitle: string }) {
  const rows = useMemo(() => sideBySide(before, after), [before, after]);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const shown = useMemo(() => fold(rows, 3, open), [rows, open]);
  if (!rows.some((r) => r.kind !== "same")) return <p className="p-4 text-sm text-subtle">No differences.</p>;
  const cell = (side: { n: number; text: string } | undefined, tone: string) => (
    <>
      <td className={`w-10 select-none border-r border-border px-2 text-right text-subtle ${tone}`}>{side?.n ?? ""}</td>
      <td className={`whitespace-pre-wrap break-all px-2 ${tone}`}>{side?.text ?? ""}</td>
    </>
  );
  const tones: Record<Exclude<Row["kind"], "skip">, [string, string]> = {
    same: ["", ""],
    del: ["bg-bad/10", "bg-muted/40"],
    add: ["bg-muted/40", "bg-ok/10"],
    mod: ["bg-bad/10", "bg-ok/10"],
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed border-collapse font-mono text-xs leading-5">
        <colgroup>
          <col className="w-10" />
          <col />
          <col className="w-10" />
          <col />
        </colgroup>
        <thead>
          <tr className="border-b border-border text-left text-[11px] font-medium text-subtle">
            <th colSpan={2} className="px-2 py-1">
              {leftTitle}
            </th>
            <th colSpan={2} className="border-l border-border px-2 py-1">
              {rightTitle}
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) =>
            row.kind === "skip" ? (
              <tr key={`skip-${row.from}`}>
                <td colSpan={4} className="bg-muted/50 px-2 py-0.5 text-center">
                  <button type="button" className="text-accent hover:underline" onClick={() => setOpen(new Set([...open, row.from]))}>
                    Show {row.count} unchanged lines
                  </button>
                </td>
              </tr>
            ) : (
              <tr key={i} className="align-top">
                {cell("left" in row ? row.left : undefined, tones[row.kind][0])}
                <td className={`w-10 select-none border-x border-border px-2 text-right text-subtle ${tones[row.kind][1]}`}>{"right" in row ? row.right.n : ""}</td>
                <td className={`whitespace-pre-wrap break-all px-2 ${tones[row.kind][1]}`}>{"right" in row ? row.right.text : ""}</td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}

interface ResolverState {
  choices: Choice[];
  manual: string | null; // edited by hand: replaces the choices
}

/** Resolving one file's conflicts: per conflict, take this run's side, the base branch's, or both; or edit by hand. */
function ConflictResolver({ detail, baseBranch, state, onChange }: { detail: ChangedFileDetail; baseBranch: string; state: ResolverState; onChange: (s: ResolverState, r: Resolution | null) => void }) {
  const segments = useMemo(() => parseConflicts(detail.merged ?? ""), [detail.merged]);
  const conflicts = segments.filter((s) => s.kind === "conflict");
  const assembled = resolve(segments, state.choices, baseBranch);
  const result = state.manual ?? assembled;
  const update = (next: ResolverState) => {
    const text = next.manual ?? resolve(segments, next.choices, baseBranch);
    onChange(next, hasMarkers(text) ? null : { content: text });
  };
  const choose = (i: number, c: Choice) => update({ ...state, choices: conflicts.map((_, k) => (k === i ? c : (state.choices[k] ?? null))) });
  const chooseAll = (c: Choice) => update({ manual: null, choices: conflicts.map(() => c) });
  const button = (active: boolean) => `rounded px-2 py-0.5 text-xs ${active ? "bg-accent text-white" : "border border-border bg-surface hover:border-accent/50"}`;
  let c = -1;
  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">
          {conflicts.length} conflict{conflicts.length === 1 ? "" : "s"} between this run and {baseBranch}.
        </span>
        <span className="text-subtle">For all:</span>
        <button type="button" className={button(false)} onClick={() => chooseAll("ours")}>
          Use this run's
        </button>
        <button type="button" className={button(false)} onClick={() => chooseAll("theirs")}>
          Use {baseBranch}'s
        </button>
        <button type="button" className={`ml-auto ${button(state.manual !== null)}`} onClick={() => update({ ...state, manual: state.manual === null ? assembled : null })}>
          {state.manual === null ? "Edit the result by hand" : "Back to choosing per conflict"}
        </button>
      </div>
      {state.manual !== null ? (
        <textarea
          aria-label={`Merged ${detail.path}`}
          className="h-96 w-full rounded-md border border-border bg-bg p-2 font-mono text-xs"
          spellCheck={false}
          value={state.manual}
          onChange={(e) => update({ ...state, manual: e.target.value })}
        />
      ) : (
        <div className="space-y-2 font-mono text-xs">
          {segments.map((s, i) => {
            if (s.kind === "text") {
              const lines = s.text.split("\n");
              const shown = lines.length > 8 ? [...lines.slice(0, 3), `… ${lines.length - 6} unchanged lines …`, ...lines.slice(-3)] : lines;
              return (
                <pre key={i} className="whitespace-pre-wrap break-all px-2 text-subtle">
                  {shown.join("\n")}
                </pre>
              );
            }
            c += 1;
            const k = c;
            const choice = state.choices[k] ?? null;
            return (
              <div key={i} className={`rounded-md border ${choice ? "border-ok/50" : "border-bad/60"}`}>
                <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-muted/50 px-2 py-1 font-sans">
                  <span className={`mr-auto text-xs font-medium ${choice ? "text-ok" : "text-bad"}`}>
                    Conflict {k + 1}
                    {choice ? " · resolved" : ""}
                  </span>
                  <button type="button" className={button(choice === "ours")} onClick={() => choose(k, "ours")}>
                    This run's
                  </button>
                  <button type="button" className={button(choice === "theirs")} onClick={() => choose(k, "theirs")}>
                    {baseBranch}'s
                  </button>
                  <button type="button" className={button(choice === "both")} onClick={() => choose(k, "both")}>
                    Both (run's first)
                  </button>
                  <button type="button" className={button(choice === "both-reversed")} onClick={() => choose(k, "both-reversed")}>
                    Both ({baseBranch}'s first)
                  </button>
                </div>
                <div className="grid sm:grid-cols-2">
                  <div className="border-border sm:border-r">
                    <div className="px-2 pt-1 font-sans text-[11px] text-subtle">This run</div>
                    <pre className={`min-h-6 whitespace-pre-wrap break-all px-2 pb-2 ${choice === "theirs" ? "opacity-40" : "bg-ok/5"}`}>{s.ours || "(nothing)"}</pre>
                  </div>
                  <div>
                    <div className="px-2 pt-1 font-sans text-[11px] text-subtle">{baseBranch}</div>
                    <pre className={`min-h-6 whitespace-pre-wrap break-all px-2 pb-2 ${choice === "ours" ? "opacity-40" : "bg-accent/5"}`}>{s.theirs || "(nothing)"}</pre>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p className={`text-xs ${hasMarkers(result) ? "text-bad" : "text-ok"}`}>
        {hasMarkers(result) ? "Unresolved: choose a side for every conflict, or edit the result so no conflict markers remain." : "Resolved: this is what will be committed."}
      </p>
    </div>
  );
}

/** Conflicts that aren't about lines: one side deleted the file, or it's binary. */
function WholeFileChoice({ file, baseBranch, resolution, onChange }: { file: ChangedFileDetail; baseBranch: string; resolution: Resolution | undefined; onChange: (r: Resolution) => void }) {
  const options: { label: string; value: Resolution }[] =
    file.conflict === "deleted_here"
      ? [
          { label: "Keep it deleted (this run)", value: { delete: true } },
          { label: `Keep ${baseBranch}'s changed version`, value: { use: "theirs" } },
        ]
      : file.conflict === "deleted_there"
        ? [
            { label: "Keep this run's version", value: { use: "ours" } },
            { label: `Delete it (as ${baseBranch} did)`, value: { delete: true } },
          ]
        : [
            { label: "Use this run's version", value: { use: "ours" } },
            { label: `Use ${baseBranch}'s version`, value: { use: "theirs" } },
          ];
  const explain =
    file.conflict === "deleted_here"
      ? `This run deleted the file, but ${baseBranch} changed it since.`
      : file.conflict === "deleted_there"
        ? `This run changed the file, but ${baseBranch} deleted it since.`
        : `Both this run and ${baseBranch} changed this file, and it can't be merged line by line.`;
  return (
    <div className="space-y-2 p-3 text-sm">
      <p>{explain}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => {
          const active = JSON.stringify(o.value) === JSON.stringify(resolution);
          return (
            <button
              key={o.label}
              type="button"
              onClick={() => onChange(o.value)}
              className={`rounded-md px-3 py-1.5 text-sm ${active ? "bg-accent text-white" : "border border-border bg-surface hover:border-accent/50"}`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

type View = "changes" | "started" | "base" | "merge";

function FileView({ runId, changes, file, resolution, resolver, onResolve }: {
  runId: string; // the repository is changes.key
  changes: RunChanges;
  file: ChangedFile;
  resolution: Resolution | undefined;
  resolver: ResolverState;
  onResolve: (r: Resolution | null, s?: ResolverState) => void;
}) {
  const { data: detail, error, isLoading } = useQuery({
    // Per version of the run's work and of the branch in git: a later step's review never shows an earlier diff.
    queryKey: ["runChangeFile", runId, changes.key, file.path, changes.latest_sha, changes.head_sha, changes.remote_branch],
    queryFn: () => api.runChangeFile(runId, changes.key, file.path, changes.latest_sha),
    staleTime: Infinity,
  });
  const [view, setView] = useState<View>(file.conflict ? "merge" : "changes");
  useEffect(() => setView(file.conflict ? "merge" : "changes"), [file.path, file.conflict]);
  const tab = (v: View, label: string) => (
    <button type="button" onClick={() => setView(v)} className={`rounded-full px-3 py-1 text-xs ${view === v ? "bg-accent/15 font-medium text-accent" : "text-subtle hover:text-fg"}`}>
      {label}
    </button>
  );
  const base = changes.base_branch;
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <code className="mr-auto break-all text-sm font-medium">{file.path}</code>
        {tab("changes", detail?.in_git ? "Changes to the branch in git" : "This run's changes")}
        {detail?.in_git && tab("started", "Since the run started")}
        {file.upstream_changed && tab("base", `${base}'s changes since`)}
        {file.upstream_changed && tab("merge", file.conflict ? "Resolve conflict" : "Merged result")}
      </div>
      {isLoading && <p className="p-4 text-sm text-subtle">Loading…</p>}
      {error && <p className="p-4 text-sm text-bad">{(error as Error).message}</p>}
      {detail &&
        (detail.binary || detail.too_large ? (
          view === "merge" && detail.conflict ? (
            <WholeFileChoice file={detail} baseBranch={base} resolution={resolution} onChange={(r) => onResolve(r)} />
          ) : (
            <p className="p-4 text-sm text-subtle">{detail.binary ? "Binary file" : "This file is too large to show"}; it's committed as a whole.</p>
          )
        ) : view === "changes" && detail.in_git ? (
          // The run pushed its feature branch: show what this commit changes on top of it.
          <DiffView
            before={detail.pushed}
            after={detail.ours}
            leftTitle={`${changes.feature_branch} in git${detail.pushed === null ? " (not there yet)" : ""}`}
            rightTitle="This run"
          />
        ) : view === "changes" || view === "started" ? (
          <DiffView before={detail.original} after={detail.ours} leftTitle="Where the run started" rightTitle="This run" />
        ) : view === "base" ? (
          <DiffView before={detail.original} after={detail.theirs} leftTitle="Where the run started" rightTitle={`${base} now`} />
        ) : detail.conflict === "content" ? (
          <ConflictResolver detail={detail} baseBranch={base} state={resolver} onChange={(s, r) => onResolve(r, s)} />
        ) : detail.conflict ? (
          <WholeFileChoice file={detail} baseBranch={base} resolution={resolution} onChange={(r) => onResolve(r)} />
        ) : (
          <DiffView before={detail.theirs} after={detail.merged ?? detail.ours} leftTitle={`${base} now`} rightTitle="What will be committed" />
        ))}
    </div>
  );
}

/** One repository of the review: every changed file, conflicts with its latest base branch, commit and push. */
function RepoReview({ run, repo, onCommitted }: { run: RunDetail; repo: RunRepository; onCommitted: () => void }) {
  const queryClient = useQueryClient();
  const canCommit = useCan("runs.approve");
  const { data: changes, error, isFetching, refetch } = useQuery({
    queryKey: ["runChanges", run.id, repo.key, run.stages.length],
    queryFn: () => api.runChanges(run.id, repo.key),
    staleTime: Infinity,
    retry: false,
  });
  const { data: account } = useQuery({ queryKey: ["accountGit"], queryFn: api.accountGit });
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, Resolution>>({});
  const [resolvers, setResolvers] = useState<Record<string, ResolverState>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  // A new comparison (e.g. fetched again) starts over: code ticked, documents not, conflicts unresolved.
  useEffect(() => {
    if (!changes) return;
    // Code the run changed, not what is already on the branch in git as it is (unticked files stay as they are in git).
    setSelected(new Set(changes.files.filter((f) => !f.document && !f.same_in_git).map((f) => f.path)));
    setActive((a) =>
      a && changes.files.some((f) => f.path === a)
        ? a
        : (changes.files.find((f) => f.conflict) ?? changes.files.find((f) => !f.document && !f.same_in_git) ?? changes.files.find((f) => !f.document) ?? changes.files[0])?.path ?? null,
    );
    setResolutions({});
    setResolvers({});
  }, [changes]);

  const commit = useMutation({
    mutationFn: () =>
      api.commitRun(run.id, {
        repo: repo.key,
        message: (message ?? changes!.message).trim(),
        branch: (branch ?? changes!.feature_branch).trim(),
        files: [...selected!],
        resolutions: Object.fromEntries(Object.entries(resolutions).filter(([p]) => selected!.has(p))),
        latest_sha: changes!.latest_sha,
        head_sha: changes!.head_sha,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      onCommitted();
    },
  });

  if (error)
    return (
      <section className="space-y-2 p-4">
        <p className="text-sm text-bad">{(error as Error).message}</p>
        <button type="button" className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm" onClick={() => refetch()}>
          Try again
        </button>
      </section>
    );
  if (!changes || !selected)
    return (
      <section className="p-4 text-sm text-subtle">Fetching {repo.base_branch ?? "the base branch"} of {repo.name} and comparing the run's changes…</section>
    );

  const q = filter.trim().toLowerCase();
  const files = changes.files.filter((f) => !q || f.path.toLowerCase().includes(q));
  const code = changes.files.filter((f) => !f.document);
  const docs = changes.files.filter((f) => f.document);
  const unresolved = changes.files.filter((f) => f.conflict && selected.has(f.path) && !resolutions[f.path]);
  const current = changes.files.find((f) => f.path === active);
  const toggle = (paths: string[], on: boolean) => {
    const next = new Set(selected);
    paths.forEach((p) => (on ? next.add(p) : next.delete(p)));
    setSelected(next);
  };
  const noIdentity = account && !account.email;
  const finalBranch = branch ?? changes.feature_branch;

  return (
    <section>
      <div className="space-y-2 border-b border-border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="truncate font-mono text-xs text-subtle" title={changes.repository}>
            {changes.repository}
          </span>
          <span className="flex items-center gap-1.5 font-mono text-xs text-subtle">
            <BranchIcon className="h-4 w-4" />
            {changes.feature_branch} ← {changes.base_branch} @ {changes.base_sha.slice(0, 8)}
          </span>
          <button type="button" className="ml-auto rounded-md px-2 py-1 text-xs text-accent hover:bg-muted disabled:opacity-50" disabled={isFetching} onClick={() => refetch()}>
            {isFetching ? "Fetching…" : `Fetch ${changes.base_branch} and compare again`}
          </button>
        </div>
        <p className="text-sm text-subtle">
          Check the {changes.files.length} file{changes.files.length === 1 ? "" : "s"} this run changed here, choose what to commit, and push the feature branch.
          Your commit goes on top of the latest {changes.base_branch}
          {changes.behind > 0 ? `, which has ${changes.behind} new commit${changes.behind === 1 ? "" : "s"} since the run started` : ""}
          {changes.previous ? `, after the commit pushed earlier (${changes.previous.sha?.slice(0, 8)})` : ""}.
          {!!changes.unchanged_in_git && (
            <>
              {" "}
              {changes.unchanged_in_git} other file{changes.unchanged_in_git === 1 ? " is" : "s are"} already in git as {changes.unchanged_in_git === 1 ? "it is" : "they are"} here (on{" "}
              {changes.feature_branch} or {changes.base_branch}), so
              {changes.unchanged_in_git === 1 ? " it isn't" : " they aren't"} listed.
            </>
          )}
        </p>
        {changes.files.length === 0 && (
          <p className="rounded-md border border-border bg-surface px-3 py-2 text-sm">Nothing new to commit here: everything this run changed is already in git, on {changes.feature_branch} or {changes.base_branch}.</p>
        )}
        {changes.conflicts > 0 && (
          <p className="text-sm text-bad">
            {changes.conflicts} file{changes.conflicts === 1 ? "" : "s"} conflict with {changes.base_branch}: resolve them before committing.
          </p>
        )}
        {changes.warning && <p className="text-sm text-warn">{changes.warning}</p>}
      </div>

      <div className="grid min-h-[28rem] lg:grid-cols-[20rem_1fr]">
        <div className="border-b border-border lg:border-b-0 lg:border-r">
          <div className="space-y-2 border-b border-border p-2">
            <input
              aria-label="Filter files"
              placeholder="Filter files…"
              className="w-full rounded-md border border-border bg-bg px-2 py-1 text-sm"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={code.length > 0 && code.every((f) => selected.has(f.path))} onChange={(e) => toggle(code.map((f) => f.path), e.target.checked)} />
                Code ({code.length})
              </label>
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={docs.length > 0 && docs.every((f) => selected.has(f.path))} onChange={(e) => toggle(docs.map((f) => f.path), e.target.checked)} />
                Documents ({docs.length})
              </label>
            </div>
          </div>
          <ul className="max-h-[32rem] overflow-y-auto py-1 text-sm" aria-label="Changed files">
            {files.map((f) => {
              const s = STATUS[f.status] ?? { label: f.status, className: "text-subtle" };
              const slash = f.path.lastIndexOf("/");
              return (
                <li key={f.path} className={`flex items-center gap-2 px-2 py-1 ${f.path === active ? "bg-accent/10" : "hover:bg-muted"}`}>
                  <input type="checkbox" aria-label={`Commit ${f.path}`} checked={selected.has(f.path)} onChange={(e) => toggle([f.path], e.target.checked)} />
                  <button type="button" onClick={() => setActive(f.path)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <span className={`w-3 shrink-0 font-mono text-xs font-bold ${s.className}`}>{s.label}</span>
                    <span className="min-w-0 flex-1 truncate" title={f.path}>
                      {slash >= 0 && <span className="text-subtle">{f.path.slice(0, slash + 1)}</span>}
                      {f.path.slice(slash + 1)}
                    </span>
                    {f.conflict ? (
                      resolutions[f.path] ? (
                        <span className="text-xs text-ok" title="Conflict resolved">
                          ✓
                        </span>
                      ) : (
                        <span className="rounded bg-bad/15 px-1 text-[10px] font-bold text-bad" title="Conflict">
                          !
                        </span>
                      )
                    ) : f.same_in_git ? (
                      <span className="text-[10px] text-subtle" title={`Already on ${changes.feature_branch} in git as it is here: nothing new to commit`}>
                        in git
                      </span>
                    ) : f.upstream_changed ? (
                      <span className="text-[10px] text-accent" title={`Also changed on ${changes.base_branch}; merged cleanly`}>
                        merged
                      </span>
                    ) : null}
                    {f.additions !== null && (
                      <span className="shrink-0 font-mono text-[10px]">
                        <span className="text-ok">+{f.additions}</span> <span className="text-bad">−{f.deletions}</span>
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
            {files.length === 0 && <li className="px-3 py-2 text-subtle">No files match.</li>}
          </ul>
        </div>
        {current ? (
          <FileView
            runId={run.id}
            changes={changes}
            file={current}
            resolution={resolutions[current.path]}
            resolver={resolvers[current.path] ?? { choices: [], manual: null }}
            onResolve={(r, s) => {
              if (s) setResolvers({ ...resolvers, [current.path]: s });
              const next = { ...resolutions };
              if (r) next[current.path] = r;
              else delete next[current.path];
              setResolutions(next);
            }}
          />
        ) : (
          <p className="p-4 text-sm text-subtle">The run didn't change any files.</p>
        )}
      </div>

      <form
        className="space-y-3 border-t border-border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          commit.mutate();
        }}
      >
        <div className="grid gap-3 md:grid-cols-[1fr_20rem]">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-subtle">Commit message</span>
            <textarea rows={3} className="w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs" value={message ?? changes.message} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-subtle">Push to branch</span>
            <input aria-label="Push to branch" className="w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-xs" value={finalBranch} onChange={(e) => setBranch(e.target.value.trim())} />
            <span className="mt-1 block text-xs text-subtle">
              {changes.remote_branch ? `The repository has this branch at ${changes.remote_branch.slice(0, 8)}.` : "A new branch in"} {!changes.remote_branch && <span className="break-all font-mono">{changes.repository}</span>}
            </span>
          </label>
        </div>
        <p className="text-xs text-subtle">
          {account?.email ? (
            <>
              Committing {selected.size} file{selected.size === 1 ? "" : "s"} as{" "}
              <strong>
                {account.name || account.default_name} &lt;{account.email}&gt;
              </strong>
              , pushing with your access token for the repository's host, or the server's own git access if you haven't saved one.{" "}
              {changes.remote_branch && <>Files you leave out stay as they are on {changes.feature_branch} in git. </>}
            </>
          ) : null}
          <Link to="/account" className="text-accent hover:underline">
            {noIdentity ? "Set your git name and email first →" : "Change in My settings"}
          </Link>
        </p>
        {unresolved.length > 0 && <p className="text-sm text-bad">Resolve the conflicts in {unresolved.map((f) => f.path).join(", ")}, or untick them.</p>}
        {commit.error && <p className="text-sm text-bad">{(commit.error as Error).message}</p>}
        {canCommit ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={selected.size === 0 || unresolved.length > 0 || !!noIdentity || !finalBranch || commit.isPending}
              className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {commit.isPending ? "Committing and pushing…" : `Commit & push ${repo.name}`}
            </button>
          </div>
        ) : (
          <p className="text-sm text-subtle">Someone who can approve workflow runs commits these changes.</p>
        )}
      </form>
    </section>
  );
}

/** The last step of a run that changed code: a tab per repository it changed, each reviewed (every changed file,
 * conflicts with its latest base branch) and committed and pushed with the user's own git identity and access. */
export function CommitReview({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const canCommit = useCan("runs.approve");
  const { data: repos, error, refetch } = useQuery({ queryKey: ["runRepositories", run.id, run.stages.length], queryFn: () => api.runRepositories(run.id) });
  // Right after a step: just the repository it changed. At the end: every repository with something to commit,
  // and those already pushed.
  const afterStep = run.pending_commit;
  const changed = (repos ?? []).filter((r) =>
    afterStep ? r.key === afterStep.repository.key : r.pending || r.delivery?.status === "pushed",
  );
  const [active, setActive] = useState<string | null>(null);
  const current = changed.find((r) => r.key === active) ?? changed.find((r) => r.pending) ?? changed[0];
  const finish = useMutation({
    mutationFn: () => api.finishRun(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", run.id] }),
  });
  const left = changed.filter((r) => r.pending);
  return (
    <section className="card border-t-4 border-t-violet-500">
      <div className="space-y-2 border-b border-border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">Review & commit</h2>
          <span className="flex items-center gap-1.5 font-mono text-xs text-subtle">
            <BranchIcon className="h-4 w-4" />
            {run.branch}
          </span>
        </div>
        <p className="text-sm text-subtle">
          {afterStep ? (
            <>
              <strong className="text-fg">{afterStep.stage_title}</strong> changed code in {afterStep.repository.name}. Review the changes and commit them before the workflow
              goes on, or continue and commit later.
            </>
          ) : (
            <>The workflow has finished. {changed.length > 1 ? `Review and commit each repository with changes.` : "Review the changes and commit them."}</>
          )}
        </p>
        {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
      </div>
      {changed.length > 1 && (
        <div className="flex flex-wrap gap-1 border-b border-border px-3 pt-2" role="tablist" aria-label="Repositories">
          {changed.map((r) => (
            <button
              key={r.key}
              role="tab"
              aria-selected={r.key === current?.key}
              onClick={() => setActive(r.key)}
              className={`-mb-px rounded-t-md border px-3 py-1.5 text-sm ${r.key === current?.key ? "border-border border-b-surface bg-surface font-medium" : "border-transparent text-subtle hover:text-fg"}`}
            >
              {r.name}
              {r.delivery?.status === "pushed" && !r.pending && <span className="ml-1 text-ok"> ✓ pushed</span>}
              {r.delivery?.status === "skipped" && <span className="ml-1 text-subtle"> left out</span>}
            </button>
          ))}
        </div>
      )}
      {current &&
        (current.delivery?.status === "pushed" && !current.pending ? (
          <RepoDone name={current.name} d={current.delivery} />
        ) : (
          <RepoReview key={`${current.key}:${run.current_stage ?? "end"}:${run.stages.length}`} run={run} repo={current} onCommitted={() => refetch()} />
        ))}
      {repos && changed.length === 0 && <p className="p-4 text-sm text-subtle">The run didn't change any repository.</p>}
      {canCommit && (
        <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-3 text-sm">
          {finish.error && <span className="text-bad">{(finish.error as Error).message}</span>}
          <button
            type="button"
            disabled={finish.isPending}
            className="rounded-md px-3 py-1.5 text-subtle hover:bg-muted hover:text-fg"
            onClick={() =>
              afterStep
                ? finish.mutate()
                : confirm(
                    left.length < changed.length
                      ? `Finish the run without committing ${left.map((r) => r.name).join(", ")}?`
                      : "Finish this run without committing its changes to git? You can still re-run a step later.",
                  ) && finish.mutate()
            }
          >
            {afterStep
              ? "Continue without committing (commit later)"
              : left.length < changed.length
                ? `Finish without committing ${left.map((r) => r.name).join(", ")}`
                : "Finish without committing"}
          </button>
        </div>
      )}
    </section>
  );
}

function RepoDone({ name, d }: { name: string; d: RepoDelivery }) {
  return (
    <p className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
      <BranchIcon className="h-4 w-4 text-ok" />
      <span>
        {d.by} committed {d.files?.length ?? 0} file{d.files?.length === 1 ? "" : "s"} in {name} as <code className="text-xs">{d.sha?.slice(0, 10)}</code> and pushed{" "}
        <code className="text-xs">{d.branch}</code>
        {d.at ? `, ${new Date(d.at).toLocaleString()}` : ""}.
      </span>
      {d.compare_url && (
        <a href={d.compare_url} target="_blank" rel="noreferrer" className="ml-auto rounded-md bg-accent px-3 py-1 text-xs font-medium text-white">
          Open a pull request into {d.base_branch} ↗
        </a>
      )}
    </p>
  );
}

/** What was committed to each repository, on a finished run. */
export function DeliverySummary({ run }: { run: RunDetail }) {
  const repos = Object.entries(run.delivery?.repos ?? {});
  if (repos.length === 0) return null;
  return (
    <section className="card divide-y divide-border border-l-4 border-l-ok">
      {repos.map(([key, d]) =>
        d.status === "pushed" ? (
          <RepoDone key={key} name={d.name ?? key} d={d} />
        ) : (
          <p key={key} className="px-4 py-3 text-sm text-subtle">
            {d.name ?? key}: finished without committing{d.by ? ` (${d.by}` : ""}
            {d.at ? `, ${new Date(d.at).toLocaleString()})` : d.by ? ")" : ""}.
          </p>
        ),
      )}
    </section>
  );
}
