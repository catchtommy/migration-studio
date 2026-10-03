import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Agent, type AgentInputs, type Domain } from "../lib/api";
import { useAgents, useDomains, useWorkflows } from "../lib/hooks";
import { ArtifactViewer } from "../components/ArtifactViewer";
import { Field, inputClass } from "../components/Field";
import { EMPTY_INPUTS, InputsEditor } from "../components/InputsEditor";
import { StatusBadge } from "../components/StatusBadge";

type Tab = "docs" | "inputs" | "details";
const TABS: { key: Tab; label: string }[] = [
  { key: "docs", label: "Documentation" },
  { key: "inputs", label: "Agent inputs" },
  { key: "details", label: "Details" },
];

function Documentation({ domain }: { domain: Domain }) {
  const [params, setParams] = useSearchParams();
  const { data: docs, isLoading } = useQuery({ queryKey: ["domainDocs", domain.key], queryFn: () => api.domainDocs(domain.key) });
  const runId = params.get("op") ?? docs?.[0]?.run.id;
  const selected = docs?.find((d) => d.run.id === runId);
  const withDocs = (selected?.run.workflow_stages ?? []).filter((s) => selected?.artifacts.some((a) => a.stage === s.key));
  const [stage, setStage] = useState<string | null>(null);
  useEffect(() => setStage(null), [runId]);
  const shownStage = stage && withDocs.some((s) => s.key === stage) ? stage : withDocs[0]?.key;

  if (isLoading || !docs) return <p className="text-sm text-subtle">Loading…</p>;
  if (docs.length === 0)
    return (
      <p className="text-sm text-subtle">
        No operations migrated yet. Start one from the{" "}
        <Link to="/" className="text-accent hover:underline">
          Migrations
        </Link>{" "}
        page; its documents will appear here.
      </p>
    );

  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <nav className="space-y-0.5 lg:sticky lg:top-4 lg:self-start" aria-label="Operations">
        {docs.map(({ run }) => (
          <button
            key={run.id}
            onClick={() => setParams({ tab: "docs", op: run.id }, { replace: true })}
            className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${run.id === runId ? "bg-muted font-medium" : ""}`}
          >
            <span className="truncate">{run.operation}</span>
            <StatusBadge status={run.status} />
          </button>
        ))}
      </nav>
      <main className="min-w-0 space-y-4">
        {selected && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-semibold">{selected.run.operation}</h2>
              <Link to={`/runs/${selected.run.id}`} className="text-sm text-accent hover:underline">
                Open migration run →
              </Link>
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
                {selected.artifacts
                  .filter((a) => a.stage === shownStage)
                  .map((a) => (
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
      <section className="rounded-lg border border-border bg-surface p-4">
        <Field label="Shared context (all agents)" hint="Module layout, naming conventions, schemas, known quirks: anything every stage should know.">
          <textarea rows={4} className={inputClass} value={form.context} onChange={(e) => setForm({ ...form, context: e.target.value })} />
        </Field>
      </section>
      {agents.map((s, i) => (
        <section key={s.key} className="space-y-3 rounded-lg border border-border bg-surface p-4">
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
    mutationFn: () => api.deleteDomain(domain.key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      navigate("/domains");
    },
  });

  return (
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
            {workflows.map((w) => (
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
        {runs > 0 ? (
          <p className="text-sm text-subtle">This domain has {runs} migration run(s), so it cannot be deleted.</p>
        ) : (
          <button
            className="rounded-md border border-bad/50 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10"
            onClick={() => window.confirm(`Delete the ${domain.name} domain and its agent inputs?`) && remove.mutate()}
          >
            Delete {domain.name}
          </button>
        )}
        {remove.error && <p className="text-sm text-bad">{(remove.error as Error).message}</p>}
      </section>
    </div>
  );
}

export function DomainPage() {
  const { domainKey = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "docs";
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
        <div className="mt-2 flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold">{domain.name}</h1>
          <code className="text-xs text-subtle">{domain.key}</code>
        </div>
        {domain.description && <p className="mt-1 max-w-3xl text-sm text-subtle">{domain.description}</p>}
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
      {tab === "docs" && <Documentation domain={domain} />}
      {tab === "inputs" && <AgentInputsForm domain={domain} agents={agents} />}
      {tab === "details" && <Details domain={domain} />}
    </div>
  );
}
