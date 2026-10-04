/** Colour-coded chips for cross-domain integration: patterns, contract kinds and contract statuses. */
const PATTERN: Record<string, string> = {
  "sync-api": "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  event: "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300",
  saga: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "read-model": "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  deferred: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  "reference-copy": "bg-slate-500/15 text-slate-700 dark:text-slate-300",
};

const KIND: Record<string, string> = {
  api: PATTERN["sync-api"],
  event: PATTERN.event,
  command: PATTERN.saga,
};

const STATUS: Record<string, string> = {
  proposed: "bg-muted text-subtle",
  approved: "bg-accent/15 text-accent",
  "provided-by-monolith": "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "provided-by-service": "bg-ok/15 text-ok",
  deprecated: "bg-bad/15 text-bad line-through",
};

/** Background colour for a matrix cell whose interactions mostly use this pattern. */
export const PATTERN_CELL: Record<string, string> = {
  "sync-api": "bg-sky-500/25",
  event: "bg-fuchsia-500/25",
  saga: "bg-amber-500/30",
  "read-model": "bg-emerald-500/25",
  deferred: "bg-rose-500/25",
  "reference-copy": "bg-slate-500/25",
};

const chip = "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium";

export const PatternChip = ({ pattern }: { pattern: string }) => <span className={`${chip} ${PATTERN[pattern] ?? STATUS.proposed}`}>{pattern}</span>;
export const KindChip = ({ kind }: { kind: string }) => <span className={`${chip} ${KIND[kind] ?? STATUS.proposed}`}>{kind}</span>;
export const ContractStatusChip = ({ status }: { status: string }) => <span className={`${chip} ${STATUS[status] ?? STATUS.proposed}`}>{status.replace(/-/g, " ")}</span>;

export const PATTERN_NAMES = Object.keys(PATTERN);
