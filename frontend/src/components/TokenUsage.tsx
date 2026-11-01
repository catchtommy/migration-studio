import type { StageExecution, StepUsage } from "../lib/api";

const n = (v: number | undefined) => (v ?? 0).toLocaleString();
const name = (path: string) => path.split("/").pop() ?? path;

/** The AI tokens of one step attempt (app/agents/usage.py): totals of its model calls, and per document the output
 * tokens of the responses that wrote it. */
export function TokenUsage({ execution, attempts }: { execution: StageExecution; attempts: StageExecution[] }) {
  const used = execution.usage;
  const all = attempts.filter((a) => a.usage);
  if (!used)
    return (
      <p className="text-sm text-subtle">
        No AI tokens recorded for this attempt (the stub agent, a skipped step, or a run from before tokens were recorded).
      </p>
    );
  const docs = Object.entries(used.documents ?? {}).sort((a, b) => b[1].output_tokens - a[1].output_tokens);
  const code = Object.values(used.code ?? {});
  const codeOut = code.reduce((s, c) => s + c.output_tokens, 0);
  const attributed = docs.reduce((s, [, d]) => s + d.output_tokens, 0) + codeOut;
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {[
          ["Total", used.input_tokens + used.output_tokens, "input + output"],
          ["Input", used.input_tokens, "everything sent to the model, every call"],
          ["Output", used.output_tokens, "reasoning, answers and tool calls"],
          ["Cache read", used.cache_read_tokens, "input served from the prompt cache"],
          ["Model calls", used.calls, used.models.join(", ")],
        ].map(([label, value, hint]) => (
          <div key={label as string} className="rounded-md border border-border bg-surface p-2" title={hint as string}>
            <dt className="text-xs text-subtle">{label}</dt>
            <dd className="font-mono text-base tabular-nums">{n(value as number)}</dd>
          </div>
        ))}
      </dl>
      <section aria-label="Tokens per document">
        <h3 className="mb-1 font-medium">Per document</h3>
        <p className="mb-2 text-xs text-subtle">
          Output tokens of the model responses that wrote or edited each document (a response writing several splits its tokens by how much it wrote
          to each). Reading the code and the earlier documents is input for the whole step, not one document.
        </p>
        {docs.length === 0 ? (
          <p className="text-subtle">No document written by a model response.</p>
        ) : (
          <table className="w-full text-left">
            <thead className="text-xs text-subtle">
              <tr>
                <th className="py-1 font-medium">Document</th>
                <th className="py-1 text-right font-medium">Output tokens to write</th>
                <th className="py-1 text-right font-medium">Writes and edits</th>
                <th className="py-1 text-right font-medium" title="About four characters a token">≈ Size in tokens</th>
              </tr>
            </thead>
            <tbody>
              {docs.map(([path, d]) => (
                <tr key={path} className="border-t border-border">
                  <td className="py-1 font-mono text-xs" title={path}>
                    {name(path)}
                  </td>
                  <td className="py-1 text-right font-mono tabular-nums">{n(d.output_tokens)}</td>
                  <td className="py-1 text-right font-mono tabular-nums">{n(d.writes)}</td>
                  <td className="py-1 text-right font-mono tabular-nums">{d.size_tokens ? n(d.size_tokens) : "–"}</td>
                </tr>
              ))}
              {code.length > 0 && (
                <tr className="border-t border-border text-subtle">
                  <td className="py-1">Code ({code.length} file{code.length === 1 ? "" : "s"})</td>
                  <td className="py-1 text-right font-mono tabular-nums">{n(codeOut)}</td>
                  <td className="py-1 text-right font-mono tabular-nums">{n(code.reduce((s, c) => s + c.writes, 0))}</td>
                  <td />
                </tr>
              )}
              <tr className="border-t border-border text-subtle">
                <td className="py-1">Reasoning, reading and other calls</td>
                <td className="py-1 text-right font-mono tabular-nums">{n(Math.max(0, used.output_tokens - attributed))}</td>
                <td />
                <td />
              </tr>
            </tbody>
          </table>
        )}
      </section>
      {all.length > 1 && (
        <p className="text-xs text-subtle">
          All {all.length} attempts of this step: {n(all.reduce((s, a) => s + (a.usage!.input_tokens + a.usage!.output_tokens), 0))} tokens.
        </p>
      )}
    </div>
  );
}

/** The run's tokens: every step attempt's, added up. */
export function runTokens(stages: StageExecution[]): { total: number; input: number; output: number } {
  return stages.reduce(
    (t, e: StageExecution) => {
      const u: StepUsage | null | undefined = e.usage;
      return u ? { total: t.total + u.input_tokens + u.output_tokens, input: t.input + u.input_tokens, output: t.output + u.output_tokens } : t;
    },
    { total: 0, input: 0, output: 0 },
  );
}
