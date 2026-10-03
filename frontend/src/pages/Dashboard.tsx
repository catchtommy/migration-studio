import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Run } from "../lib/api";
import { useDomains, useWorkflows } from "../lib/hooks";
import { EMPTY_INPUTS, InputsEditor } from "../components/InputsEditor";
import { ProgressStrip } from "../components/ProgressStrip";
import { StatusBadge } from "../components/StatusBadge";

function NewRunForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: domains = [] } = useDomains();
  const { data: workflows = [] } = useWorkflows();
  const [chosen, setChosen] = useState<string | null>(null);
  const domain = chosen ?? domains[0]?.key ?? "";
  // The workflow follows the domain's default until the user picks one.
  const [chosenWorkflow, setChosenWorkflow] = useState<string | null>(null);
  const domainDefault = domains.find((d) => d.key === domain)?.default_workflow;
  const workflow =
    chosenWorkflow ?? (workflows.some((w) => w.key === domainDefault) ? domainDefault! : (workflows.find((w) => w.key === "standard") ?? workflows[0])?.key ?? "");
  const [operation, setOperation] = useState("");
  const [showInputs, setShowInputs] = useState(false);
  const [inputs, setInputs] = useState(EMPTY_INPUTS);
  const create = useMutation({
    mutationFn: () => {
      const paths = inputs.paths.map((p) => p.trim()).filter(Boolean);
      const notes = inputs.notes.trim();
      return api.createRun({ domain, operation: operation.trim(), workflow, inputs: paths.length || notes ? { paths, notes } : undefined });
    },
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  return (
    <form
      className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-4"
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
        <span className="mb-1 block text-xs font-medium text-subtle">Operation</span>
        <input
          value={operation}
          onChange={(e) => setOperation(e.target.value)}
          placeholder="e.g. createCustomer"
          className="w-full rounded-md border border-border bg-bg px-2 py-1.5"
        />
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
      <button type="submit" disabled={!domain || !workflow || !operation.trim() || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
        Start migration
      </button>
      {showInputs && (
        <div className="w-full">
          <InputsEditor
            value={inputs}
            onChange={setInputs}
            pathsLabel="Entry points and files for this operation"
            notesLabel="Notes for the agents"
            notesHint="Given to every agent in this run, alongside the domain's inputs."
          />
        </div>
      )}
      {create.error && <p className="w-full text-sm text-bad">{(create.error as Error).message}</p>}
    </form>
  );
}

export function Dashboard() {
  const { data: domains = [] } = useDomains();
  const { data: runs = [], isLoading } = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 3000 });

  const byDomain = new Map<string, Run[]>();
  for (const d of domains) byDomain.set(d.key, []);
  for (const r of runs) byDomain.set(r.domain, [...(byDomain.get(r.domain) ?? []), r]);
  const domainName = (key: string) => domains.find((d) => d.key === key)?.name ?? key;
  const waiting = runs.filter((r) => r.status === "awaiting_approval").length;
  const needsInput = runs.filter((r) => r.status === "awaiting_input").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-semibold">Migrations</h1>
        <p className="text-sm text-subtle">
          {runs.length} runs · {runs.filter((r) => r.status === "completed").length} completed ·{" "}
          <span className={waiting ? "font-medium text-warn" : ""}>{waiting} awaiting approval</span>
          {needsInput > 0 && <span className="font-medium text-warn"> · {needsInput} need input</span>}
        </p>
      </div>

      <NewRunForm />

      {isLoading ? (
        <p className="text-subtle">Loading…</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {[...byDomain].map(([domain, domainRuns]) => {
            const done = domainRuns.filter((r) => r.status === "completed").length;
            return (
              <section key={domain} className="rounded-lg border border-border bg-surface">
                <header className="flex items-center justify-between border-b border-border px-4 py-3">
                  <Link to={`/domains/${domain}`} className="font-semibold hover:text-accent">
                    {domainName(domain)}
                  </Link>
                  <span className="text-xs text-subtle">
                    {done}/{domainRuns.length} operations migrated
                  </span>
                </header>
                {domainRuns.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-subtle">No operations started.</p>
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
    </div>
  );
}
