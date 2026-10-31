import type { StageDef } from "../lib/api";
import { progressIndex } from "../lib/stageStatus";

export function ProgressStrip({ stages, current, status }: { stages: StageDef[]; current: string | null; status: string }) {
  const done = progressIndex(stages, current, status);
  return (
    <div className="flex gap-0.5" aria-label={`${Math.min(done, stages.length)} of ${stages.length} stages done`}>
      {stages.map((s, i) => {
        let color = "bg-muted";
        if (i < done) color = "bg-ok";
        else if (i === done && status === "running") color = "bg-accent";
        else if (i === done && (status === "awaiting_approval" || status === "awaiting_input" || status === "changes_requested" || status === "awaiting_commit" || status === "awaiting_branches")) color = "bg-warn";
        else if (i === done && status === "failed") color = "bg-bad";
        return <div key={s.key} title={s.title} className={`h-1.5 flex-1 ${s.kind === "gate" ? "max-w-2" : ""} rounded-sm ${color}`} />;
      })}
    </div>
  );
}
