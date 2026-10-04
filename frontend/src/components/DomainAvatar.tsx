/** A stable colour per domain, so the same domain looks the same on every page. Full class names, for Tailwind. */
const TONES = [
  "bg-indigo-500/15 text-indigo-600 dark:text-indigo-300",
  "bg-sky-500/15 text-sky-600 dark:text-sky-300",
  "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
  "bg-amber-500/15 text-amber-600 dark:text-amber-300",
  "bg-rose-500/15 text-rose-600 dark:text-rose-300",
  "bg-violet-500/15 text-violet-600 dark:text-violet-300",
  "bg-teal-500/15 text-teal-600 dark:text-teal-300",
  "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300",
];

export function domainTone(key: string): string {
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[hash % TONES.length];
}

export function DomainAvatar({ name, domainKey, size = "h-9 w-9 text-sm" }: { name: string; domainKey: string; size?: string }) {
  return (
    <span className={`flex shrink-0 items-center justify-center rounded-lg font-semibold ${size} ${domainTone(domainKey)}`} aria-hidden="true">
      {name.trim().charAt(0).toUpperCase() || "?"}
    </span>
  );
}
