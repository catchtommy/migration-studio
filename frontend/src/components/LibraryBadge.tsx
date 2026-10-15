import type { Library } from "../lib/api";

/** Where an agent or workflow comes from: the library shared by every project, this project's copy, or its own. */
export function LibraryBadge({ library, customised, compact }: { library: Library; customised: boolean; compact?: boolean }) {
  if (library === "shared")
    return compact ? null : <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-subtle" title="Used by every project">Shared</span>;
  return (
    <span
      className="rounded-full bg-sky-500/15 px-2 py-0.5 text-xs font-medium text-sky-700 dark:text-sky-300"
      title={customised ? "This project's own copy of a shared one" : "Exists only in this project"}
    >
      {compact ? (customised ? "copy" : "project") : customised ? "Customised for this project" : "This project only"}
    </span>
  );
}

/** Where saving goes when editing something shared: every project, or a copy for this project. */
export function SaveTarget({ value, onChange, canShared, noun }: { value: Library; onChange: (v: Library) => void; canShared: boolean; noun: string }) {
  if (!canShared) return <span className="text-xs text-subtle">Saving makes this project's own copy; other projects keep the shared {noun}.</span>;
  return (
    <fieldset className="inline-flex rounded-lg border border-border bg-muted p-0.5 text-xs">
      <legend className="sr-only">Save for</legend>
      {(
        [
          ["shared", "All projects"],
          ["project", "This project only"],
        ] as const
      ).map(([v, label]) => (
        <label key={v} className={`cursor-pointer rounded-md px-2.5 py-1 ${value === v ? "bg-surface font-medium text-fg shadow-sm" : "text-subtle hover:text-fg"}`}>
          <input type="radio" className="sr-only" name={`save-target-${noun}`} checked={value === v} onChange={() => onChange(v)} />
          {label}
        </label>
      ))}
    </fieldset>
  );
}
