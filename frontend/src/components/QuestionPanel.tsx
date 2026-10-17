import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Markdown } from "./Markdown";
import { api, type Question, type StageDef } from "../lib/api";
import { useCan } from "../lib/hooks";

export function QuestionPanel({ runId, question, stages }: { runId: string; question: Question; stages: StageDef[] }) {
  const queryClient = useQueryClient();
  const canAnswer = useCan("runs.edit");
  const [answer, setAnswer] = useState("");
  const title = stages.find((s) => s.key === question.stage)?.title ?? question.stage;

  const send = useMutation({
    mutationFn: (text: string) => api.answer(question.id, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["questions", runId] });
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
  });

  return (
    <section className="rounded-lg border border-accent/40 bg-accent/5 p-4" aria-label="Agent question">
      <h2 className="font-semibold">The {title} agent has a question</h2>
      <Markdown className="mt-2">{question.question}</Markdown>

      {question.options && (
        <div className="mt-3 flex flex-wrap gap-2">
          {question.options.map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => setAnswer(o)}
              className={`rounded-full border px-3 py-1 text-sm ${answer === o ? "border-accent bg-accent/15 text-accent" : "border-border bg-surface hover:border-accent/50"}`}
            >
              {o}
            </button>
          ))}
        </div>
      )}

      {canAnswer && (
        <label className="mt-4 block text-sm">
          <span className="mb-1 block text-xs font-medium text-subtle">Answer</span>
          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-border bg-surface px-2 py-1.5"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && answer.trim()) send.mutate(answer);
            }}
          />
        </label>
      )}
      {!canAnswer && <p className="mt-4 text-sm text-subtle">Waiting for someone who can answer agents.</p>}

      {send.error && <p className="mt-2 text-sm text-bad">{(send.error as Error).message}</p>}
      {canAnswer && (
        <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
          <button
            className="rounded-md px-3 py-1.5 text-sm text-subtle hover:bg-muted hover:text-fg disabled:opacity-50"
            disabled={send.isPending}
            title="The agent continues using its own judgement"
            onClick={() => send.mutate("")}
          >
            Skip
          </button>
          <button
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            disabled={!answer.trim() || send.isPending}
            onClick={() => send.mutate(answer)}
          >
            Send answer
          </button>
        </div>
      )}
    </section>
  );
}
