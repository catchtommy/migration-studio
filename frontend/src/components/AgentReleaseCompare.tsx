import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type AgentComparison, type ReleaseStatus } from "../lib/api";
import { fold, sideBySide } from "../lib/lineDiff";

/** How a built-in agent stands against this release's default (none for a custom agent). */
const STATUS: Record<ReleaseStatus, { label: string; short: string; title: string; className: string }> = {
  current: {
    label: "Release default",
    short: "",
    title: "Everything a release sets (instructions, required documents, tools, checks, guardrails) is this release's default.",
    className: "bg-ok/15 text-ok",
  },
  "older-default": {
    label: "Older release default",
    short: "older",
    title: "An earlier release's default that nobody edited: the next start of the studio brings it up to this release.",
    className: "bg-warn/15 text-warn",
  },
  edited: {
    label: "Edited since release",
    short: "edited",
    title: "Differs from this release's default, and a release never overwrites it. Compare with release shows what differs; Reset to default takes the release's.",
    className: "bg-accent/15 text-accent",
  },
};

export function ReleaseBadge({ status, compact }: { status?: ReleaseStatus | null; compact?: boolean }) {
  if (!status || (compact && status === "current")) return null;
  const s = STATUS[status];
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${s.className}`} title={s.title}>
      {compact ? s.short : s.label}
    </span>
  );
}

const FIELD_LABELS: Record<string, string> = {
  instructions: "Instructions",
  outputs: "Required documents",
  tools: "Tools",
  checks: "Checks",
  guardrails: "Guardrails",
};

/** The agent against this release's default, field by field: what differs, side by side (read only). */
export function CompareWithRelease({ agentKey, onClose }: { agentKey: string; onClose: () => void }) {
  const { data, error, isLoading } = useQuery({ queryKey: ["agent-compare", agentKey], queryFn: () => api.compareAgent(agentKey) });
  return (
    <section aria-label="Compare with release" className="card space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Compare with this release's default</h3>
        {data && <ReleaseBadge status={data.status} />}
        <button type="button" className="ml-auto text-xs font-medium text-accent hover:underline" onClick={onClose}>
          Close
        </button>
      </div>
      {isLoading && <p className="text-sm text-subtle">Comparing…</p>}
      {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
      {data && <Fields data={data} />}
    </section>
  );
}

function Fields({ data }: { data: AgentComparison }) {
  const differing = data.fields.filter((f) => !f.same);
  if (differing.length === 0) return <p className="text-sm text-ok">Identical to this release's default.</p>;
  return (
    <div className="space-y-4">
      <p className="text-xs text-subtle">
        Left: what this agent has now{data.scope === "project" ? " (this project's copy)" : ""}. Right: this release's default. Same in both:{" "}
        {data.fields
          .filter((f) => f.same)
          .map((f) => FIELD_LABELS[f.field] ?? f.field)
          .join(", ") || "nothing"}
        .
      </p>
      {differing.map((f) => (
        <FieldDiff key={f.field} label={FIELD_LABELS[f.field] ?? f.field} stored={f.stored} defaultText={f.default} />
      ))}
    </div>
  );
}

function FieldDiff({ label, stored, defaultText }: { label: string; stored: string; defaultText: string }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const rows = fold(sideBySide(stored, defaultText), 2, open);
  const cell = "w-1/2 whitespace-pre-wrap break-words px-2 py-0.5 align-top font-mono text-[11px] leading-5";
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">{label}</h4>
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full table-fixed border-collapse">
          <thead className="text-xs text-subtle">
            <tr>
              <th className="border-b border-border px-2 py-1 text-left font-medium">This agent</th>
              <th className="border-b border-l border-border px-2 py-1 text-left font-medium">Release default</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) =>
              r.kind === "skip" ? (
                <tr key={i}>
                  <td colSpan={2} className="bg-muted/50 px-2 py-0.5 text-center text-xs">
                    <button type="button" className="text-accent hover:underline" onClick={() => setOpen((o) => new Set(o).add(r.from))}>
                      {r.count} unchanged line{r.count === 1 ? "" : "s"}
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={i}>
                  <td className={`${cell} ${r.kind === "del" || r.kind === "mod" ? "bg-bad/10" : ""}`}>{"left" in r ? r.left.text : ""}</td>
                  <td className={`${cell} border-l border-border ${r.kind === "add" || r.kind === "mod" ? "bg-ok/10" : ""}`}>{"right" in r ? r.right.text : ""}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
