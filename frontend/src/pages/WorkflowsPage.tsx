import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, SCOPE_LABELS, type Agent, type Scope, type StepCode, type Workflow, type WorkflowGit, type WorkflowStep } from "../lib/api";
import { useAgents, useDomains, useWorkflows } from "../lib/hooks";
import { LibraryBadge } from "../components/LibraryBadge";
import { slug } from "../lib/slug";
import { normalizeSteps, type EditableStep } from "../lib/workflowSteps";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { FlowIcon } from "../components/icons";

let nextUid = 1;
const editable = (steps: WorkflowStep[]): EditableStep[] => steps.map((s) => ({ ...s, uid: nextUid++ }));
const toSteps = (steps: EditableStep[]): WorkflowStep[] =>
  steps.map(({ uid: _uid, ...s }) =>
    s.kind === "agent"
      ? { kind: "agent", key: s.key, uses: s.uses ?? null, include_content: s.include_content ?? false, code: s.code ?? null }
      : { kind: "gate", key: s.key, title: s.title ?? "", description: s.description ?? "", reviews: s.reviews ?? [] },
  );

/** The agent a new single-step workflow starts with, by what each run covers. */
const FIRST_AGENT: Record<Scope, string> = { operation: "as_is_doc", domain: "domain_features", monolith: "bounded_contexts" };

