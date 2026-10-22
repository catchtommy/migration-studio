import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  expandPlaceholders,
  looksLikeAgentMd,
  placeholders,
  type Agent,
  type AgentCatalog,
  type AgentInput,
  type Library,
  type ParsedAgentFile,
  type SharedText,
} from "../lib/api";
import { useAgents, useCanGlobally, useWorkflows } from "../lib/hooks";
import { LibraryBadge, SaveTarget } from "../components/LibraryBadge";
import { DangerDialog } from "../components/DangerDialog";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { ChipIcon } from "../components/icons";

function toInput(agent: Agent): AgentInput {
  const { key: _key, builtin: _builtin, created_at: _created, updated_at: _updated, scope: _scope, customised: _customised, ...rest } = agent;
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
  const canShared = useCanGlobally("agents.manage");
  const [libraryChoice, setLibrary] = useState<Library | null>(null);
  const library: Library = libraryChoice ?? (canShared ? "shared" : "project");
  const create = useMutation({
    mutationFn: async () => {
      // Pasted text (or an imported file) defines the agent; the title box overrides its name if filled in.
      const parsed = imported?.parsed ?? (pasted.trim() ? await api.parseAgentMd(pasted) : undefined);
      const agentTitle = title.trim() || parsed?.title || "";
      const key = slug(agentTitle);
      if (!key) throw new Error("Give the agent a title, or a name in the agent.md header.");
      const agentDescription = description.trim() || parsed?.description || "";
      const agent = await api.createAgent({ key, title: agentTitle, description: agentDescription }, library);
      if (!parsed) return agent;
      const { warnings: _w, ...fields } = parsed;
      const { key: _k, builtin: _b, created_at: _c, updated_at: _u, scope: _s, customised: _cu, ...current } = agent;
      return api.updateAgent(agent.key, { ...current, ...fields, title: agentTitle, description: agentDescription }, library);
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
        <SaveTarget value={library} onChange={setLibrary} canShared={canShared} noun="new agent" />
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

/** This agent's guardrails: one text in plain words. The built-in checks it runs are always on, so only named here. */
function AgentGuardrails({ agent, form, onChange }: { agent: Agent; form: AgentInput; onChange: (text: string) => void }) {
  const { data } = useQuery({ queryKey: ["guardrails"], queryFn: api.guardrails, staleTime: 60_000 });
  const runsCommands = form.tools.includes("run_command");
  const builtin = (data?.builtin ?? []).filter((b) => b.checks.some((c) => form.checks.includes(c)) || (b.key === "agent_limits" && runsCommands));
  return (
    <div className="space-y-2">
      <Field
        label="Guardrails"
        hint="Rules in plain words this agent must follow, one per line. It gets them as hard rules, and again each time it checks its work; when it can't keep one, it stops and asks. To give one project different rules, customise the agent for that project."
      >
        <textarea
          aria-label={`Guardrails of ${agent.title}`}
          rows={Math.min(24, Math.max(6, (form.guardrails ?? "").split("\n").length + 1))}
          className={`${inputClass} font-mono text-xs leading-5`}
          value={form.guardrails ?? ""}
          placeholder={"e.g.\n- Keep the router under 200 lines.\n- Never change classes under com.acme.common."}
          onChange={(e) => onChange(e.target.value)}
        />
      </Field>
      <InstructionPlaceholders instructions={form.guardrails ?? ""} what="guardrails" />
      {builtin.length > 0 && (
        <details className="text-xs text-subtle">
          <summary className="cursor-pointer hover:text-fg">Also enforced automatically for this agent: {builtin.map((b) => b.title).join(", ")}</summary>
          <div className="mt-2 space-y-3">
            {builtin.map((b) => (
              <div key={b.key}>
                <p className="font-medium text-fg">{b.title}</p>
                <pre className="mt-1 whitespace-pre-wrap font-sans">{b.description}</pre>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

/** Deleting an agent: what it affects, or why it can't be deleted yet (a workflow has it as a step, or an
 * unfinished run uses it). */
function DeleteAgentDialog({ agent, remove, onClose }: {
  agent: Agent;
  remove: { isPending: boolean; error: unknown; mutate: () => void };
  onClose: () => void;
}) {
  const { data: usage, error } = useQuery({
    queryKey: ["agentUsage", agent.key, agent.scope],
    queryFn: () => api.agentUsage(agent.key, agent.scope),
  });
  const shared = agent.scope === "shared";
  return (
    <DangerDialog
      title={`Delete the ${agent.title} agent`}
      checking={!usage && !error}
      blocked={(error as Error | null)?.message ?? usage?.blocked ?? undefined}
      sections={
        usage
          ? [
              {
                tone: "bad",
                title: "Deleted for good",
                items: [
                  <>
                    The <strong>{agent.title}</strong> agent: its instructions, guardrails and settings{shared ? ", in every project" : ""}
                  </>,
                  ...(usage.copies.length ? [`Projects' own copies of it: ${usage.copies.join(", ")}`] : []),
                  ...(agent.builtin ? ["It is a built-in agent: Reset can't bring it back; it comes back only if the studio adds it again"] : []),
                ],
              },
              {
                tone: "ok",
                title: "Kept",
                items: ["Completed runs that used it keep their documents and history", "Every other agent and every workflow"],
              },
              {
                tone: "neutral",
                title: "Not in use",
                text: "No workflow has it as a step and no unfinished run uses it.",
              },
            ]
          : []
      }
      confirmText={agent.key}
      actionLabel="Delete the agent"
      pendingLabel="Deleting…"
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() => remove.mutate()}
      onClose={onClose}
    />
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

  const canShared = useCanGlobally("agents.manage");
  // Where a save goes: a project's own agent or copy stays in the project; a shared one goes to every project
  // (if the user may change the library) or becomes this project's copy.
  const [choice, setChoice] = useState<Library | null>(null); // null: the default below
  const target: Library = config.scope === "project" ? "project" : (choice ?? (canShared ? "shared" : "project"));
  const setTarget = setChoice;
  const onSaved = (saved: Agent) => {
    queryClient.setQueryData<Agent[]>(["agents"], (prev) => prev?.map((a) => (a.key === saved.key ? saved : a)));
    queryClient.invalidateQueries({ queryKey: ["workflows"] }); // step titles come from agents
    queryClient.invalidateQueries({ queryKey: ["sharedTexts"] }); // which agents include each shared text
  };
  const save = useMutation({
    mutationFn: () => api.updateAgent(config.key, { ...form, outputs: outputs.split("\n").map((o) => o.trim()).filter(Boolean) }, target),
    onSuccess: onSaved,
  });
  const reset = useMutation({ mutationFn: () => api.resetAgent(config.key), onSuccess: onSaved });
  const revert = useMutation({ mutationFn: () => api.revertAgent(config.key), onSuccess: (a) => (onSaved(a), setTarget(null)) });
  const [deleting, setDeleting] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteAgent(config.key, config.scope),
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
    mutationFn: () => api.saveAgentMd(config.key, md.text, target),
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
          <LibraryBadge library={config.scope} customised={config.customised} />
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

        <div className="space-y-6">
          {ai && (
            <div className="max-w-sm">
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
            </div>
          )}

          <Field
            label="Instructions"
            hint="The agent's system prompt. Domain and operation inputs, earlier stages' documents and reviewer feedback are added automatically for each run."
          >
            <textarea rows={22} className={`${inputClass} font-mono text-xs leading-5`} value={form.instructions} onChange={(e) => set("instructions", e.target.value)} />
          </Field>
          <InstructionPlaceholders instructions={form.instructions} />
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

          <AgentGuardrails agent={config} form={form} onChange={(text) => set("guardrails", text)} />

          <details className="rounded-md border border-border px-4 py-3">
            <summary className="cursor-pointer text-sm font-medium">Advanced settings</summary>
            <p className="mt-1 text-xs text-subtle">Effort, toolchain, turn limit, tools and required documents. The defaults suit most agents.</p>
            <div className="mt-4 space-y-6">
              <div className="grid gap-4 sm:grid-cols-3">
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
              <div className="grid gap-6 md:grid-cols-2">
                <fieldset className="text-sm">
                  <legend className="mb-2 font-medium">Tools</legend>
                  <div className="space-y-1.5">
                    {/* Check tools aren't chosen: an agent gets those of the checks it must pass (common.tools_of). */}
                    {catalog.tools.filter((t) => !t.name.startsWith("check_")).map((t) => (
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
          </details>
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
        {config.scope === "shared" && <SaveTarget value={target} onChange={setTarget} canShared={canShared} noun="agent" />}
        {config.customised && (
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg"
            disabled={revert.isPending}
            onClick={() => window.confirm(`Drop this project's changes to ${config.title} and use the shared agent again?`) && revert.mutate()}
          >
            Revert to shared
          </button>
        )}
        {config.builtin && config.scope === "shared" && canShared && (
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg"
            disabled={reset.isPending}
            onClick={() => window.confirm(`Reset the ${config.title} agent to its default configuration?`) && reset.mutate()}
          >
            Reset to default
          </button>
        )}
        {!config.customised && (config.scope === "project" || canShared) && (
          <button
            type="button"
            className="ml-auto rounded-md px-3 py-1.5 text-sm text-bad hover:bg-bad/10 disabled:opacity-50"
            disabled={remove.isPending}
            onClick={() => setDeleting(true)}
          >
            Delete agent
          </button>
        )}
        {deleting && <DeleteAgentDialog agent={config} remove={remove} onClose={() => setDeleting(false)} />}
        {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
        {(save.isSuccess || saveMd.isSuccess) && !dirty && <span className="text-sm text-ok">Saved. Applies from the next stage run.</span>}
        {(save.error || reset.error || remove.error || revert.error) && (
          <span className="w-full text-sm text-bad">{((save.error || reset.error || remove.error || revert.error) as Error).message}</span>
        )}
      </div>
    </form>
  );
}

/** The global guardrails in plain words (editable) and what the built-in checks enforce (read-only). */
function useSharedTexts() {
  return useQuery({ queryKey: ["sharedTexts"], queryFn: api.sharedTexts });
}

/** One shared text: what it is, where it is used, and its text, editable by those who may change the library. */
function SharedTextEditor({ text }: { text: SharedText }) {
  const queryClient = useQueryClient();
  const canEdit = useCanGlobally("agents.manage");
  const [draft, setDraft] = useState<string | null>(null);
  const onSaved = (saved: SharedText) => {
    queryClient.setQueryData<SharedText[]>(["sharedTexts"], (prev) => prev?.map((t) => (t.key === saved.key ? saved : t)));
    setDraft(null);
  };
  const save = useMutation({ mutationFn: () => api.updateSharedText(text.key, draft ?? text.text), onSuccess: onSaved });
  const reset = useMutation({ mutationFn: () => api.resetSharedText(text.key), onSuccess: onSaved });
  const value = draft ?? text.text;
  const error = (save.error ?? reset.error) as Error | null;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{text.title}</span>
        <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{`{{${text.key}}}`}</code>
        {text.customised && <span className="rounded-full bg-warn/15 px-2 py-0.5 text-xs text-warn">Customised</span>}
      </div>
      {text.description && <p className="text-xs text-subtle">{text.description}</p>}
      <p className="text-xs text-subtle">
        Used by: {text.used_by.length ? text.used_by.join(", ") : "no agent"}
        {text.updated_by && ` · last changed by ${text.updated_by}`}
      </p>
      <textarea
        aria-label={`Shared text ${text.title}`}
        rows={Math.min(24, Math.max(6, value.split("\n").length + 1))}
        className={`${inputClass} font-mono text-xs leading-5`}
        value={value}
        readOnly={!canEdit}
        onChange={(e) => setDraft(e.target.value)}
      />
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={save.isPending || draft === null || draft === text.text || !draft.trim()}
            onClick={() => save.mutate()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Save shared text
          </button>
          {draft !== null && draft !== text.text && (
            <button type="button" onClick={() => setDraft(null)} className="text-sm text-subtle hover:text-fg">
              Discard changes
            </button>
          )}
          {text.builtin && text.customised && (
            <button type="button" disabled={reset.isPending} onClick={() => reset.mutate()} className="text-sm text-accent hover:underline disabled:opacity-50">
              Reset to default
            </button>
          )}
          {(save.isSuccess || reset.isSuccess) && draft === null && <span className="text-sm text-ok">Saved: every agent above gets it from the next step that starts</span>}
          {error && <span className="text-sm text-bad">{error.message}</span>}
        </div>
      ) : (
        <p className="text-xs text-subtle">Changing a shared text affects every project, so it needs Manage agents.</p>
      )}
    </div>
  );
}

/** The shared texts an agent's instructions include: each one viewable and editable in place, and the full prompt. */
function InstructionPlaceholders({ instructions, what = "instructions" }: { instructions: string; what?: "instructions" | "guardrails" }) {
  const { data: texts } = useSharedTexts();
  const [open, setOpen] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const keys = placeholders(instructions);
  if (keys.length === 0 || !texts) return null;
  const byKey = new Map(texts.map((t) => [t.key, t]));
  const opened = open ? byKey.get(open) : undefined;

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-subtle">Shared texts in these {what}:</span>
        {keys.map((key) => {
          const known = byKey.get(key);
          return known ? (
            <button
              key={key}
              type="button"
              title={known.description || known.title}
              onClick={() => setOpen(open === key ? null : key)}
              className={`rounded-full border px-2 py-0.5 font-mono text-xs ${open === key ? "border-accent bg-accent/10 text-accent" : "border-border hover:border-accent/50"}`}
            >
              {`{{${key}}}`}
              {known.customised && " •"}
            </button>
          ) : (
            <span key={key} title="No shared text has this key: saving will be refused" className="rounded-full border border-bad/50 px-2 py-0.5 font-mono text-xs text-bad">
              {`{{${key}}}`} unknown
            </span>
          );
        })}
        <button type="button" onClick={() => setPreview(!preview)} className="ml-auto text-xs font-medium text-accent hover:underline">
          {preview ? "Hide full text" : what === "guardrails" ? "Show the full guardrails" : "Preview full prompt"}
        </button>
      </div>
      {opened && <SharedTextEditor key={opened.key} text={opened} />}
      {preview && (
        <div className="space-y-1">
          <p className="text-xs text-subtle">
            {what === "guardrails"
              ? "The guardrails as the agent gets them, with the shared texts filled in, after its instructions."
              : "The instructions as the agent gets them, with the shared texts filled in (the preamble and, in a feature's migration, the feature rules come before them; the guardrails come after)."}
          </p>
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs leading-5">
            {expandPlaceholders(instructions, Object.fromEntries(texts.map((t) => [t.key, t.text])))}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Every shared text, the preamble and feature rules included. */
function SharedTextsPanel({ onClose, focus }: { onClose: () => void; focus: string | null }) {
  const { data: texts } = useSharedTexts();
  if (!texts) return <p className="text-subtle">Loading…</p>;
  return (
    <section className="space-y-4 rounded-lg border border-border p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold">Shared texts</h2>
          <p className="text-sm text-subtle">
            Texts several agents share. Instructions include one with its placeholder, such as <code>{"{{rest_rules}}"}</code>, filled in when a step
            starts, so a change here reaches every agent that includes it, in every project. The preamble and the feature rules are given to every agent.
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-subtle hover:text-fg">
          Close
        </button>
      </div>
      {texts.map((t) => (
        <details key={t.key} open={t.key === focus} className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-sm">
            <span className="font-medium">{t.title}</span> <code className="text-xs text-subtle">{`{{${t.key}}}`}</code>
            {t.customised && <span className="ml-2 text-xs text-warn">Customised</span>}
            <span className="ml-2 text-xs text-subtle">
              {t.used_by.length} agent{t.used_by.length === 1 ? "" : "s"}
            </span>
          </summary>
          <div className="mt-3">
            <SharedTextEditor text={t} />
          </div>
        </details>
      ))}
    </section>
  );
}

/** One click: every agent (the shared library and this project's own) runs with AI, or as a stub again. */
function AllAgentsMode({ agents }: { agents: Agent[] }) {
  const queryClient = useQueryClient();
  const canShared = useCanGlobally("agents.manage");
  const stubs = agents.filter((a) => a.mode !== "llm").length;
  const target = stubs > 0 ? "llm" : "stub";
  const change = useMutation({
    mutationFn: () => api.setAllAgentsMode(target),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
  });
  if (!canShared || agents.length === 0) return null;
  const label = target === "llm" ? `Switch all agents to AI (${stubs} in stub)` : "Switch all agents to stub";
  const message =
    target === "llm"
      ? `Run every agent with AI? ${stubs} agent(s) in stub mode will call the AI provider set in Settings from their next step, in every project.`
      : "Run every agent as a stub again? No agent calls an AI model then, in any project; steps write placeholder documents.";
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        disabled={change.isPending}
        onClick={() => window.confirm(message) && change.mutate()}
        className={`rounded-md border px-4 py-1.5 text-sm font-medium disabled:opacity-50 ${
          target === "llm" ? "border-accent/60 text-accent hover:bg-accent/10" : "border-border hover:bg-muted"
        }`}
      >
        {change.isPending ? "Switching…" : label}
      </button>
      {change.error && <span className="text-sm text-bad">{(change.error as Error).message}</span>}
    </span>
  );
}

export function AgentsPage() {
  const { agentKey: selected } = useParams();
  const navigate = useNavigate();
  const { data: agents } = useAgents();
  const { data: catalog } = useQuery({ queryKey: ["agentCatalog"], queryFn: api.agentCatalog, staleTime: Infinity });
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [workflowKey, setWorkflowKey] = useState(""); // "": every agent
  const { data: workflows = [] } = useWorkflows();
  const [params, setParams] = useSearchParams();
  // The Shared texts panel is a search parameter (with `text` for the one opened), keeping the others in the URL.
  const toggleParam = (name: string, on: boolean) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (on) next.set(name, "1");
        else next.delete(name);
        if (name === "shared" && !on) next.delete("text");
        return next;
      },
      { replace: true },
    );
  const showShared = params.get("shared") === "1";
  const setShowShared = (on: boolean) => toggleParam("shared", on);

  useEffect(() => {
    if (!selected && agents?.length) navigate(`/agents/${agents[0].key}${window.location.search}`, { replace: true });
  }, [selected, agents, navigate]);

  if (!agents || !catalog) return <p className="text-subtle">Loading…</p>;
  const config = agents.find((a) => a.key === selected);
  const query = search.trim().toLowerCase();
  const workflow = workflows.find((w) => w.key === workflowKey);
  // With a workflow chosen: only its agents, in the order of its steps.
  const inWorkflow = workflow
    ? workflow.steps.filter((s) => s.kind === "agent").map((s) => agents.find((a) => a.key === s.key)).filter((a): a is Agent => !!a)
    : agents;
  const shown = query ? inWorkflow.filter((a) => [a.title, a.key, a.description].some((s) => s.toLowerCase().includes(query))) : inWorkflow;

  return (
    <div className="space-y-6">
      <PageTitle icon={<ChipIcon />} title="Agents" subtitle="The agent library. Workflows use these agents as steps; changes apply to stages that start after saving." tone="from-emerald-500 to-teal-500">
        <AllAgentsMode agents={agents} />
        <button onClick={() => setShowShared(!showShared)} className="rounded-md border border-border px-4 py-1.5 text-sm font-medium hover:bg-muted">
          Shared texts
        </button>
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New agent
          </button>
        )}
      </PageTitle>
      {showShared && <SharedTextsPanel onClose={() => setShowShared(false)} focus={params.get("text")} />}
      {adding && <NewAgentForm onCancel={() => setAdding(false)} />}
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <nav className="space-y-0.5 lg:sticky lg:top-4 lg:self-start">
          <select
            aria-label="Filter agents by workflow"
            className={`${inputClass} mb-2`}
            value={workflowKey}
            onChange={(e) => setWorkflowKey(e.target.value)}
          >
            <option value="">All agents</option>
            {workflows.map((w) => (
              <option key={w.key} value={w.key}>
                {w.name}
              </option>
            ))}
          </select>
          <input
            type="search"
            aria-label="Search agents"
            className={`${inputClass} mb-2`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents…"
          />
          {shown.length === 0 && (
            <p className="px-2 py-1.5 text-sm text-subtle">
              {query ? `No agent matches “${search.trim()}”${workflow ? ` in ${workflow.name}` : ""}.` : `${workflow?.name ?? "This workflow"} has no agents.`}
            </p>
          )}
          {shown.map((a) => (
            <NavLink
              key={a.key}
              to={`/agents/${a.key}`}
              className={({ isActive }) => `flex items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-muted ${isActive ? "bg-muted font-medium" : ""}`}
            >
              <span className="flex-1">{a.title}</span>
              <LibraryBadge library={a.scope} customised={a.customised} compact />
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
