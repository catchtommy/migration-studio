import type { RunDetail, StageDef } from "../lib/api";
import { stageStatuses, type DisplayStatus } from "../lib/stageStatus";

const DOT: Record<DisplayStatus, string> = {
  pending: "border-border bg-surface",
  running: "border-accent bg-accent animate-pulse",
  completed: "border-ok bg-ok",
  approved: "border-ok bg-ok",
  awaiting_approval: "border-warn bg-warn",
  failed: "border-bad bg-bad",
  rejected: "border-bad bg-bad",
};

export function StageTimeline({ stages, run, selected, onSelect }: {
  stages: StageDef[];
  run: RunDetail;
  selected: string | null;
  onSelect: (stage: string) => void;
}) {
  const statuses = stageStatuses(stages, run);
  return (
    <ol className="space-y-0.5">
      {stages.map((stage, i) => {
        const { status, attempts } = statuses.get(stage.key)!;
        const isGate = stage.kind === "gate";
        return (
          <li key={stage.key}>
            <button
              onClick={() => onSelect(stage.key)}
              className={`flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${
                selected === stage.key ? "bg-muted" : ""
              }`}
            >
              <span className={`h-3 w-3 shrink-0 border-2 ${isGate ? "rotate-45 rounded-[2px]" : "rounded-full"} ${DOT[status]}`} />
              <span className="w-5 shrink-0 text-xs tabular-nums text-subtle">{i + 1}</span>
              <span className={`flex-1 ${isGate ? "font-medium" : ""} ${status === "pending" ? "text-subtle" : ""}`}>
                {isGate ? "👤 " : ""}
                {stage.title}
              </span>
              {attempts.length > 1 && (
                <span className="text-xs text-subtle" title={`${attempts.length} attempts`}>
                  ×{attempts.length}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
