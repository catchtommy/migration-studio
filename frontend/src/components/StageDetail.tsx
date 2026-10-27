import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AuditEvent, type Question, type RunDetail, type StageDef } from "../lib/api";
import { stageStatuses } from "../lib/stageStatus";
import { ActivityLog } from "./ActivityLog";
import { ArtifactViewer } from "./ArtifactViewer";
import { RestartPanel } from "./RestartPanel";
import { chosen, DocumentPicker, type DocumentChoice } from "./DocumentPicker";
import { DiffView } from "./DiffView";
import { DownloadButton } from "./DownloadButton";
import { StatusBadge } from "./StatusBadge";
import { useCan } from "../lib/hooks";

type Tab = "output" | "changes" | "activity";

function StageInputs({ stage, stages }: { stage: StageDef; stages: StageDef[] }) {
  const earlier = stages.slice(0, stages.findIndex((s) => s.key === stage.key)).filter((s) => s.kind === "agent");
  const used = stage.uses == null ? earlier : earlier.filter((s) => stage.uses!.includes(s.key));
  if (earlier.length === 0) return null;
  return (
    <p className="mt-1 text-xs text-subtle">
      Uses output of: {used.length ? used.map((s) => s.title).join(", ") : "no earlier steps"}
      {stage.include_content && used.length > 0 && " (contents included in its prompt)"}
    </p>
  );
}

export function StageDetail({ stage, stages, run, events, questions }: {
  stage: StageDef;
  stages: StageDef[];
  run: RunDetail;
  events: AuditEvent[];
  questions: Question[];
}) {
  const { status, attempts } = stageStatuses(stages, run).get(stage.key)!;
  const [attemptIdx, setAttemptIdx] = useState(attempts.length - 1);
  const [tab, setTab] = useState<Tab>("output");
  const { data: artifacts = [], isLoading: loadingArtifacts } = useQuery({ queryKey: ["artifacts", run.id], queryFn: () => api.artifacts(run.id) });

  useEffect(() => setAttemptIdx(attempts.length - 1), [stage.key, attempts.length]);

  const execution = attempts[attemptIdx];
  const isLatest = attemptIdx === attempts.length - 1;
  const canEdit = useCan("runs.edit");
  const editable = canEdit && isLatest && (run.status === "awaiting_approval" || run.status === "changes_requested") && !!run.pending_gate?.reviews.includes(stage.key);
  const shown = artifacts.filter((a) => a.stage === stage.key && a.attempt === execution?.attempt);
  const [doc, setDoc] = useState<DocumentChoice>("all");
  useEffect(() => setDoc("all"), [stage.key, execution?.attempt]);
  const gateApprovals = run.approvals.filter((a) => a.gate === stage.key);
  const title = (key: string) => stages.find((s) => s.key === key)?.title ?? key;
  const answered = questions.filter((q) => q.stage === stage.key && q.attempt === execution?.attempt && q.status === "answered");

  return (
    <div className="space-y-4">
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold">{stage.title}</h2>
          <StatusBadge status={run.status === "changes_requested" && run.pending_rerun?.gate === stage.key ? "changes_requested" : status} />
          <RestartPanel run={run} stage={stage} stages={stages} completed={attempts.some((a) => a.status === "completed")} />
        </div>
        <p className="mt-1 text-sm text-subtle">{stage.description}</p>
        {stage.kind === "agent" && <StageInputs stage={stage} stages={stages} />}
      </header>

      {attempts.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {attempts.map((a, i) => (
            <button
              key={a.id}
              onClick={() => setAttemptIdx(i)}
              className={`rounded-md px-2 py-1 text-xs ${i === attemptIdx ? "bg-fg text-bg" : "bg-muted text-subtle hover:text-fg"}`}
            >
              Attempt {a.attempt} · {a.status}
              {a.rerun ? " · re-run" : ""}
            </button>
          ))}
        </div>
      )}

      {execution?.feedback && stage.kind === "agent" && (
        <div className="rounded-md border border-warn/40 bg-warn/5 p-3 text-sm">
          <span className="font-medium">{execution.rerun ? "Notes for this re-run:" : "Reviewer feedback for this attempt:"}</span> {execution.feedback}
        </div>
      )}
      {stage.kind === "gate" && execution?.checklist && execution.checklist.length > 0 && execution.status !== "awaiting_approval" && (
        <div className="rounded-md border border-border bg-surface p-3 text-sm">
          <span className="font-medium">Checklist ticked at this attempt:</span>
          <ul className="mt-1 space-y-0.5">
            {execution.checklist.map((t) => (
              <li key={t.item}>
                ☑ {t.item} <span className="text-xs text-subtle">— {t.by}, {new Date(t.at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {execution?.error && <div className="rounded-md border border-bad/40 bg-bad/5 p-3 text-sm text-bad">{execution.error}</div>}

      {answered.length > 0 && (
        <details className="rounded-md border border-border bg-surface p-3 text-sm">
          <summary className="cursor-pointer font-medium">
            {answered.length} question{answered.length > 1 ? "s" : ""} answered during this attempt
          </summary>
          <dl className="mt-3 space-y-3">
            {answered.map((q) => (
              <div key={q.id}>
                <dt className="whitespace-pre-wrap text-subtle">{q.question}</dt>
                <dd className="mt-1 whitespace-pre-wrap">
                  {q.answer} <span className="text-xs text-subtle">— {q.answered_by}</span>
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {stage.kind === "gate" ? (
        <div className="space-y-3 text-sm">
          <p>
            Reviews: <span className="font-medium">{stage.reviews.map(title).join(", ")}</span>
          </p>
          {gateApprovals.length === 0 ? (
            <p className="text-subtle">No decisions yet.</p>
          ) : (
            <ul className="space-y-2">
              {gateApprovals.map((a) => (
                <li key={a.id} className="rounded-md border border-border bg-surface p-3">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={a.decision === "approve" ? "approved" : "rejected"} />
                    <span className="font-medium">{a.reviewer}</span>
                    <span className="text-xs text-subtle">{new Date(a.created_at).toLocaleString()}</span>
                    {a.target_stage && <span className="text-xs text-subtle">→ re-run from {title(a.target_stage)}</span>}
                  </div>
                  {a.comments && <p className="mt-2 whitespace-pre-wrap">{a.comments}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : !execution ? (
        <p className="text-sm text-subtle">This stage has not run yet.</p>
      ) : (
        <>
          <nav className="flex gap-4 border-b border-border text-sm">
            {(["output", "changes", "activity"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`-mb-px border-b-2 pb-2 capitalize ${tab === t ? "border-accent font-medium" : "border-transparent text-subtle hover:text-fg"}`}
              >
                {t}
              </button>
            ))}
          </nav>
          {tab === "output" &&
            (shown.length === 0 ? (
              <p className="text-sm text-subtle">{loadingArtifacts ? "Loading documents…" : "No artifacts for this attempt."}</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  {shown.length > 1 ? <DocumentPicker docs={shown} value={doc} onChange={setDoc} /> : <span />}
                  <DownloadButton
                    label="Download all documents (.zip)"
                    title="Every document of this run, from all its agents (latest attempt of each step)"
                    onDownload={() => api.downloadRunDocuments(run.id)}
                  />
                </div>
                {chosen(shown, doc).map((a) => (
                  <ArtifactViewer key={a.id} artifactId={a.id} editable={editable} />
                ))}
              </>
            ))}
          {tab === "changes" && <DiffView runId={run.id} executionId={execution.id} />}
          {tab === "activity" && <ActivityLog events={events} stage={stage.key} />}
        </>
      )}
    </div>
  );
}