function NewWorkflowForm({ workflows, agents, onCancel }: { workflows: Workflow[]; agents: Agent[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [from, setFrom] = useState(workflows.find((w) => w.key === "standard")?.key ?? workflows[0]?.key ?? "");
  const [scope, setScope] = useState<Scope>("operation");
  const key = slug(name, "-");
  const create = useMutation({
    mutationFn: () =>
      api.createWorkflow(
        {
          key,
          name: name.trim(),
          description,
          ...(from ? { copy_from: from } : { scope, steps: [{ kind: "agent", key: (agents.find((a) => a.key === FIRST_AGENT[scope]) ?? agents[0])?.key ?? "" }] }),
        },
        "project", // workflows belong to the project they're made in
      ),
    onSuccess: (w) => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${w.key}`);
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
      <h2 className="font-semibold">New workflow</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" hint={key ? `Key: ${key}` : "e.g. Reporting (read-only)"}>
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Start from">
          <select className={inputClass} value={from} onChange={(e) => setFrom(e.target.value)}>
            {workflows.map((w) => (
              <option key={w.key} value={w.key}>
                Copy of {w.name}
              </option>
            ))}
            <option value="">A single agent step</option>
          </select>
        </Field>
      </div>
      {!from && (
        <Field label="Each run covers" hint="A copy covers what the original covers.">
          <select className={inputClass} value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
            {(Object.keys(SCOPE_LABELS) as Scope[]).map((s) => (
              <option key={s} value={s}>
                {SCOPE_LABELS[s]}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Description">
        <textarea rows={2} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={!key || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Create workflow
        </button>
        <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
        {create.error && <span className="text-sm text-bad">{(create.error as Error).message}</span>}
      </div>
    </form>
  );
}

function IconButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded-md text-subtle hover:bg-muted hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

/** Which earlier steps' documents an agent step receives, and whether their contents go in its prompt. */
function StepInputs({ label, step, earlierAgents, title, onChange }: {
  label: string;
  step: EditableStep;
  earlierAgents: string[];
  title: (key: string) => string;
  onChange: (step: EditableStep) => void;
}) {
  const selected = step.uses != null;
  return (
    <fieldset className="w-full rounded-md bg-muted/60 p-2 text-sm" aria-label={label}>
      <legend className="sr-only">{label}</legend>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-xs font-medium text-subtle">Uses output of</span>
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={!selected} onChange={() => onChange({ ...step, uses: null })} />
          All earlier steps
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={selected} onChange={() => onChange({ ...step, uses: [...earlierAgents] })} />
          Selected steps
        </label>
        <label className="ml-auto flex items-center gap-1.5 text-xs" title="Put the documents' contents in the agent's prompt instead of only their paths">
          <input type="checkbox" checked={step.include_content ?? false} onChange={(e) => onChange({ ...step, include_content: e.target.checked })} />
          Include contents in prompt
        </label>
      </div>
      {selected && (
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
          {earlierAgents.map((k) => (
            <label key={k} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={step.uses!.includes(k)}
                onChange={(e) =>
                  onChange({ ...step, uses: e.target.checked ? earlierAgents.filter((a) => a === k || step.uses!.includes(a)) : step.uses!.filter((u) => u !== k) })
                }
              />
              {title(k)}
            </label>
          ))}
          {step.uses!.length === 0 && <span className="text-xs text-subtle">No earlier documents: this step starts from the code and its inputs only.</span>}
        </div>
      )}
    </fieldset>
  );
}

/** Whether an agent step changes code, and where: the project's monolith, or another repository (e.g. each domain's
 * microservice, with {domain} in its URL, or the orchestrator) from its base branch. */
function StepCodeSettings({ label, step, onChange }: { label: string; step: EditableStep; onChange: (step: EditableStep) => void }) {
  const code = step.code ?? null;
  const other = code !== null && code.repository !== "";
  const [otherUrl, setOtherUrl] = useState(other ? code!.repository : "");
  const set = (next: Partial<StepCode> | null) => onChange({ ...step, code: next === null ? null : { repository: "", base_branch: "", ...code, ...next } });
  return (
    <fieldset className="w-full rounded-md border border-dashed border-border p-2 text-sm" aria-label={label}>
      <legend className="sr-only">{label}</legend>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={code !== null} onChange={(e) => set(e.target.checked ? { repository: "", base_branch: "" } : null)} />
          <span className="font-medium">Changes code</span>
        </label>
        {code !== null && (
          <>
            <label className="flex items-center gap-1.5">
              <input type="radio" checked={!other} onChange={() => set({ repository: "", base_branch: "" })} />
              In the project's monolith
            </label>
            <label className="flex items-center gap-1.5">
              <input type="radio" checked={other} onChange={() => set({ repository: otherUrl || "https://", base_branch: code.base_branch })} />
              In another repository
            </label>
          </>
        )}
        {code === null && <span className="text-xs text-subtle">Writes documents only.</span>}
      </div>
      {other && (
        <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_12rem]">
          <label className="text-xs">
            <span className="mb-0.5 block font-medium text-subtle">Repository</span>
            <input
              aria-label={`${label} repository`}
              className={`${inputClass} font-mono text-xs`}
              placeholder="https://github.com/acme/{domain}-service.git"
              value={code!.repository}
              onChange={(e) => {
                setOtherUrl(e.target.value.trim());
                set({ repository: e.target.value.trim() });
              }}
            />
            <span className="mt-0.5 block text-subtle">{"{domain}"} is filled in per run, so one step serves each domain's own repository.</span>
          </label>
          <label className="text-xs">
            <span className="mb-0.5 block font-medium text-subtle">Base branch</span>
            <input
              aria-label={`${label} base branch`}
              className={`${inputClass} font-mono text-xs`}
              placeholder="default branch"
              value={code!.base_branch}
              onChange={(e) => set({ base_branch: e.target.value.trim() })}
            />
          </label>
        </div>
      )}
    </fieldset>
  );
}

function StepRow({ step, index, steps, agents, codeSteps, onChange, onMove, onRemove, onInsert }: {
  codeSteps?: boolean;
  step: EditableStep;
  index: number;
  steps: EditableStep[];
  agents: Agent[];
  onChange: (step: EditableStep) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
  onInsert: (kind: "agent" | "gate") => void;
}) {
  const title = (key: string) => agents.find((a) => a.key === key)?.title ?? key;
  const earlierAgents = steps.slice(0, index).filter((s) => s.kind === "agent").map((s) => s.key);
  const usedElsewhere = new Set(steps.filter((s) => s.kind === "agent" && s.uid !== step.uid).map((s) => s.key));
  const isGate = step.kind === "gate";

  return (
    <li className={`rounded-xl border border-l-4 p-3 shadow-sm ${isGate ? "border-warn/40 border-l-warn bg-warn/5" : "border-border border-l-accent bg-surface"}`}>
      <div className="flex items-start gap-3">
        <span className="mt-1.5 w-5 shrink-0 text-right text-xs tabular-nums text-subtle">{index + 1}</span>
        <span className={`mt-2 h-3 w-3 shrink-0 border-2 ${isGate ? "rotate-45 rounded-[2px] border-warn" : "rounded-full border-accent"}`} />
        <div className="min-w-0 flex-1 space-y-2">
          {isGate ? (
            <>
              <div className="grid gap-2 sm:grid-cols-[14rem_1fr]">
                <input
                  aria-label={`Step ${index + 1} gate title`}
                  className={`${inputClass} font-medium`}
                  value={step.title ?? ""}
                  placeholder="Approval title"
                  onChange={(e) => onChange({ ...step, title: e.target.value })}
                />
                <input
                  aria-label={`Step ${index + 1} gate description`}
                  className={inputClass}
                  value={step.description ?? ""}
                  placeholder="What the reviewer checks"
                  onChange={(e) => onChange({ ...step, description: e.target.value })}
                />
              </div>
              <fieldset className="text-sm">
                <legend className="mb-1 text-xs font-medium text-subtle">👤 Human approval: reviews (and can send back to)</legend>
                {earlierAgents.length === 0 ? (
                  <p className="text-xs text-bad">Move this gate after at least one agent step.</p>
                ) : (
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    {earlierAgents.map((k) => (
                      <label key={k} className="flex items-center gap-1.5">
                        <input
                          type="checkbox"
                          checked={step.reviews?.includes(k) ?? false}
                          onChange={(e) =>
                            onChange({
                              ...step,
                              reviews: e.target.checked ? earlierAgents.filter((a) => a === k || step.reviews?.includes(a)) : (step.reviews ?? []).filter((r) => r !== k),
                            })
                          }
                        />
                        {title(k)}
                      </label>
                    ))}
                  </div>
                )}
                {earlierAgents.length > 0 && !step.reviews?.length && <p className="mt-1 text-xs text-bad">Select at least one step to review.</p>}
              </fieldset>
            </>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <select aria-label={`Step ${index + 1} agent`} className={`${inputClass} max-w-xs`} value={step.key} onChange={(e) => onChange({ ...step, key: e.target.value })}>
                {agents.map((a) => (
                  <option key={a.key} value={a.key} disabled={usedElsewhere.has(a.key)}>
                    {a.title}
                  </option>
                ))}
              </select>
              <Link to={`/agents/${step.key}`} className="text-xs text-accent hover:underline">
                Configure agent
              </Link>
              <p className="w-full text-xs text-subtle">{agents.find((a) => a.key === step.key)?.description}</p>
              {earlierAgents.length > 0 && <StepInputs label={`Step ${index + 1} inputs`} step={step} earlierAgents={earlierAgents} title={title} onChange={onChange} />}
              {codeSteps && <StepCodeSettings label={`Step ${index + 1} code`} step={step} onChange={onChange} />}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center">
          <IconButton label="Move up" onClick={() => onMove(-1)} disabled={index === 0}>
            ↑
          </IconButton>
          <IconButton label="Move down" onClick={() => onMove(1)} disabled={index === steps.length - 1}>
            ↓
          </IconButton>
          <IconButton label="Delete step" onClick={onRemove}>
            ✕
          </IconButton>
        </div>
      </div>
      <div className="mt-2 flex gap-3 pl-11 text-xs">
        <button type="button" className="text-subtle hover:text-accent" onClick={() => onInsert("agent")} disabled={usedElsewhere.size + 1 >= agents.length}>
          + agent step below
        </button>
        <button type="button" className="text-subtle hover:text-accent" onClick={() => onInsert("gate")}>
          + approval gate below
        </button>
      </div>
    </li>
  );
}

/** Where a migration's code changes go: the repository, the base branch the feature branch is cut from, the feature
 * branch's name, and whether the run ends with a review and commit. */
function GitSettings({ value, onChange }: { value: WorkflowGit; onChange: (git: WorkflowGit) => void }) {
  const set = (field: keyof WorkflowGit, v: string | boolean) => onChange({ ...value, [field]: v });
  return (
    <section className="space-y-3">
      <h2 className="font-semibold">Git</h2>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5" checked={value.commit} onChange={(e) => set("commit", e.target.checked)} />
        <span>
          Review and commit the code changes when the run finishes
          <span className="block text-xs text-subtle">
            The run waits as <em>Ready to commit</em>: someone reviews every changed file, resolves conflicts with the latest base branch, and pushes the feature branch with
            their own git access.
          </span>
        </span>
      </label>
      <p className="text-sm text-subtle">
        Which repository each step changes is set on the step (<em>Changes code</em>). A run cuts the same feature branch in every repository its steps change, each from
        that repository's base branch (the monolith's is the project's base branch).
      </p>
      <Field label="Feature branch name" hint="{domain}, {feature}, {run} (short run id) and {date} are filled in. It can be changed when starting a run.">
        <input className={`${inputClass} max-w-md font-mono`} value={value.branch_pattern} onChange={(e) => set("branch_pattern", e.target.value.trim())} />
      </Field>
    </section>
  );
}

function WorkflowEditor({ workflow, agents }: { workflow: Workflow; agents: Agent[] }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: domains = [] } = useDomains();
  const defaultFor = domains.filter((d) => d.default_workflow === workflow.key);
  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description);
  const [steps, setSteps] = useState(() => editable(workflow.steps));
  const [git, setGit] = useState<WorkflowGit>(workflow.git);
  useEffect(() => {
    setName(workflow.name);
    setDescription(workflow.description);
    setSteps(editable(workflow.steps));
    setGit(workflow.git);
  }, [workflow]);

  const dirty =
    name !== workflow.name ||
    description !== workflow.description ||
    JSON.stringify(toSteps(steps)) !== JSON.stringify(toSteps(editable(workflow.steps))) ||
    JSON.stringify(git) !== JSON.stringify(workflow.git);
  const onSaved = (saved: Workflow) => queryClient.setQueryData<Workflow[]>(["workflows"], (prev) => prev?.map((w) => (w.key === saved.key ? saved : w)));
  const save = useMutation({
    mutationFn: () =>
      api.updateWorkflow(workflow.key, { name: name.trim(), description, steps: toSteps(steps), ...(workflow.scope === "operation" ? { git } : {}) }, "project"), // changes apply to this project only
    onSuccess: onSaved,
  });
  const revert = useMutation({ mutationFn: () => api.revertWorkflow(workflow.key), onSuccess: onSaved });
  const remove = useMutation({
    mutationFn: () => api.deleteWorkflow(workflow.key, workflow.library),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      navigate("/workflows");
    },
  });

  const update = (next: EditableStep[]) => {
    save.reset();
    setSteps(normalizeSteps(next));
  };
  // A migration workflow suggests migration agents before the discovery ones.
  const unusedAgent = () => {
    const unused = agents.filter((a) => !steps.some((s) => s.kind === "agent" && s.key === a.key));
    const discovery = Object.values(FIRST_AGENT).filter((k) => k !== FIRST_AGENT.operation);
    return (workflow.scope === "operation" && unused.find((a) => !discovery.includes(a.key))) || unused[0];
  };
  const newStep = (kind: "agent" | "gate", at: number): EditableStep | null => {
    if (kind === "agent") {
      const agent = unusedAgent();
      return agent ? { kind: "agent", key: agent.key, uid: nextUid++ } : null;
    }
    const before = steps.slice(0, at).filter((s) => s.kind === "agent");
    const last = before[before.length - 1];
    return { kind: "gate", key: "", title: "Approve", description: "", reviews: last ? [last.key] : [], uid: nextUid++ };
  };
  const insert = (kind: "agent" | "gate", at: number) => {
    const step = newStep(kind, at);
    if (step) update([...steps.slice(0, at), step, ...steps.slice(at)]);
  };
  const move = (i: number, delta: number) => {
    const next = [...steps];
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    update(next);
  };

  return (
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <LibraryBadge library={workflow.library} customised={workflow.customised} />
      <div className="grid gap-4 sm:grid-cols-[16rem_1fr]">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => {
              save.reset();
              setName(e.target.value);
            }}
          />
        </Field>
        <Field label="Description">
          <input className={inputClass} value={description} onChange={(e) => {
              save.reset();
              setDescription(e.target.value);
            }}
          />
        </Field>
      </div>
      <p className="text-sm text-subtle">
        <code className="text-xs">{workflow.key}</code> · {SCOPE_LABELS[workflow.scope]} · {workflow.runs} run{workflow.runs === 1 ? "" : "s"}
        {defaultFor.length > 0 && <> · default for {defaultFor.map((d) => d.name).join(", ")}</>}. Changes apply to runs started afterwards; runs in progress keep the
        workflow they started with.
      </p>

      <section className="space-y-2">
        <h2 className="font-semibold">Steps</h2>
        <ol className="space-y-2">
          {steps.map((step, i) => (
            <StepRow
              key={step.uid}
              step={step}
              index={i}
              steps={steps}
              agents={agents}
              codeSteps={workflow.scope === "operation"}
              onChange={(s) => update(steps.map((x) => (x.uid === s.uid ? s : x)))}
              onMove={(delta) => move(i, delta)}
              onRemove={() => update(steps.filter((x) => x.uid !== step.uid))}
              onInsert={(kind) => insert(kind, i + 1)}
            />
          ))}
        </ol>
        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:border-accent/50 disabled:opacity-50"
            disabled={!unusedAgent()}
            title={unusedAgent() ? undefined : "Every agent is already in this workflow; create another on the Agents page"}
            onClick={() => insert("agent", steps.length)}
          >
            + Add agent step
          </button>
          <button type="button" className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:border-warn/50" onClick={() => insert("gate", steps.length)}>
            + Add approval gate
          </button>
        </div>
      </section>

      {workflow.scope === "operation" && <GitSettings value={git} onChange={setGit} />}

      <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-border bg-bg py-3">
        <button type="submit" disabled={!dirty || !name.trim() || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Save workflow
        </button>
        <span className="text-xs text-subtle">Changes apply to this project only.</span>
        {workflow.customised && (
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg"
            disabled={revert.isPending}
            onClick={() => window.confirm(`Drop this project's changes to ${workflow.name} and go back to the standard version?`) && revert.mutate()}
          >
            Reset to standard
          </button>
        )}
        {dirty && (
          <button type="button" className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg" onClick={() => {
              setName(workflow.name);
              setDescription(workflow.description);
              setSteps(editable(workflow.steps));
              setGit(workflow.git);
            }}
          >
            Discard changes
          </button>
        )}
        {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
        {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved</span>}
        {workflow.library === "project" && !workflow.customised && (
          <button
            type="button"
            className="ml-auto rounded-md px-3 py-1.5 text-sm text-bad hover:bg-bad/10 disabled:opacity-50"
            disabled={remove.isPending}
            onClick={() =>
              window.confirm(
                `Delete the ${workflow.name} workflow from this project?${defaultFor.length ? ` It is the default for ${defaultFor.map((d) => d.name).join(", ")}.` : ""} Runs that used it are not affected.`,
              ) && remove.mutate()
            }
          >
            Delete workflow
          </button>
        )}
        {(save.error || remove.error || revert.error) && (
          <p className="w-full text-sm text-bad">{((save.error || remove.error || revert.error) as Error).message}</p>
        )}
      </div>
    </form>
  );
}

