import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type RunBrief } from "../lib/api";
import { useDomains } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { GridIcon } from "../components/icons";
import { DomainAvatar } from "../components/DomainAvatar";
import { PATTERN_CELL, PATTERN_NAMES, PatternChip } from "../components/Chips";
import { StatusBadge } from "../components/StatusBadge";
import { EMPTY_INPUTS, InputsEditor } from "../components/InputsEditor";
import { AgentModeNote } from "../components/AgentModeNote";

const ACTIVE = ["pending", "running", "awaiting_input", "awaiting_approval", "changes_requested"];
const isActive = (run: RunBrief | null) => !!run && ACTIVE.includes(run.status);
const unfinished = (run: RunBrief | null) => !!run && run.status !== "completed";

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${done ? "bg-ok/15 text-ok" : "bg-accent/15 text-accent"}`}
      >
        {done ? "✓" : n}
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <h3 className="font-semibold">{title}</h3>
        {children}
      </div>
    </div>
  );
}

/** Before migrating: find the bounded contexts, then list each domain's features. */
function DiscoveryPanel() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["discovery"],
    queryFn: api.discovery,
    refetchInterval: (q) => (q.state.data && (isActive(q.state.data.bounded_contexts) || q.state.data.domains.some((d) => isActive(d.run))) ? 3000 : false),
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["discovery"] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
  };
  // Paths and notes for the bounded-contexts run (e.g. existing documentation); starts from the last run's.
  const [inputs, setInputs] = useState<typeof EMPTY_INPUTS | null>(null);
  const [setup, setSetup] = useState<"contexts" | "features" | null>(null);
  const shownInputs = inputs ?? data?.contexts_inputs ?? EMPTY_INPUTS;
  const given = { paths: shownInputs.paths.map((p) => p.trim()).filter(Boolean), notes: shownInputs.notes.trim() };
  const contexts = useMutation({
    mutationFn: () => api.discoverBoundedContexts(given.paths.length || given.notes ? given : undefined),
    onSuccess: () => {
      setSetup(null);
      refresh();
    },
  });
  const features = useMutation({
    mutationFn: api.discoverAllFeatures,
    onSuccess: () => {
      setSetup(null);
      refresh();
    },
  });
  if (!data) return null;

  const run = data.bounded_contexts;
  const listed = data.domains.filter((d) => d.features > 0).length;
  const listing = data.domains.filter((d) => isActive(d.run)).length;
  const missing = data.domains.filter((d) => d.features === 0 && !unfinished(d.run)).length;

  return (
    <section className="card space-y-4 border-t-4 border-t-accent p-4">
      <div>
        <h2 className="font-semibold">Discovery</h2>
        <p className="mt-0.5 text-sm text-subtle">Map the monolith before migrating it. Each step is a workflow run with an approval gate; approving it updates the domains below.</p>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <Step n={1} title="Find the bounded contexts" done={data.contexts_approved}>
          <p className="text-sm text-subtle">
            Groups the monolith's code and tables by business capability. Approving adds new contexts as domains and fills in their packages, tables and entry points; no
            domain is deleted.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {run && unfinished(run) ? (
              <Link to={`/runs/${run.id}`} className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted">
                Bounded contexts <StatusBadge status={run.status} /> <span className="text-accent">Open →</span>
              </Link>
            ) : (
              setup !== "contexts" && (
                <button onClick={() => setSetup("contexts")} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
                  {data.contexts_approved ? "Find them again…" : "Find bounded contexts…"}
                </button>
              )
            )}
            {run && !unfinished(run) && (
              <Link to={`/runs/${run.id}`} className="text-sm text-accent hover:underline">
                Last result
              </Link>
            )}
          </div>
          {run && unfinished(run) && (
            <p className="text-xs text-subtle">
              <em>Where to look & notes</em> is set when a run starts. Finish this one first: approve it, or reject it with comments naming the documentation to read
              (the agent re-runs with them).
            </p>
          )}
          {setup === "contexts" && !(run && unfinished(run)) && (
            <div role="region" className="space-y-3 rounded-md border border-border bg-bg p-3" aria-label="Set up bounded contexts">
              <AgentModeNote agentKey="bounded_contexts" />
              <InputsEditor
                value={shownInputs}
                onChange={setInputs}
                pathsLabel="Where to look"
                pathsHint="One per line, relative to the repository root: existing documentation (docs/**/*.md), an index file, key modules."
                notesHint="E.g. which folders to ignore, domains you already know, or that the docs may be out of date and the code wins."
              />
              <div className="flex items-center gap-2">
                <button onClick={() => contexts.mutate()} disabled={contexts.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
                  Start
                </button>
                <button onClick={() => setSetup(null)} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
                  Cancel
                </button>
              </div>
            </div>
          )}
          {contexts.error && <p className="text-sm text-bad">{(contexts.error as Error).message}</p>}
        </Step>
        <Step n={2} title="List each domain's features" done={data.domains.length > 0 && listed === data.domains.length}>
          <p className="text-sm text-subtle">
            {listed} of {data.domains.length} domains have a feature list
            {listing > 0 && `, ${listing} being listed`}. Migrations start from these lists; review each one at its gate.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {setup !== "features" && (
              <button onClick={() => setSetup("features")} disabled={missing === 0} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
                List features for {missing === data.domains.length ? "all" : missing} domain{missing === 1 ? "" : "s"}…
              </button>
            )}
            {!data.contexts_approved && <span className="text-xs text-warn">Best after step 1 is approved.</span>}
          </div>
          {setup === "features" && (
            <div role="region" className="space-y-3 rounded-md border border-border bg-bg p-3" aria-label="Set up feature listing">
              <AgentModeNote agentKey="domain_features" />
              <p className="text-sm text-subtle">
                Each domain's run uses that domain's <em>Domain features</em> inputs (where to look and notes). To set them first, open a domain's Features tab and
                use <strong>List features…</strong> there.
              </p>
              <div className="flex items-center gap-2">
                <button onClick={() => features.mutate()} disabled={features.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
                  Start for {missing} domain{missing === 1 ? "" : "s"}
                </button>
                <button onClick={() => setSetup(null)} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
                  Cancel
                </button>
              </div>
            </div>
          )}
          {features.error && <p className="text-sm text-bad">{(features.error as Error).message}</p>}
        </Step>
      </div>
    </section>
  );
}

function NewDomainForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => api.createDomain({ key, name: name.trim(), description }),
    onSuccess: (d) => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      navigate(`/domains/${d.key}?tab=features&setup=1`);
    },
  });

  return (
    <form
      className="space-y-3 card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h2 className="font-semibold">New domain</h2>
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <Field label="Name">
          <input
            className={inputClass}
            value={name}
            placeholder="e.g. Collateral"
            onChange={(e) => {
              setName(e.target.value);
              if (!keyEdited) setKey(slug(e.target.value, "-"));
            }}
          />
        </Field>
        <Field label="Key" hint="Used in branch names">
          <input
            className={`${inputClass} font-mono text-xs`}
            value={key}
            onChange={(e) => {
              setKeyEdited(true);
              setKey(e.target.value);
            }}
          />
        </Field>
      </div>
      <Field label="Description">
        <textarea rows={2} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={!name.trim() || !key || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Add domain
        </button>
        {create.error && <span className="text-sm text-bad">{(create.error as Error).message}</span>}
      </div>
    </form>
  );
}

/** Which domain depends on which (from approved integration designs), and the suggested migration order. */
function MigrationOverview({ names }: { names: Map<string, string> }) {
  const { data: map } = useQuery({ queryKey: ["dependencyMap"], queryFn: api.dependencyMap });
  if (!map) return null;
  const involved = map.domains.filter((d) => map.edges.some((e) => e.source === d || e.target === d));
  const edge = (source: string, target: string) => map.edges.find((e) => e.source === source && e.target === target);
  const name = (key: string) => names.get(key) ?? key;
  const dominant = (patterns: Record<string, number>) => Object.entries(patterns).sort((a, b) => b[1] - a[1])[0][0];

  return (
    <section className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="card overflow-x-auto p-4">
        <h2 className="font-semibold">Cross-domain dependencies</h2>
        <p className="mt-0.5 text-xs text-subtle">Rows depend on columns (call their API, use their events or data). From approved integration designs.</p>
        {involved.length === 0 ? (
          <p className="mt-4 text-sm text-subtle">None recorded yet. They appear once a migration's cross-domain integration design is approved.</p>
        ) : (
          <>
            <table className="mt-3 border-separate border-spacing-1 text-xs">
              <thead>
                <tr>
                  <th />
                  {involved.map((d) => (
                    <th key={d} className="px-1 pb-1 align-bottom font-medium text-subtle">
                      <span className="inline-flex flex-col items-center gap-1">
                        <DomainAvatar name={name(d)} domainKey={d} size="h-6 w-6 text-[11px]" />
                        {name(d)}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {involved.map((row) => (
                  <tr key={row}>
                    <th className="whitespace-nowrap pr-2 text-right font-medium">{name(row)}</th>
                    {involved.map((col) => {
                      const e = edge(row, col);
                      return (
                        <td
                          key={col}
                          title={e ? `${name(row)} → ${name(col)}: ${Object.entries(e.patterns).map(([p, n]) => `${n} ${p}`).join(", ")}` : undefined}
                          className={`h-9 min-w-9 rounded-md text-center font-semibold tabular-nums ${row === col ? "bg-muted/60" : e ? PATTERN_CELL[dominant(e.patterns)] : "bg-muted/30"}`}
                        >
                          {e?.count ?? ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {PATTERN_NAMES.map((p) => (
                <PatternChip key={p} pattern={p} />
              ))}
            </div>
          </>
        )}
      </div>
      <div className="card p-4">
        <h2 className="font-semibold">Suggested migration order</h2>
        <p className="mt-0.5 text-xs text-subtle">Domains others depend on synchronously the least go first.</p>
        <ol className="mt-3 space-y-2.5">
          {map.order.map((step, i) => (
            <li key={step.domain} className="flex gap-2.5 text-sm">
              <span className="mt-0.5 w-4 shrink-0 text-right text-xs tabular-nums text-subtle">{i + 1}</span>
              <DomainAvatar name={name(step.domain)} domainKey={step.domain} size="h-6 w-6 text-[11px]" />
              <span>
                <Link to={`/domains/${step.domain}?tab=dependencies`} className="font-medium hover:text-accent">
                  {name(step.domain)}
                </Link>
                <span className="block text-xs text-subtle">{step.reason}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

export function DomainsPage() {
  const { data: domains, isLoading } = useDomains();
  const { data: discovery } = useQuery({ queryKey: ["discovery"], queryFn: api.discovery });
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-6">
      <PageTitle icon={<GridIcon />} title="Domains" subtitle="Tell each domain's agents where to look, and read the documentation they produced." tone="from-sky-500 to-indigo-500">
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New domain
          </button>
        )}
      </PageTitle>
      {adding && <NewDomainForm />}
      <DiscoveryPanel />
      {domains && <MigrationOverview names={new Map(domains.map((d) => [d.key, d.name]))} />}
      {isLoading || !domains ? (
        <p className="text-subtle">Loading…</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {domains.map((d) => {
            const configured = Object.values(d.agent_inputs).filter((i) => i.paths.length || i.notes).length;
            const listing = discovery?.domains.find((x) => x.key === d.key)?.run;
            return (
              <Link key={d.key} to={`/domains/${d.key}`} className="card block space-y-3 p-4 transition hover:-translate-y-0.5 hover:border-accent/50 hover:shadow-md">
                <div className="flex items-center gap-3">
                  <DomainAvatar name={d.name} domainKey={d.key} />
                  <h2 className="flex-1 font-semibold">{d.name}</h2>
                  <code className="text-xs text-subtle">{d.key}</code>
                </div>
                {discovery?.not_found.includes(d.key) && (
                  <span
                    className="inline-block rounded-full bg-warn/15 px-2 py-0.5 text-xs font-medium text-warn"
                    title="The latest approved bounded contexts don't include this domain. Check whether it should stay."
                  >
                    Not found by discovery
                  </span>
                )}
                <p className="line-clamp-2 min-h-10 text-sm text-subtle">{d.description || "No description."}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-subtle">
                  <span className={d.features ? "" : "text-warn"}>
                    {d.features ? `${d.completed}/${d.features} features migrated` : isActive(listing ?? null) ? "Listing features…" : "No features listed"}
                  </span>
                  <span className={configured || d.context ? "" : "text-warn"}>
                    {configured || d.context ? `Inputs for ${configured} agent${configured === 1 ? "" : "s"}${d.context ? " + shared context" : ""}` : "No agent inputs yet"}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
