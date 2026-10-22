import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, MONOLITH, runSubject } from "../lib/api";
import { useRunEvents, useWorkflows, useCan } from "../lib/hooks";
import { ActivityLog } from "../components/ActivityLog";
import { AmendPanel } from "../components/AmendPanel";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { QuestionPanel } from "../components/QuestionPanel";
import { StageDetail } from "../components/StageDetail";
import { StageTimeline } from "../components/StageTimeline";
import { CommitReview, DeliverySummary } from "../components/CommitReview";
import { BranchConfirmation } from "../components/BranchConfirmation";
import { PublishDocsPanel } from "../components/PublishDocsPanel";
import { StatusBadge } from "../components/StatusBadge";
import { DomainAvatar } from "../components/DomainAvatar";
import { WorkflowUpdate } from "../components/WorkflowUpdate";
import { StartOver } from "../components/StartOver";
import { FailedRun, StopRun } from "../components/RunControls";

export function RunPage() {
  const canRetry = useCan("runs.start");
  const { runId = "" } = useParams();
  const { data: workflows = [] } = useWorkflows();
  const { data: run, error } = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId) });
  const events = useRunEvents(runId);
  const { data: questions = [] } = useQuery({ queryKey: ["questions", runId], queryFn: () => api.questions(runId) });
  const openQuestion = questions.find((q) => q.status === "open");
  const [selected, setSelected] = useState<string | null>(null);

  // Follow the run's current stage until the user picks one themselves.
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned && run?.current_stage) setSelected(run.current_stage);
  }, [run?.current_stage, pinned]);

  if (error) return <p className="text-bad">{(error as Error).message}</p>;
  if (!run) return <p className="text-subtle">Loading…</p>;

  const stages = run.workflow_stages;
  const workflowName = workflows.find((w) => w.key === run.workflow)?.name ?? run.workflow;

  const stage = stages.find((s) => s.key === selected);

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="text-sm text-subtle hover:text-fg">
          ← All migrations
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <DomainAvatar name={run.domain === MONOLITH ? "Monolith" : run.domain} domainKey={run.domain} />
          <h1 className="text-2xl font-semibold tracking-tight">
            {run.scope === "operation" ? (
              <>
                <span className="text-subtle">{run.domain} /</span> {run.operation}
              </>
            ) : (
              runSubject(run)
            )}
          </h1>
          <StatusBadge status={run.status} />
          <StopRun run={run} />
        </div>
        <p className="mt-1 text-xs text-subtle">
          <span className="font-mono">{run.branch}</span>
          {run.repository && run.base_branch && <span className="font-mono"> ← {run.base_branch}</span>}
          {!!run.repositories?.length && <> · also in {run.repositories.map((r) => r.name).join(", ")}</>}
          {workflowName && <> · {workflowName} workflow</>}
          {run.base_sha && (
            <>
              {" "}
              · from monolith commit <span className="font-mono">{run.base_sha.slice(0, 10)}</span>
            </>
          )}
          {run.legacy && (
            <span title="Shared by the project's runs; never changed">
              {" "}
              · reads the monolith (read-only) at <span className="font-mono">{run.legacy.sha.slice(0, 10)}</span>
            </span>
          )}
        </p>
        {!!run.monolith_behind && run.status !== "awaiting_commit" && !run.delivery?.repos?.monolith && (
          <p className="mt-2 rounded-md bg-warn/10 px-3 py-1.5 text-sm text-warn">
            The monolith has {run.monolith_behind} newer commit{run.monolith_behind === 1 ? "" : "s"} on the project's base branch since this run started. This run keeps
            working on the commit it started from; a new run starts from the latest.
          </p>
        )}
        <WorkflowUpdate run={run} workflowName={workflowName ?? null} canUpdate={canRetry} />
        <div className="mt-2">
          <StartOver run={run} />
        </div>
        {run.inputs && (run.inputs.paths.length > 0 || run.inputs.notes) && (
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer text-subtle hover:text-fg">{run.scope === "operation" ? "Operation inputs" : "Paths and notes"} given to the agents</summary>
            <div className="mt-2 space-y-2 rounded-md border border-border bg-surface p-3">
              {run.inputs.paths.length > 0 && (
                <ul className="font-mono text-xs">
                  {run.inputs.paths.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {run.inputs.notes && <p className="whitespace-pre-wrap">{run.inputs.notes}</p>}
            </div>
          </details>
        )}
      </div>

      {run.status === "awaiting_input" && openQuestion && <QuestionPanel key={openQuestion.id} runId={run.id} question={openQuestion} stages={stages} />}

      {run.status === "awaiting_approval" && run.pending_gate && (
        <ApprovalPanel key={run.pending_gate.gate + run.stages.length} runId={run.id} gate={run.pending_gate} stages={stages} />
      )}

      {run.status === "awaiting_branches" && run.pending_branches && <BranchConfirmation key={`${run.pending_branches.stage}:${run.pending_branches.repository.key}`} runId={run.id} pending={run.pending_branches} />}
      {run.status === "awaiting_publish" && <PublishDocsPanel key={run.current_stage ?? ""} run={run} />}
      {run.delivery?.docs && run.status !== "awaiting_publish" && (
        <p className="card px-4 py-2.5 text-sm text-subtle">
          AS-IS documentation published to <code className="text-xs">{run.delivery.docs.branch}</code> as{" "}
          <code className="text-xs">{run.delivery.docs.sha.slice(0, 10)}</code> by {run.delivery.docs.by}, {new Date(run.delivery.docs.at).toLocaleString()}.
        </p>
      )}
      {run.status === "awaiting_commit" && <CommitReview run={run} />}
      {run.status === "completed" && <DeliverySummary run={run} />}

      {run.status === "changes_requested" && run.pending_rerun && <AmendPanel key={run.pending_rerun.gate + run.stages.length} run={run} />}

      {run.status === "failed" && <FailedRun key={run.stages.length} run={run} />}

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <aside className="card p-2 lg:sticky lg:top-4 lg:self-start">
          <StageTimeline
            stages={stages}
            run={run}
            selected={selected}
            onSelect={(s) => {
              setSelected(s);
              setPinned(true);
            }}
          />
        </aside>
        <main className="min-w-0">
          {stage ? (
            <StageDetail stage={stage} stages={stages} run={run} events={events} questions={questions} />
          ) : (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold">Activity</h2>
              <ActivityLog events={events} />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
