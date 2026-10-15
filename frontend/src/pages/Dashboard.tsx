import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, runSubject, type Run } from "../lib/api";
import { useDomains, useWorkflows } from "../lib/hooks";
import { EMPTY_INPUTS, InputsEditor } from "../components/InputsEditor";
import { ProgressStrip } from "../components/ProgressStrip";
import { StatusBadge } from "../components/StatusBadge";
import { DomainAvatar } from "../components/DomainAvatar";
import { PageTitle } from "../components/PageTitle";
import { ActivityIcon, CheckIcon, ClockIcon, LayersIcon, MessageIcon } from "../components/icons";
import { Guarded } from "../components/Guarded";

/** emphasis: text colour for the count when something is waiting on a person. */
function StatTile({ label, value, icon, tone, emphasis }: { label: string; value: number; icon: React.ReactNode; tone: string; emphasis?: string }) {
  return (
    <div className="card flex items-center gap-3 p-4">
      <span className={`flex h-10 w-10 items-center justify-center rounded-lg ${tone}`}>{icon}</span>
      <div>
        <p className={`text-2xl font-semibold tabular-nums leading-none ${emphasis ?? ""}`}>{value}</p>
        <p className="mt-1 text-xs text-subtle">{label}</p>
      </div>
    </div>
  );
}

function NewRunForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const { data: domains = [] } = useDomains();
  const { data: allWorkflows = [] } = useWorkflows();
  const workflows = allWorkflows.filter((w) => w.scope === "operation");
  const [chosen, setChosen] = useState<string | null>(params.get("domain"));
  const domain = chosen ?? domains[0]?.key ?? "";
  // The workflow follows the domain's default until the user picks one.
  const [chosenWorkflow, setChosenWorkflow] = useState<string | null>(null);
  const domainDefault = domains.find((d) => d.key === domain)?.default_workflow;
  const workflow =
    chosenWorkflow ?? (workflows.some((w) => w.key === domainDefault) ? domainDefault! : (workflows.find((w) => w.key === "standard") ?? workflows[0])?.key ?? "");
  const { data: features } = useQuery({ queryKey: ["features", domain], queryFn: () => api.features(domain), enabled: !!domain });
  const [chosenFeature, setChosenFeature] = useState<string | null>(params.get("feature"));
  const available = (features ?? []).filter((f) => !f.migration);
  const operation = chosenFeature && available.some((f) => f.key === chosenFeature) ? chosenFeature : (available[0]?.key ?? "");
  const feature = features?.find((f) => f.key === operation);
  const [showInputs, setShowInputs] = useState(false);
  const [inputs, setInputs] = useState(EMPTY_INPUTS);
  const create = useMutation({
    mutationFn: () => {
      const paths = inputs.paths.map((p) => p.trim()).filter(Boolean);
      const notes = inputs.notes.trim();
      return api.createRun({ domain, operation, workflow, inputs: paths.length || notes ? { paths, notes } : undefined });
    },
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["features", domain] });
      if (params.has("feature")) setParams({}, { replace: true });
      navigate(`/runs/${run.id}`);
    },
  });

  return (
    <Guarded permission="runs.start">
      <form
        className="card flex flex-wrap items-end gap-3 border-t-4 border-t-accent p-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Domain</span>
          <select
            value={domain}
            onChange={(e) => {
              setChosen(e.target.value);
              setChosenWorkflow(null);
              setChosenFeature(null);
            }}
            className="rounded-md border border-border bg-bg px-2 py-1.5"
          >
            {domains.map((d) => (
              <option key={d.key} value={d.key}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-48 flex-1 text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Feature</span>
          {features && features.length === 0 ? (
            <span className="block py-1.5 text-sm text-warn">
              No features listed yet.{" "}
              <Link to={`/domains/${domain}?tab=features`} className="text-accent hover:underline">
                List them first →
              </Link>
            </span>
          ) : (
            <select value={operation} onChange={(e) => setChosenFeature(e.target.value)} className="w-full rounded-md border border-border bg-bg px-2 py-1.5">
              {available.length === 0 && <option value="">Every feature is already migrating</option>}
              {(features ?? []).map((f) => (
                <option key={f.key} value={f.key} disabled={!!f.migration}>
                  {f.name}
                  {f.migration ? (f.migration.status === "completed" ? " (migrated)" : " (in progress)") : ""}
                </option>
              ))}
            </select>
          )}
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Workflow</span>
          <select value={workflow} onChange={(e) => setChosenWorkflow(e.target.value)} className="rounded-md border border-border bg-bg px-2 py-1.5">
            {workflows.map((w) => (
              <option key={w.key} value={w.key}>
                {w.name}
                {w.key === domainDefault ? " (domain default)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => setShowInputs(!showInputs)}
          aria-expanded={showInputs}
          className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg"
        >
          {showInputs ? "Hide agent inputs" : "Add entry points & notes"}
        </button>
        <button type="submit" disabled={!domain || !workflow || !operation || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Start migration
        </button>
        {feature && feature.entry_points.length > 0 && (
          <p className="w-full text-xs text-subtle">
            Agents start from the feature's entry points: <span className="font-mono">{feature.entry_points.join(", ")}</span>
            {showInputs ? ". Entry points you add below replace them." : "."}
          </p>
        )}
        {showInputs && (
          <div className="w-full">
            <InputsEditor
              value={inputs}
              onChange={setInputs}
              pathsLabel="Entry points and files for this feature"
              notesLabel="Notes for the agents"
              notesHint="Given to every agent in this run, alongside the domain's inputs."
            />
          </div>
        )}
        {create.error && <p className="w-full text-sm text-bad">{(create.error as Error).message}</p>}
      </form>
    </Guarded>
  );
}

export function Dashboard() {
  const { data: domains = [] } = useDomains();
  const { data: runs = [], isLoading } = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 3000 });

  const migrations = runs.filter((r) => r.scope === "operation");
  const discovery = runs.filter((r) => r.scope !== "operation").sort((a, b) => b.created_at.localeCompare(a.created_at));
  const byDomain = new Map<string, Run[]>();
  for (const d of domains) byDomain.set(d.key, []);
  for (const r of migrations) byDomain.set(r.domain, [...(byDomain.get(r.domain) ?? []), r]);
  const domainName = (key: string) => domains.find((d) => d.key === key)?.name ?? key;
  const waiting = runs.filter((r) => r.status === "awaiting_approval" || r.status === "changes_requested").length;
  const needsInput = runs.filter((r) => r.status === "awaiting_input").length;
  const completed = runs.filter((r) => r.status === "completed").length;

  return (
    <div className="space-y-6">
      <PageTitle icon={<LayersIcon />} title="Migrations" subtitle="Move the monolith to microservices, one domain feature at a time." />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Runs" value={runs.length} icon={<ActivityIcon />} tone="bg-accent/15 text-accent" />
        <StatTile label="Completed" value={completed} icon={<CheckIcon />} tone="bg-ok/15 text-ok" />
        <StatTile label="Awaiting approval" value={waiting} icon={<ClockIcon />} tone="bg-warn/15 text-warn" emphasis={waiting > 0 ? "text-warn" : undefined} />
        <StatTile label="Need your input" value={needsInput} icon={<MessageIcon />} tone="bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300" emphasis={needsInput > 0 ? "text-fuchsia-600 dark:text-fuchsia-300" : undefined} />
      </div>

      <NewRunForm />

      {isLoading ? (
        <p className="text-subtle">Loading…</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {[...byDomain].map(([domain, domainRuns]) => {
            const done = domainRuns.filter((r) => r.status === "completed").length;
            const total = Math.max(domains.find((d) => d.key === domain)?.features ?? 0, domainRuns.length);
            const pct = total ? Math.round((100 * done) / total) : 0;
            return (
              <section key={domain} className="card overflow-hidden">
                <header className="flex items-center gap-3 border-b border-border px-4 py-3">
                  <DomainAvatar name={domainName(domain)} domainKey={domain} />
                  <Link to={`/domains/${domain}`} className="flex-1 font-semibold hover:text-accent">
                    {domainName(domain)}
                  </Link>
                  <span className="text-right text-xs text-subtle">
                    {total ? `${done}/${total} features migrated` : "No features listed"}
                    <span className="mt-1 block h-1.5 w-28 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-gradient-to-r from-ok to-emerald-400" style={{ width: `${pct}%` }} />
                    </span>
                  </span>
                </header>
                {domainRuns.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-subtle">
                    No migrations started.{" "}
                    <Link to={`/domains/${domain}?tab=features`} className="text-accent hover:underline">
                      Features →
                    </Link>
                  </p>
                ) : (
                  <ul className="divide-y divide-border">
                    {domainRuns.map((r) => (
                      <li key={r.id}>
                        <Link to={`/runs/${r.id}`} className="block space-y-2 px-4 py-3 hover:bg-muted">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate font-medium">{r.operation}</span>
                            <StatusBadge status={r.status} />
                          </div>
                          <ProgressStrip stages={r.workflow_stages} current={r.current_stage} status={r.status} />
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}

      {discovery.length > 0 && (
        <section className="card overflow-hidden">
          <header className="border-b border-border px-4 py-3">
            <h2 className="font-semibold">Discovery</h2>
            <p className="text-xs text-subtle">
              Bounded contexts and feature lists, from the{" "}
              <Link to="/domains" className="text-accent hover:underline">
                Domains
              </Link>{" "}
              page.
            </p>
          </header>
          <ul className="divide-y divide-border">
            {discovery.map((r) => (
              <li key={r.id}>
                <Link to={`/runs/${r.id}`} className="flex items-center justify-between gap-2 px-4 py-2.5 hover:bg-muted">
                  <span className="truncate text-sm font-medium">{runSubject(r)}</span>
                  <StatusBadge status={r.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
