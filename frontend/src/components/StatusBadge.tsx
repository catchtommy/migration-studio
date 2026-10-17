const STYLES: Record<string, string> = {
  pending: "bg-muted text-subtle",
  running: "bg-accent/15 text-accent",
  awaiting_input: "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300",
  awaiting_approval: "bg-warn/15 text-warn",
  changes_requested: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  awaiting_branches: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  awaiting_publish: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  awaiting_commit: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  completed: "bg-ok/15 text-ok",
  approved: "bg-ok/15 text-ok",
  failed: "bg-bad/15 text-bad",
  rejected: "bg-bad/15 text-bad",
};

const LABELS: Record<string, string> = {
  awaiting_approval: "Awaiting approval",
  changes_requested: "Changes requested",
  awaiting_input: "Needs input",
  awaiting_commit: "Ready to commit",
  awaiting_branches: "Confirm branches",
  awaiting_publish: "Ready to publish docs",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status] ?? STYLES.pending}`}>
      {status === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {LABELS[status] ?? status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}
