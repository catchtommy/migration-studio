import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Project } from "../lib/api";

/** The current project in the header, and the projects the user can switch to. */
export function ProjectSwitcher({ projects, current, onSwitch }: { projects: Project[]; current: Project; onSwitch: (key: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const q = query.trim().toLowerCase();
  const shown = projects.filter((p) => !q || p.name.toLowerCase().includes(q) || p.key.includes(q));

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Switch project"
        className="flex items-center gap-2 rounded-lg bg-white/15 px-3 py-1.5 text-sm font-medium text-white ring-1 ring-white/25 hover:bg-white/25"
      >
        <span className="max-w-48 truncate">{current.name}</span>
        <span aria-hidden="true" className="text-white/70">
          ▾
        </span>
      </button>
      {open && (
        <div className="card absolute left-0 z-30 mt-2 w-72 p-2 text-sm">
          {projects.length > 6 && (
            <input
              autoFocus
              aria-label="Search projects"
              placeholder="Search projects…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="mb-1 w-full rounded-md border border-border bg-bg px-2 py-1.5"
            />
          )}
          <ul role="listbox" aria-label="Projects" className="max-h-72 overflow-y-auto">
            {shown.map((p) => (
              <li key={p.key} role="option" aria-selected={p.key === current.key}>
                <button
                  onClick={() => {
                    setOpen(false);
                    if (p.key !== current.key) onSwitch(p.key);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-muted ${p.key === current.key ? "font-medium text-accent" : ""}`}
                >
                  <span className="truncate">{p.name}</span>
                  <span className="shrink-0 text-xs text-subtle">{p.my_role}</span>
                </button>
              </li>
            ))}
          </ul>
          <Link to="/projects" onClick={() => setOpen(false)} className="mt-1 block border-t border-border px-2 pt-2 text-xs text-accent hover:underline">
            All projects →
          </Link>
        </div>
      )}
    </div>
  );
}