export function WorkflowsPage() {
  const { workflowKey: selected } = useParams();
  const navigate = useNavigate();
  const { data: workflows } = useWorkflows();
  const { data: agents } = useAgents();
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!selected && workflows?.length) navigate(`/workflows/${workflows[0].key}`, { replace: true });
  }, [selected, workflows, navigate]);

  if (!workflows || !agents) return <p className="text-subtle">Loading…</p>;
  const workflow = workflows.find((w) => w.key === selected);

  return (
    <div className="space-y-6">
      <PageTitle icon={<FlowIcon />} title="Workflows" subtitle="Agent steps and human approval gates, in order. Pick a workflow when starting a run, or set a default per domain." tone="from-amber-500 to-rose-500">
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New workflow
          </button>
        )}
      </PageTitle>
      {adding && <NewWorkflowForm workflows={workflows} agents={agents} onCancel={() => setAdding(false)} />}
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <nav className="space-y-0.5 lg:sticky lg:top-4 lg:self-start">
          {workflows.map((w) => {
            const gates = w.stages.filter((s) => s.kind === "gate").length;
            return (
              <NavLink
                key={w.key}
                to={`/workflows/${w.key}`}
                className={({ isActive }) => `block rounded-md px-2 py-1.5 text-sm hover:bg-muted ${isActive ? "bg-muted font-medium" : ""}`}
              >
                <span className="flex items-center gap-2">
                  <span className="flex-1">{w.name}</span>
                  <LibraryBadge library={w.library} customised={w.customised} compact />
                </span>
                <span className="block text-xs font-normal text-subtle">
                  {w.scope !== "operation" && <span className="text-accent">{w.scope === "monolith" ? "Discovery" : "Features"} · </span>}
                  {w.stages.length - gates} agent{w.stages.length - gates === 1 ? "" : "s"} · {gates} approval{gates === 1 ? "" : "s"}
                </span>
              </NavLink>
            );
          })}
        </nav>
        <main className="min-w-0">{workflow ? <WorkflowEditor key={workflow.key} workflow={workflow} agents={agents} /> : selected && <p className="text-sm text-subtle">Workflow not found.</p>}</main>
      </div>
    </div>
  );
}
