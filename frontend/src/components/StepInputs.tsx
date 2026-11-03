import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type StageExecution } from "../lib/api";
import { shortPath } from "./DocumentPicker";

const NOT_RECORDED = "Not recorded for this attempt: steps record their inputs and prompt from this release on.";

/** A link to one version of a document on this run's page: that step, that attempt, that document. */
export function documentLink(stage: string, attempt: number, artifactId: number | null): string {
  return `?step=${encodeURIComponent(stage)}&attempt=${attempt}${artifactId ? `&doc=${artifactId}` : ""}`;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="text-xs text-subtle hover:text-fg"
      onClick={(e) => {
        e.preventDefault();
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function PromptSection({ title, text }: { title: string; text: string }) {
  return (
    <details className="rounded-md border border-border bg-surface p-3 text-sm">
      <summary className="flex cursor-pointer items-center justify-between gap-2 font-medium">
        <span>{title}</span>
        <CopyButton text={text} />
      </summary>
      <pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs">{text}</pre>
    </details>
  );
}

/** What a step attempt was given: the earlier documents, at the version it used, and the full prompt. Shown only to
 * users with runs.inspect. */
export function StepInputs({ runId, execution }: { runId: string; execution: StageExecution }) {
  const prompt = useQuery({
    queryKey: ["stagePrompt", runId, execution.id],
    queryFn: () => api.stagePrompt(runId, execution.id),
    retry: false,
  });
  const inputs = execution.inputs;

  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Documents used</h3>
        {inputs == null ? (
          <p className="text-sm text-subtle">{NOT_RECORDED}</p>
        ) : inputs.length === 0 ? (
          <p className="text-sm text-subtle">No earlier documents: this step started from the code alone.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-subtle">
              <tr>
                <th className="py-1 font-normal">Document</th>
                <th className="py-1 font-normal">From step</th>
                <th className="py-1 font-normal">Version</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {inputs.map((i) => (
                <tr key={i.path} className="border-t border-border align-top">
                  <td className="py-1.5 pr-3 font-mono text-xs">{shortPath(i.path)}</td>
                  <td className="py-1.5 pr-3">{i.stage_title}</td>
                  <td className="py-1.5 pr-3">
                    attempt {i.attempt}
                    {i.newer_attempt != null && <div className="text-xs text-warn">⚠ a newer attempt ({i.newer_attempt}) exists</div>}
                    {i.changed && <div className="text-xs text-subtle">changed since this step's last attempt</div>}
                  </td>
                  <td className="py-1.5 text-right">
                    <Link to={documentLink(i.stage, i.attempt, i.artifact_id)} className="text-accent hover:underline">
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Prompt</h3>
        {prompt.isLoading ? (
          <p className="text-sm text-subtle">Loading…</p>
        ) : prompt.error || !prompt.data ? (
          <p className="text-sm text-subtle">{NOT_RECORDED}</p>
        ) : (
          <>
            <p className="text-xs text-subtle">
              As given when the attempt started
              {prompt.data.mode === "stub" && " (a stub run: what an AI agent would have been given)"}
              {prompt.data.mode !== "stub" && prompt.data.provider === "copilot" && ". With Copilot, the system prompt is appended to Copilot's own"}.
            </p>
            <PromptSection title="System prompt (instructions and guardrails)" text={prompt.data.system} />
            <PromptSection title="Task message (documents, domain guidance, notes and answers)" text={prompt.data.brief} />
            <PromptSection title={`Tools (${prompt.data.tools.length})`} text={prompt.data.tools.join("\n")} />
          </>
        )}
      </section>
    </div>
  );
}
