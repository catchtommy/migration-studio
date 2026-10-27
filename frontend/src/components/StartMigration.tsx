import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getCurrentProject, type AgentInputs, type Domain, type Feature } from "../lib/api";
import { useWorkflows } from "../lib/hooks";
import { InputsEditor } from "./InputsEditor";
import { Guarded } from "./Guarded";

/** Start one feature's migration where the feature is listed: choose the workflow (the domain's default, else the
 * project's, else the Standard, as the server picks) and, if needed, change the entry points for this run. */
export function StartMigration({ domain, feature, onCancel }: { domain: Domain; feature: Feature; onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: allWorkflows = [] } = useWorkflows();
  const workflows = allWorkflows.filter((w) => w.scope === "operation");
  const { data: project } = useQuery({ queryKey: ["project", getCurrentProject()], queryFn: () => api.project(getCurrentProject()!) });
  const projectDefault = project?.settings.default_workflow as string | undefined;
  const preferred = [domain.default_workflow, projectDefault, "standard"].find((k) => k && workflows.some((w) => w.key === k));
  const [chosen, setChosen] = useState<string | null>(null);
  const workflow = chosen ?? preferred ?? workflows[0]?.key ?? "";

  const defaults: AgentInputs = { paths: feature.entry_points, notes: "" };
  const [inputs, setInputs] = useState<AgentInputs>(defaults);
  const [showInputs, setShowInputs] = useState(false);

  const start = useMutation({
    mutationFn: () => {
      const paths = inputs.paths.map((p) => p.trim()).filter(Boolean);
      const notes = inputs.notes.trim();
      const changed = notes || paths.join("\n") !== defaults.paths.join("\n"); // unchanged: the feature's own entry points
      return api.createRun({ domain: domain.key, operation: feature.key, workflow, inputs: changed ? { paths, notes } : undefined });
    },
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["features", domain.key] });
      navigate(`/runs/${run.id}`);
    },
  });

  return (
    <Guarded permission="runs.start">
      <form
        aria-label={`Start migrating ${feature.name}`}
        className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          start.mutate();
        }}
      >
        <p className="text-sm font-medium">Migrate {feature.name}</p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-subtle">Workflow</span>
            <select value={workflow} onChange={(e) => setChosen(e.target.value)} className="rounded-md border border-border bg-bg px-2 py-1.5">
              {workflows.map((w) => (
                <option key={w.key} value={w.key}>
                  {w.name}
                  {w.key === domain.default_workflow ? " (domain default)" : ""}
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
            {showInputs ? "Hide entry points" : `Add / show entry points${defaults.paths.length ? ` (${defaults.paths.length})` : ""}`}
          </button>
          <span className="ml-auto flex gap-2">
            <button type="button" onClick={onCancel} disabled={start.isPending} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50">
              Cancel
            </button>
            <button
              type="submit"
              disabled={!workflow || start.isPending}
              className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {start.isPending ? "Starting…" : "Start migration"}
            </button>
          </span>
        </div>
        {showInputs && (
          <InputsEditor
            value={inputs}
            onChange={setInputs}
            pathsLabel="Entry points and files for this feature"
            pathsHint="The feature's entry points, one per line. Changes here apply to this run only."
            notesLabel="Notes for the agents"
            notesHint="Given to every agent in this run, alongside the domain's inputs."
          />
        )}
        {start.error && <p className="text-sm text-bad">{(start.error as Error).message}</p>}
      </form>
    </Guarded>
  );
}
