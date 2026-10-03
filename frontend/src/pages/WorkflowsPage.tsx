import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Agent, type Workflow, type WorkflowStep } from "../lib/api";
import { useAgents, useDomains, useWorkflows } from "../lib/hooks";
import { slug } from "../lib/slug";
import { normalizeSteps, type EditableStep } from "../lib/workflowSteps";
import { Field, inputClass } from "../components/Field";

let nextUid = 1;
const editable = (steps: WorkflowStep[]): EditableStep[] => steps.map((s) => ({ ...s, uid: nextUid++ }));
const toSteps = (steps: EditableStep[]): WorkflowStep[] =>
  steps.map(({ uid: _uid, ...s }) =>
    s.kind === "agent" ? { kind: "agent", key: s.key, uses: s.uses ?? null, include_content: s.include_content ?? false } : { kind: "gate", key: s.key, title: s.title ?? "", description: s.description ?? "", reviews: s.reviews ?? [] },
  );

function NewWorkflowForm({ workflows, agents, onCancel }: { workflows: Workflow[]; agents: Agent[]; onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [from, setFrom] = useState(workflows.find((w) => w.key === "standard")?.key ?? workflows[0]?.key ?? "");
  const key = slug(name, "-");
  const create = useMutation({
    mutationFn: () =>
      api.createWorkflow({
        key,
        name: name.trim(),
        description,
        ...(from ? { copy_from: from } : { steps: [{ kind: "agent", key: agents[0]?.key ?? "" }] }),
      }),
    onSuccess: (w) => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${w.key}`);
      onCancel();
    },
  });
  return (
    <form
      className="space-y-3 rounded-lg border border-border bg-surface p-4"
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

function StepRow({ step, index, steps, agents, onChange, onMove, onRemove, onInsert }: {
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
    <li className={`rounded-lg border bg-surface p-3 ${isGate ? "border-warn/40" : "border-border"}`}>
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

function WorkflowEditor({ workflow, agents }: { workflow: Workflow; agents: Agent[] }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: domains = [] } = useDomains();
  const defaultFor = domains.filter((d) => d.default_workflow === workflow.key);
  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description);
  const [steps, setSteps] = useState(() => editable(workflow.steps));
  useEffect(() => {
    setName(workflow.name);
    setDescription(workflow.description);
    setSteps(editable(workflow.steps));
  }, [workflow]);

  const dirty =
    name !== workflow.name || description !== workflow.description || JSON.stringify(toSteps(steps)) !== JSON.stringify(toSteps(editable(workflow.steps)));
  const save = useMutation({
    mutationFn: () => api.updateWorkflow(workflow.key, { name: name.trim(), description, steps: toSteps(steps) }),
    onSuccess: (saved) => queryClient.setQueryData<Workflow[]>(["workflows"], (prev) => prev?.map((w) => (w.key === saved.key ? saved : w))),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteWorkflow(workflow.key),
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
  const unusedAgent = () => agents.find((a) => !steps.some((s) => s.kind === "agent" && s.key === a.key));
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
        <code className="text-xs">{workflow.key}</code> · {workflow.runs} run{workflow.runs === 1 ? "" : "s"}
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

      <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-border bg-bg py-3">
        <button type="submit" disabled={!dirty || !name.trim() || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Save workflow
        </button>
        {dirty && (
          <button type="button" className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg" onClick={() => {
              setName(workflow.name);
              setDescription(workflow.description);
              setSteps(editable(workflow.steps));
            }}
          >
            Discard changes
          </button>
        )}
        {dirty && <span className="text-sm text-warn">Unsaved changes</span>}
        {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved</span>}
        <button
          type="button"
          className="ml-auto rounded-md px-3 py-1.5 text-sm text-bad hover:bg-bad/10 disabled:opacity-50"
          disabled={remove.isPending}
          onClick={() =>
            window.confirm(
              `Delete the ${workflow.name} workflow?${defaultFor.length ? ` It is the default for ${defaultFor.map((d) => d.name).join(", ")}.` : ""} Runs that used it are not affected.`,
            ) && remove.mutate()
          }
        >
          Delete workflow
        </button>
        {(save.error || remove.error) && <p className="w-full text-sm text-bad">{((save.error || remove.error) as Error).message}</p>}
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Workflows</h1>
          <p className="mt-1 text-sm text-subtle">Agent steps and human approval gates, in order. Pick a workflow when starting a run, or set a default per domain.</p>
        </div>
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New workflow
          </button>
        )}
      </div>
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
                <span className="block">{w.name}</span>
                <span className="block text-xs font-normal text-subtle">
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
