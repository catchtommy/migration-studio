import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, FEATURES_OP, type Agent, type AgentInputs, type Dependency, type Domain } from "../lib/api";
import { PatternChip } from "../components/Chips";
import { useAgents, useDomains, useWorkflows } from "../lib/hooks";
import { ArtifactViewer } from "../components/ArtifactViewer";
import { DownloadButton } from "../components/DownloadButton";
import { chosen, DocumentPicker, type DocumentChoice } from "../components/DocumentPicker";
import { Field, inputClass } from "../components/Field";
import { EMPTY_INPUTS, InputsEditor } from "../components/InputsEditor";
import { StatusBadge } from "../components/StatusBadge";
import { DomainAvatar } from "../components/DomainAvatar";
import { DomainFeatures } from "./DomainFeatures";
import { Guarded } from "../components/Guarded";

type Tab = "features" | "docs" | "inputs" | "boundary" | "dependencies" | "details";
const TABS: { key: Tab; label: string }[] = [
  { key: "features", label: "Features" },
  { key: "docs", label: "Documentation" },
  { key: "inputs", label: "Agent inputs" },
  { key: "boundary", label: "Boundary" },
  { key: "dependencies", label: "Dependencies" },
  { key: "details", label: "Details" },
];

const opLabel = (operation: string) => (operation === FEATURES_OP ? "Feature list" : operation);
const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

