import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ArtifactSummary } from "../lib/api";

export type DocumentChoice = number | "all";

const KIND_LABELS: Record<string, string> = { markdown: "MD", yaml: "YAML", openapi: "OpenAPI", java: "Java", text: "Text" };

/** A path without the run's documents folder (.migration/<domain>/<operation>/), which every document shares. */
export function shortPath(path: string): string {
  return path.startsWith(".migration/") ? path.split("/").slice(3).join("/") : path;
}

/** The documents to show for a choice; an unknown choice (e.g. after switching step) shows them all. */
export function chosen<T extends { id: number }>(docs: T[], choice: DocumentChoice): T[] {
  const one = choice === "all" ? undefined : docs.find((d) => d.id === choice);
  return one ? [one] : docs;
}

/** A searchable dropdown to show one of a step's documents, or all of them. */
export function DocumentPicker({ docs, value, onChange }: { docs: ArtifactSummary[]; value: DocumentChoice; onChange: (choice: DocumentChoice) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  const options = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matching = docs.filter((d) => !q || d.name.toLowerCase().includes(q) || d.path.toLowerCase().includes(q));
    return [...(q ? [] : [{ id: "all" as const, name: `All documents (${docs.length})`, path: "", kind: "" }]), ...matching];
  }, [docs, query]);
  const current = value === "all" ? undefined : docs.find((d) => d.id === value);

  useEffect(() => {
    if (!open) return;
    setActive(0);
    input.current?.focus();
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  useEffect(() => setActive(0), [query]);

  const pick = (choice: DocumentChoice) => {
    onChange(choice);
    setOpen(false);
    setQuery("");
  };

  return (
    <div ref={ref} className="relative w-full max-w-md">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Choose a document"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-left text-sm hover:border-accent/50"
      >
        <span className="text-xs text-subtle">Document</span>
        <span className="min-w-0 flex-1 truncate font-medium">{current ? shortPath(current.path) : `All documents (${docs.length})`}</span>
        <span aria-hidden="true" className="text-subtle">
          ▾
        </span>
      </button>
      {open && (
        <div className="card absolute z-20 mt-1 w-full p-2">
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={options[active] ? `${listId}-${active}` : undefined}
            aria-label="Search documents"
            placeholder="Search by name or path…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((i) => Math.min(i + 1, options.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter" && options[active]) {
                e.preventDefault();
                pick(options[active].id);
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
            className="mb-1 w-full rounded-md border border-border bg-bg px-2 py-1.5 text-sm"
          />
          <ul id={listId} role="listbox" aria-label="Documents" className="max-h-72 overflow-y-auto">
            {options.length === 0 && <li className="px-2 py-1.5 text-sm text-subtle">No document matches “{query}”</li>}
            {options.map((o, i) => {
              const selected = o.id === value || (o.id === "all" && !current);
              return (
                <li
                  key={o.id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(o.id)}
                  className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm ${i === active ? "bg-muted" : ""} ${selected ? "font-medium text-accent" : ""}`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{o.name}</span>
                    {o.path && shortPath(o.path) !== o.name && <span className="block truncate text-xs text-subtle">{shortPath(o.path)}</span>}
                  </span>
                  {o.kind && <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-subtle">{KIND_LABELS[o.kind] ?? o.kind}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
