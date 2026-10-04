import { useEffect, useRef } from "react";
import type { AuditEvent } from "../lib/api";

const KIND: Record<AuditEvent["kind"], string> = {
  log: "text-subtle",
  status: "text-fg",
  artifact: "text-accent",
  approval: "text-warn",
  error: "text-bad",
  question: "text-warn font-medium",
  answer: "text-ok",
};

export function ActivityLog({ events, stage }: { events: AuditEvent[]; stage?: string | null }) {
  const end = useRef<HTMLDivElement>(null);
  const shown = stage ? events.filter((e) => e.stage === stage) : events;

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [shown.length]);

  if (shown.length === 0) return <p className="text-sm text-subtle">No activity yet.</p>;
  return (
    <div className="max-h-[28rem] overflow-y-auto card p-3 font-mono text-xs leading-6">
      {shown.map((e) => (
        <div key={e.id} className="flex gap-3">
          <span className="shrink-0 tabular-nums text-subtle">{new Date(e.created_at).toLocaleTimeString()}</span>
          <span className={KIND[e.kind]}>{e.message}</span>
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}