/** Tables and Java packages this domain owns: given to agents and enforced by the boundary check. */
function Boundary({ domain }: { domain: Domain }) {
  const queryClient = useQueryClient();
  const [tables, setTables] = useState((domain.owned_tables ?? []).join("\n"));
  const [packages, setPackages] = useState((domain.owned_packages ?? []).join("\n"));
  const dirty = lines(tables).join() !== (domain.owned_tables ?? []).join() || lines(packages).join() !== (domain.owned_packages ?? []).join();
  const save = useMutation({
    mutationFn: () => api.updateDomain(domain.key, { owned_tables: lines(tables), owned_packages: lines(packages) }),
    onSuccess: (d) => {
      queryClient.setQueryData(["domain", domain.key], d);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
  });
  return (
    <Guarded permission="domains.manage">
      <form
        className="max-w-3xl space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="text-sm text-subtle">
          What belongs to {domain.name}. Agents use this to tell domains apart, and the boundary check fails another domain's microservice if it uses these
          packages or tables directly instead of going through a contract.
        </p>
        <div className="card grid gap-4 p-4 md:grid-cols-2">
          <Field label="Owned tables" hint="One per line, e.g. CUSTOMER, CUST_ADDRESS">
            <textarea rows={8} className={`${inputClass} font-mono text-xs`} value={tables} onChange={(e) => setTables(e.target.value)} />
          </Field>
          <Field label="Owned Java packages" hint="One per line, e.g. com.acme.customer">
            <textarea rows={8} className={`${inputClass} font-mono text-xs`} value={packages} onChange={(e) => setPackages(e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" disabled={!dirty || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Save boundary
          </button>
          {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
          {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved</span>}
          {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
        </div>
      </form>
    </Guarded>
  );
}

function DependencyTable({ title, rows, other, names }: { title: string; rows: Dependency[]; other: (d: Dependency) => string; names: Map<string, string> }) {
  return (
    <section className="card overflow-x-auto">
      <h3 className="border-b border-border px-4 py-3 font-semibold">{title}</h3>
      {rows.length === 0 ? (
        <p className="px-4 py-4 text-sm text-subtle">None recorded.</p>
      ) : (
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-subtle">
            <tr>
              <th className="px-4 py-2 font-medium">Domain</th>
              <th className="px-4 py-2 font-medium">Interaction</th>
              <th className="px-4 py-2 font-medium">Pattern</th>
              <th className="px-4 py-2 font-medium">Contract</th>
              <th className="px-4 py-2 font-medium">Operation</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} className="border-t border-border align-top">
                <td className="px-4 py-2">
                  <Link to={`/domains/${other(d)}?tab=dependencies`} className="inline-flex items-center gap-1.5 hover:text-accent">
                    <DomainAvatar name={names.get(other(d)) ?? other(d)} domainKey={other(d)} size="h-5 w-5 text-[10px]" />
                    {names.get(other(d)) ?? other(d)}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  {d.summary || d.interaction}
                  <span className="block text-xs text-subtle">
                    {d.kind}
                    {d.status !== "planned" && ` · ${d.status}`}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <PatternChip pattern={d.pattern} />
                </td>
                <td className="px-4 py-2">{d.contract ? <code className="text-xs">{d.contract}</code> : <span className="text-subtle">–</span>}</td>
                <td className="px-4 py-2">
                  <Link to={`/runs/${d.run_id}`} className="text-accent hover:underline">
                    {d.operation ?? "run"}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Dependencies({ domain }: { domain: Domain }) {
  const { data: rows, isLoading } = useQuery({ queryKey: ["dependencies", domain.key], queryFn: () => api.domainDependencies(domain.key) });
  const { data: domains = [] } = useDomains();
  const names = new Map(domains.map((d) => [d.key, d.name]));
  if (isLoading || !rows) return <p className="text-sm text-subtle">Loading…</p>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-subtle">
        Cross-domain interactions from approved integration designs. Contracts are listed on the{" "}
        <Link to="/contracts" className="text-accent hover:underline">
          Contracts
        </Link>{" "}
        page.
      </p>
      <DependencyTable title={`${domain.name} depends on`} rows={rows.filter((d) => d.source === domain.key)} other={(d) => d.target} names={names} />
      <DependencyTable title={`Depends on ${domain.name}`} rows={rows.filter((d) => d.target === domain.key)} other={(d) => d.source} names={names} />
    </div>
  );
}

function Documentation({ domain }: { domain: Domain }) {
  const [params, setParams] = useSearchParams();
  const { data: docs, isLoading } = useQuery({ queryKey: ["domainDocs", domain.key], queryFn: () => api.domainDocs(domain.key) });
  const runId = params.get("op") ?? docs?.[0]?.run.id;
  const selected = docs?.find((d) => d.run.id === runId);
  const withDocs = (selected?.run.workflow_stages ?? []).filter((s) => selected?.artifacts.some((a) => a.stage === s.key));
  const [stage, setStage] = useState<string | null>(null);
  useEffect(() => setStage(null), [runId]);
  const shownStage = stage && withDocs.some((s) => s.key === stage) ? stage : withDocs[0]?.key;
  const stageDocs = (selected?.artifacts ?? []).filter((a) => a.stage === shownStage);
  const [doc, setDoc] = useState<DocumentChoice>("all");
  useEffect(() => setDoc("all"), [runId, shownStage]);

  if (isLoading || !docs) return <p className="text-sm text-subtle">Loading…</p>;
  if (docs.length === 0)
    return (
      <p className="text-sm text-subtle">
        No documents yet. List the domain's features on the Features tab, then migrate them; the documents of every run appear here.
      </p>
    );

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <nav className="space-y-0.5 lg:sticky lg:top-4 lg:self-start" aria-label="Operations">
        <div className="mb-2 px-2">
          <DownloadButton
            label="Download all (.zip)"
            title={`Every document of every feature of ${domain.name}, one folder per feature`}
            onDownload={() => api.downloadDomainDocuments(domain.key)}
          />
        </div>
        {docs.map(({ run }) => (
          <button
            key={run.id}
            onClick={() => setParams({ tab: "docs", op: run.id }, { replace: true })}
            className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${run.id === runId ? "bg-muted font-medium" : ""}`}
          >
            <span className="truncate">{opLabel(run.operation)}</span>
            <StatusBadge status={run.status} />
          </button>
        ))}
      </nav>
      <main className="min-w-0 space-y-4">
        {selected && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-semibold">{opLabel(selected.run.operation)}</h2>
              <div className="flex items-center gap-4">
                {selected.artifacts.length > 0 && (
                  <DownloadButton label="Download documents (.zip)" onDownload={() => api.downloadRunDocuments(selected.run.id)} />
                )}
                <Link to={`/runs/${selected.run.id}`} className="text-sm text-accent hover:underline">
                  Open run →
                </Link>
              </div>
            </div>
            {withDocs.length === 0 ? (
              <p className="text-sm text-subtle">No documents yet.</p>
            ) : (
              <>
                <nav className="flex flex-wrap gap-x-4 gap-y-1 border-b border-border text-sm">
                  {withDocs.map((s) => (
                    <button
                      key={s.key}
                      onClick={() => setStage(s.key)}
                      className={`-mb-px border-b-2 pb-2 ${shownStage === s.key ? "border-accent font-medium" : "border-transparent text-subtle hover:text-fg"}`}
                    >
                      {s.title}
                    </button>
                  ))}
                </nav>
                {stageDocs.length > 1 && <DocumentPicker docs={stageDocs} value={doc} onChange={setDoc} />}
                {chosen(stageDocs, doc).map((a) => (
                  <ArtifactViewer key={a.id} artifactId={a.id} editable={false} />
                ))}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function AgentInputsForm({ domain, agents }: { domain: Domain; agents: Agent[] }) {
  const queryClient = useQueryClient();
  const initial = () => ({
    context: domain.context,
    inputs: Object.fromEntries(agents.map((s) => [s.key, domain.agent_inputs[s.key] ?? EMPTY_INPUTS])) as Record<string, AgentInputs>,
  });
  const [form, setForm] = useState(initial);
  const clean = (i: AgentInputs) => ({ paths: i.paths.map((p) => p.trim()).filter(Boolean), notes: i.notes.trim() });
  const payload = { context: form.context, agent_inputs: Object.fromEntries(Object.entries(form.inputs).map(([k, v]) => [k, clean(v)])) };
  const saved = { context: domain.context, agent_inputs: Object.fromEntries(agents.map((s) => [s.key, clean(domain.agent_inputs[s.key] ?? EMPTY_INPUTS)])) };
  const dirty = JSON.stringify(payload) !== JSON.stringify(saved);

  const save = useMutation({
    mutationFn: () => api.updateDomain(domain.key, payload),
    onSuccess: (d) => {
      queryClient.setQueryData(["domain", domain.key], d);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
  });

  return (
    <Guarded permission="domains.manage">
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="text-sm text-subtle">
          Every run in this domain passes these to its agents (whichever workflow it uses). Operation-specific entry points can be added when starting a run. Agents can still ask you questions while
          they work.
        </p>
        <section className="card p-4">
          <Field label="Shared context (all agents)" hint="Module layout, naming conventions, schemas, known quirks: anything every stage should know.">
            <textarea rows={4} className={inputClass} value={form.context} onChange={(e) => setForm({ ...form, context: e.target.value })} />
          </Field>
        </section>
        {agents.map((s, i) => (
          <section key={s.key} className="space-y-3 card p-4">
            <h3 className="font-semibold">
              <span className="mr-2 text-xs tabular-nums text-subtle">{i + 1}</span>
              {s.title}
            </h3>
            <InputsEditor
              value={form.inputs[s.key]}
              onChange={(v) => setForm({ ...form, inputs: { ...form.inputs, [s.key]: v } })}
              notesHint={`Anything the ${s.title} agent should know for this domain.`}
            />
          </section>
        ))}
        <div className="sticky bottom-0 flex items-center gap-3 border-t border-border bg-bg py-3">
          <button type="submit" disabled={!dirty || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Save inputs
          </button>
          {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
          {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved</span>}
          {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
        </div>
      </form>
    </Guarded>
  );
}

function Details({ domain }: { domain: Domain }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: domains } = useDomains();
  const runs = domains?.find((d) => d.key === domain.key)?.runs ?? 0;
  const { data: workflows = [] } = useWorkflows();
  const [name, setName] = useState(domain.name);
  const [description, setDescription] = useState(domain.description);
  const [defaultWorkflow, setDefaultWorkflow] = useState(domain.default_workflow ?? "");
  const save = useMutation({
    mutationFn: () => api.updateDomain(domain.key, { name: name.trim(), description, default_workflow: defaultWorkflow }),
    onSuccess: (d) => {
      queryClient.setQueryData(["domain", domain.key], d);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteDomain(domain.key, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["discovery"] });
      navigate("/domains");
    },
  });

  return (
    <Guarded permission="domains.manage">
      <div className="max-w-2xl space-y-8">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Name">
            <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Description" hint="Also given to the agents.">
            <textarea rows={3} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label="Default workflow" hint="Preselected when starting a run in this domain.">
            <select className={inputClass} value={defaultWorkflow} onChange={(e) => setDefaultWorkflow(e.target.value)}>
              <option value="">Standard (no domain default)</option>
              {workflows
                .filter((w) => w.scope === "operation")
                .map((w) => (
                  <option key={w.key} value={w.key}>
                    {w.name}
                  </option>
                ))}
            </select>
          </Field>
          <div className="flex items-center gap-3">
            <button type="submit" disabled={!name.trim() || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
              Save details
            </button>
            {save.isSuccess && <span className="text-sm text-ok">Saved</span>}
            {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
          </div>
        </form>
        <section className="space-y-2 border-t border-border pt-6">
          <h3 className="font-semibold">Delete domain</h3>
          <p className="text-sm text-subtle">
            Deletes the domain with everything recorded for it: its migration and feature-listing runs (documents, approvals, workspaces), its feature list, its
            cross-domain dependencies and the contracts it provides. This can't be undone.
            {runs > 0 && ` It has ${runs} migration run${runs === 1 ? "" : "s"}.`}
          </p>
          <button
            className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10 disabled:opacity-50"
            disabled={remove.isPending}
            onClick={() => {
              const typed = window.prompt(`Type ${domain.key} to delete the ${domain.name} domain and all its runs.`);
              if (typed?.trim() === domain.key) remove.mutate();
            }}
          >
            Delete {domain.name} and its runs
          </button>
          {remove.error && <p className="text-sm text-bad">{(remove.error as Error).message}</p>}
        </section>
      </div>
    </Guarded>
  );
}

export function DomainPage() {
  const { domainKey = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "features";
  const { data: agents } = useAgents();
  const { data: domain, error } = useQuery({ queryKey: ["domain", domainKey], queryFn: () => api.domain(domainKey) });

  if (error) return <p className="text-bad">{(error as Error).message}</p>;
  if (!domain || !agents) return <p className="text-subtle">Loading…</p>;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/domains" className="text-sm text-subtle hover:text-fg">
          ← All domains
        </Link>
        <div className="mt-3 flex items-center gap-3">
          <DomainAvatar name={domain.name} domainKey={domain.key} size="h-11 w-11 text-lg" />
          <div>
            <div className="flex flex-wrap items-baseline gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">{domain.name}</h1>
              <code className="text-xs text-subtle">{domain.key}</code>
            </div>
            {domain.description && <p className="mt-0.5 max-w-3xl text-sm text-subtle">{domain.description}</p>}
          </div>
        </div>
      </div>
      <nav className="flex gap-4 border-b border-border text-sm">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setParams({ tab: t.key }, { replace: true })}
            className={`-mb-px border-b-2 pb-2 ${tab === t.key ? "border-accent font-medium" : "border-transparent text-subtle hover:text-fg"}`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "features" && <DomainFeatures domain={domain} />}
      {tab === "docs" && <Documentation domain={domain} />}
      {tab === "inputs" && <AgentInputsForm domain={domain} agents={agents} />}
      {tab === "boundary" && <Boundary key={domain.key} domain={domain} />}
      {tab === "dependencies" && <Dependencies domain={domain} />}
      {tab === "details" && <Details domain={domain} />}
    </div>
  );
}
