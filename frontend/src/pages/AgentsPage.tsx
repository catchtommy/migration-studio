import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Agent, type AgentCatalog, type AgentInput } from "../lib/api";
import { useAgents, useWorkflows } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { ChipIcon } from "../components/icons";

function toInput(agent: Agent): AgentInput {
  const { key: _key, builtin: _builtin, created_at: _created, updated_at: _updated, ...rest } = agent;
  return rest;
}


function NewAgentForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const key = slug(title);
  const create = useMutation({
    mutationFn: () => api.createAgent({ key, title: title.trim(), description }),
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${agent.key}`);
      onCancel();
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
      <h2 className="font-semibold">New agent</h2>
      <Field label="Title" hint={key ? `Key: ${key}` : "e.g. Security review"}>
        <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Description">
        <textarea rows={2} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={!key || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Create agent
        </button>
        <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
        {create.error && <span className="text-sm text-bad">{(create.error as Error).message}</span>}
      </div>
    </form>
  );
}

function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${mode === "llm" ? "bg-gradient-to-r from-accent to-accent-2 text-white shadow-sm" : "bg-muted text-subtle"}`}>
      {mode === "llm" ? "AI" : "Stub"}
    </span>
  );
}

function AgentEditor({ config, catalog }: { config: Agent; catalog: AgentCatalog }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: workflows = [] } = useWorkflows();
  const usedIn = workflows.filter((w) => w.steps.some((s) => s.kind === "agent" && s.key === config.key));
  const [form, setForm] = useState<AgentInput>(() => toInput(config));
  const [outputs, setOutputs] = useState(config.outputs.join("\n"));
  useEffect(() => {
    setForm(toInput(config));
    setOutputs(config.outputs.join("\n"));
  }, [config]);

  const onSaved = (saved: Agent) => {
    queryClient.setQueryData<Agent[]>(["agents"], (prev) => prev?.map((a) => (a.key === saved.key ? saved : a)));
    queryClient.invalidateQueries({ queryKey: ["workflows"] }); // step titles come from agents
  };
  const save = useMutation({
    mutationFn: () => api.updateAgent(config.key, { ...form, outputs: outputs.split("\n").map((o) => o.trim()).filter(Boolean) }),
    onSuccess: onSaved,
  });
  const reset = useMutation({ mutationFn: () => api.resetAgent(config.key), onSuccess: onSaved });
  const remove = useMutation({
    mutationFn: () => api.deleteAgent(config.key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/agents");
    },
  });

  const set = <K extends keyof AgentInput>(key: K, value: AgentInput[K]) => {
    save.reset();
    setForm({ ...form, [key]: value });
  };
  const dirty = JSON.stringify({ ...form, outputs: outputs.split("\n").map((o) => o.trim()).filter(Boolean) }) !== JSON.stringify(toInput(config));
  const toggleTool = (name: string) =>
    set("tools", form.tools.includes(name) ? form.tools.filter((t) => t !== name) : [...form.tools, name]);
  const ai = form.mode === "llm";

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <header className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="text-lg font-semibold">{config.title}</h2>
          <code className="text-xs text-subtle">{config.key}</code>
          {!config.builtin && <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-subtle">Custom</span>}
        </div>
        <p className="text-sm text-subtle">
          {usedIn.length ? (
            <>
              Used in{" "}
              {usedIn.map((w, i) => (
                <span key={w.key}>
                  {i > 0 && ", "}
                  <Link to={`/workflows/${w.key}`} className="text-accent hover:underline">
                    {w.name}
                  </Link>
                </span>
              ))}
            </>
          ) : (
            <>
              Not used in any workflow yet. Add it on the{" "}
              <Link to="/workflows" className="text-accent hover:underline">
                Workflows
              </Link>{" "}
              page.
            </>
          )}
        </p>
        <div className="grid gap-4 sm:grid-cols-[16rem_1fr]">
          <Field label="Title">
            <input className={inputClass} value={form.title} onChange={(e) => set("title", e.target.value)} />
          </Field>
          <Field label="Description" hint="Shown in workflows and given to the agent as its stage description.">
            <input className={inputClass} value={form.description} onChange={(e) => set("description", e.target.value)} />
          </Field>
        </div>
      </header>

      <div role="radiogroup" aria-label="Mode" className="inline-flex rounded-md border border-border bg-surface p-0.5 text-sm">
        {(["stub", "llm"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={form.mode === m}
            onClick={() => set("mode", m)}
            className={`rounded px-3 py-1 ${form.mode === m ? "bg-fg text-bg" : "text-subtle hover:text-fg"}`}
          >
            {m === "llm" ? "AI" : "Stub"}
          </button>
        ))}
      </div>
      <p className="text-sm text-subtle">
        {ai ? (
          <>
            Runs with {catalog.provider === "copilot" ? "GitHub Copilot, using your account" : "the Claude API"} (
            <Link to="/settings" className="text-accent hover:underline">
              Settings
            </Link>
            ).
          </>
        ) : (
          "The stub writes a placeholder document without calling an AI model. Switch to AI to run this agent for real."
        )}
      </p>

      <div className={`space-y-6 ${ai ? "" : "opacity-60"}`}>
        <div className="grid gap-4 sm:grid-cols-4">
          <Field
            label="Model"
            hint={catalog.provider === "copilot" && catalog.models.length <= 1 ? "Sign in to Copilot to see your models" : "auto lets the provider choose"}
          >
            <input className={inputClass} list="model-options" value={form.model} onChange={(e) => set("model", e.target.value)} />
            <datalist id="model-options">
              {catalog.models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          <Field label="Effort" hint="Used when the model supports it">
            <select className={inputClass} value={form.effort} onChange={(e) => set("effort", e.target.value)}>
              {catalog.efforts.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label="JDK" hint="For run_command">
            <select className={inputClass} value={form.jdk} onChange={(e) => set("jdk", e.target.value as AgentInput["jdk"])}>
              <option value="legacy">JDK 8 (legacy)</option>
              <option value="modern">JDK 25 (modern)</option>
            </select>
          </Field>
          <Field label="Max turns">
            <input type="number" min={1} max={500} className={inputClass} value={form.max_turns} onChange={(e) => set("max_turns", Number(e.target.value))} />
          </Field>
        </div>

        <Field
          label="Instructions"
          hint="The agent's system prompt. Domain and operation inputs, earlier stages' documents and reviewer feedback are added automatically for each run."
        >
          <textarea rows={22} className={`${inputClass} font-mono text-xs leading-5`} value={form.instructions} onChange={(e) => set("instructions", e.target.value)} />
        </Field>

        <div className="grid gap-6 md:grid-cols-2">
          <fieldset className="text-sm">
            <legend className="mb-2 font-medium">Tools</legend>
            <div className="space-y-1.5">
              {catalog.tools.map((t) => (
                <label key={t.name} className="flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={form.tools.includes(t.name)} onChange={() => toggleTool(t.name)} />
                  <span>
                    <code className="text-xs">{t.name}</code>
                    <span className="block text-xs text-subtle">{t.description}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Required documents" hint="One per line, relative to .migration/<domain>/<operation>/. The agent is sent back if any is missing.">
            <textarea rows={5} className={`${inputClass} font-mono text-xs`} value={outputs} onChange={(e) => {
                save.reset();
                setOutputs(e.target.value);
              }}
            />
          </Field>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <button type="submit" disabled={!dirty || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Save agent
        </button>
        {config.builtin && (
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg"
            disabled={reset.isPending}
            onClick={() => window.confirm(`Reset the ${config.title} agent to its default configuration?`) && reset.mutate()}
          >
            Reset to default
          </button>
        )}
        <button
          type="button"
          className="ml-auto rounded-md px-3 py-1.5 text-sm text-bad hover:bg-bad/10 disabled:opacity-50"
          disabled={remove.isPending}
          onClick={() => window.confirm(`Delete the ${config.title} agent?`) && remove.mutate()}
        >
          Delete agent
        </button>
        {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
        {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved. Applies from the next stage run.</span>}
        {(save.error || reset.error || remove.error) && (
          <span className="w-full text-sm text-bad">{((save.error || reset.error || remove.error) as Error).message}</span>
        )}
      </div>
    </form>
  );
}

export function AgentsPage() {
  const { agentKey: selected } = useParams();
  const navigate = useNavigate();
  const { data: agents } = useAgents();
  const { data: catalog } = useQuery({ queryKey: ["agentCatalog"], queryFn: api.agentCatalog, staleTime: Infinity });
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!selected && agents?.length) navigate(`/agents/${agents[0].key}`, { replace: true });
  }, [selected, agents, navigate]);

  if (!agents || !catalog) return <p className="text-subtle">Loading…</p>;
  const config = agents.find((a) => a.key === selected);

  return (
    <div className="space-y-6">
      <PageTitle icon={<ChipIcon />} title="Agents" subtitle="The agent library. Workflows use these agents as steps; changes apply to stages that start after saving." tone="from-emerald-500 to-teal-500">
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New agent
          </button>
        )}
      </PageTitle>
      {adding && <NewAgentForm onCancel={() => setAdding(false)} />}
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <nav className="space-y-0.5 lg:sticky lg:top-4 lg:self-start">
          {agents.map((a) => (
            <NavLink
              key={a.key}
              to={`/agents/${a.key}`}
              className={({ isActive }) => `flex items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-muted ${isActive ? "bg-muted font-medium" : ""}`}
            >
              <span className="flex-1">{a.title}</span>
              <ModeBadge mode={a.mode} />
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0">
          {config ? <AgentEditor key={config.key} config={config} catalog={catalog} /> : selected && <p className="text-sm text-subtle">Agent not found.</p>}
        </main>
      </div>
    </div>
  );
}
