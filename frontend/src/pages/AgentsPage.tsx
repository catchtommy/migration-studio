import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, looksLikeAgentMd, type Agent, type AgentCatalog, type AgentInput, type ParsedAgentFile } from "../lib/api";
import { useAgents, useWorkflows } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { ChipIcon } from "../components/icons";

function toInput(agent: Agent): AgentInput {
  const { key: _key, builtin: _builtin, created_at: _created, updated_at: _updated, ...rest } = agent;
  return rest;
}


/** Picks a Copilot custom agent file (*.agent.md) and returns its parsed fields. */
function AgentFileButton({ label, onParsed }: { label: string; onParsed: (parsed: ParsedAgentFile, fileName: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button type="button" onClick={() => input.current?.click()} className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:border-accent/50">
        {label}
      </button>
      <input
        ref={input}
        type="file"
        accept=".md,text/markdown"
        aria-label={label}
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          try {
            setError(null);
            onParsed(await api.parseAgentMd(await file.text()), file.name);
          } catch (err) {
            setError(`${file.name}: ${(err as Error).message}`);
          }
        }}
      />
      {error && <span className="w-full text-sm text-bad">{error}</span>}
    </>
  );
}

function ImportNotice({ fileName, warnings, onDismiss }: { fileName: string; warnings: string[]; onDismiss: () => void }) {
  return (
    <div className="rounded-md border border-accent/40 bg-accent/5 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p>
          Imported <span className="font-medium">{fileName}</span>. Review the fields below, then save.
        </p>
        <button type="button" onClick={onDismiss} className="text-xs text-subtle hover:text-fg">
          Dismiss
        </button>
      </div>
      {warnings.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-warn">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Which editor view opens: the agent.md text (default) or the form. Remembered per browser.
const VIEW_KEY = "migration-studio.agent-view";
function savedView(): "form" | "md" {
  try {
    return localStorage.getItem(VIEW_KEY) === "form" ? "form" : "md";
  } catch {
    return "md";
  }
}
function rememberView(view: "form" | "md") {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* storage unavailable: the default applies next time */
  }
}

const FRONTMATTER = /^\s*---[ \t]*\n([\s\S]*?)\n---[ \t]*(\n|$)/;

/** Settings an agent.md text without a metadata section leaves as they are. */
function KeptSettings({ text, form, outputs }: { text: string; form: AgentInput; outputs: string[] }) {
  const header = FRONTMATTER.exec(text)?.[1];
  if (header === undefined || /^metadata\s*:/m.test(header)) return null;
  return (
    <p className="rounded-md bg-muted px-3 py-2 text-xs text-subtle">
      This text has no <code>metadata</code> section, so the agent keeps its current settings: {form.mode === "llm" ? "AI" : "Stub"} mode, effort {form.effort},{" "}
      {form.jdk === "modern" ? "JDK 25" : "JDK 8"}, max turns {form.max_turns}, required documents {outputs.join(", ") || "none"}, checks{" "}
      {form.checks.join(", ") || "none"}.
    </p>
  );
}

function NewAgentForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [pasted, setPasted] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [imported, setImported] = useState<{ parsed: ParsedAgentFile; fileName: string } | null>(null);
  const create = useMutation({
    mutationFn: async () => {
      // Pasted text (or an imported file) defines the agent; the title box overrides its name if filled in.
      const parsed = imported?.parsed ?? (pasted.trim() ? await api.parseAgentMd(pasted) : undefined);
      const agentTitle = title.trim() || parsed?.title || "";
      const key = slug(agentTitle);
      if (!key) throw new Error("Give the agent a title, or a name in the agent.md header.");
      const agentDescription = description.trim() || parsed?.description || "";
      const agent = await api.createAgent({ key, title: agentTitle, description: agentDescription });
      if (!parsed) return agent;
      const { warnings: _w, ...fields } = parsed;
      const { key: _k, builtin: _b, created_at: _c, updated_at: _u, ...current } = agent;
      return api.updateAgent(agent.key, { ...current, ...fields, title: agentTitle, description: agentDescription });
    },
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${agent.key}`);
      onCancel();
    },
  });
  const ready = !!(pasted.trim() || imported || slug(title));

  return (
    <form
      className="card space-y-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">New agent</h2>
        <AgentFileButton label="Import from agent.md" onParsed={(parsed, fileName) => setImported({ parsed, fileName })} />
      </div>
      {imported ? (
        <ImportNotice fileName={imported.fileName} warnings={imported.parsed.warnings} onDismiss={() => setImported(null)} />
      ) : (
        <Field label="Paste your agent.md" hint="The content of your GitHub Copilot agent file: the --- header, then the instructions.">
          <textarea
            aria-label="agent.md text"
            autoFocus
            rows={14}
            spellCheck={false}
            className={`${inputClass} font-mono text-xs`}
            placeholder={"---\nname: Security review\ndescription: Reviews the service for security issues\ntools: [read, search]\n---\n\nYou review…"}
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
          />
        </Field>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-subtle hover:text-fg">Title and description (optional: taken from the agent.md header)</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-[16rem_1fr]">
          <Field label="Title" hint={slug(title) ? `Key: ${slug(title)}` : "Leave empty to use the name in the header"}>
            <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label="Description">
            <input className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" disabled={!ready || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
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

  const [imported, setImported] = useState<{ fileName: string; warnings: string[] } | null>(null);
  const outputsList = () => outputs.split("\n").map((o) => o.trim()).filter(Boolean);

  /** Put parsed agent.md fields into the form (required documents live in their own text box). */
  const applyParsed = (parsed: ParsedAgentFile, source: string) => {
    const { warnings, outputs: parsedOutputs, ...fields } = parsed;
    save.reset();
    setForm((f) => ({ ...f, ...fields }));
    if (parsedOutputs) setOutputs(parsedOutputs.join("\n"));
    setImported({ fileName: source, warnings });
  };

  // The agent.md tab: the whole agent as one text. Switching tabs converts unsaved edits both ways.
  const [view, setView] = useState<"form" | "md">(savedView);
  const [md, setMd] = useState({ text: "", base: "" });
  const [mdError, setMdError] = useState<string | null>(null);
  const showMd = async () => {
    try {
      const { content } = await api.renderAgentMd({ ...form, outputs: outputsList() });
      setMd({ text: content, base: content });
      setMdError(null);
      setView("md");
      rememberView("md");
    } catch (e) {
      setMdError((e as Error).message);
    }
  };
  const showForm = async () => {
    if (md.text !== md.base) {
      try {
        applyParsed(await api.parseAgentMd(md.text), "the agent.md tab");
      } catch (e) {
        setMdError((e as Error).message);
        return;
      }
    }
    setMdError(null);
    setView("form");
    rememberView("form");
  };
  useEffect(() => {
    if (view === "md") void showMd(); // opening on the text view: load the agent's text
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const saveMd = useMutation({
    mutationFn: () => api.saveAgentMd(config.key, md.text),
    onSuccess: ({ agent, warnings }) => {
      onSaved(agent);
      setMd((m) => ({ ...m, base: m.text }));
      setMdError(null);
      setImported(warnings.length ? { fileName: "agent.md", warnings } : null);
    },
    onError: (e) => setMdError((e as Error).message),
  });

  const set = <K extends keyof AgentInput>(key: K, value: AgentInput[K]) => {
    save.reset();
    setForm({ ...form, [key]: value });
  };
  const formDirty = JSON.stringify({ ...form, outputs: outputsList() }) !== JSON.stringify(toInput(config));
  const dirty = formDirty || (view === "md" && md.text !== md.base);
  const toggleTool = (name: string) =>
    set("tools", form.tools.includes(name) ? form.tools.filter((t) => t !== name) : [...form.tools, name]);
  const ai = form.mode === "llm";

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (view === "md") saveMd.mutate();
        else save.mutate();
      }}
    >
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">{config.title}</h2>
          <code className="text-xs text-subtle">{config.key}</code>
          {!config.builtin && <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-subtle">Custom</span>}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <AgentFileButton
              label="Import agent.md"
              onParsed={(parsed, fileName) => {
                applyParsed(parsed, fileName);
                setView("form");
              }}
            />
            <a href={api.agentMdUrl(config.key)} download className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg">
              Download agent.md
            </a>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div role="tablist" aria-label="Edit as" className="inline-flex rounded-lg border border-border bg-muted p-1 text-sm">
            {(["md", "form"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={view === v}
                onClick={() => view !== v && (v === "md" ? showMd() : showForm())}
                className={`rounded-md px-4 py-1.5 ${view === v ? "bg-surface font-medium text-fg shadow-sm" : "text-subtle hover:text-fg"}`}
              >
                {v === "md" ? "agent.md (free text)" : "Form"}
              </button>
            ))}
          </div>
          <span className="text-xs text-subtle">
            {view === "md" ? "The whole agent as one text, in GitHub Copilot's agent.md format." : "The same agent, setting by setting."}
          </span>
        </div>
        {mdError && <p className="text-sm text-bad">{mdError}</p>}
        {imported && <ImportNotice fileName={imported.fileName} warnings={imported.warnings} onDismiss={() => setImported(null)} />}
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
        {view === "form" && ( // on the agent.md tab, name and description are edited in the text
          <div className="grid gap-4 sm:grid-cols-[16rem_1fr]">
            <Field label="Title">
              <input className={inputClass} value={form.title} onChange={(e) => set("title", e.target.value)} />
            </Field>
            <Field label="Description" hint="Shown in workflows and given to the agent as its stage description.">
              <input className={inputClass} value={form.description} onChange={(e) => set("description", e.target.value)} />
            </Field>
          </div>
        )}
      </header>


      {view === "md" ? (
        <div className="space-y-2">
          <p className="text-sm">
            Paste your agent.md from GitHub, or edit it here, then <span className="font-medium">Save agent</span>.
          </p>
          <details className="text-xs text-subtle">
            <summary className="cursor-pointer hover:text-fg">What the header can hold</summary>
            <p className="mt-1">
              <code>name</code> (title), <code>description</code>, <code>model</code> (or auto) and <code>tools</code> (Copilot's read, search, edit, execute, or
              this app's tool names). Settings Copilot has no field for go under <code>metadata</code>: mode (stub or llm), effort, jdk (legacy or modern),
              max-turns, required-documents and checks. Anything the text leaves out keeps its current value.
            </p>
          </details>
          <textarea
            aria-label="agent.md"
            rows={30}
            spellCheck={false}
            className={`${inputClass} font-mono text-xs leading-5`}
            value={md.text}
            placeholder="Loading…"
            onChange={(e) => {
              saveMd.reset();
              setMdError(null);
              setMd({ ...md, text: e.target.value });
            }}
          />
          <KeptSettings text={md.text} form={form} outputs={outputsList()} />
        </div>
      ) : (
      <>

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
          {looksLikeAgentMd(form.instructions) && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-warn/40 bg-warn/5 p-3 text-sm">
              <span>This looks like a whole agent.md file. Apply it, so its header sets the agent's settings and only the instructions go to the model.</span>
              <button
                type="button"
                className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50"
                onClick={async () => {
                  try {
                    applyParsed(await api.parseAgentMd(form.instructions), "pasted text");
                  } catch (e) {
                    setMdError((e as Error).message);
                  }
                }}
              >
                Apply agent.md
              </button>
            </div>
          )}

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

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={(form.checks ?? []).includes("boundary")}
              onChange={(e) => set("checks", e.target.checked ? ["boundary"] : [])}
            />
            <span>
              Boundary check
              <span className="block text-xs text-subtle">
                Before finishing, the agent must remove every use of another domain's packages or tables from this domain's service (ownership is set on each
                domain's Boundary tab).
              </span>
            </span>
          </label>
        </div>

      </>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <button
          type="submit"
          disabled={!dirty || save.isPending || saveMd.isPending}
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
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
        {(save.isSuccess || saveMd.isSuccess) && !dirty && <span className="text-sm text-ok">Saved. Applies from the next stage run.</span>}
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
