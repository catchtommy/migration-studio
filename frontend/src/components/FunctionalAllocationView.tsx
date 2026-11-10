import { useMemo, useState } from "react";
import { dump, load } from "js-yaml";

/** functional-allocation.yaml: where each functionality of the feature goes once migrated (the domain microservice, a
 * direct call from it to another domain, the orchestrator, or an event and its handler), written by the dependency step. The reviewer reads it here grouped
 * by placement and, while the gate waits, changes placements in place; the design then follows the file as saved.
 * Each change is recorded on the functionality (`review`: amended, the agent's original placement, a comment). */

export const ALLOCATION_FILE = "functional-allocation.yaml";

export const PLACEMENTS = ["domain", "domain-call", "orchestrator", "event-handler", "other-domain", "ui-unchanged"] as const;
type Placement = (typeof PLACEMENTS)[number];
const TRIGGERS = ["domain", "orchestrator"] as const;
const VIAS = ["orchestrator", "domain-call"] as const;
/** Placements whose functionality calls other domains (`calls`), and those another domain does (`owner`). */
const CALLING: Placement[] = ["domain-call", "orchestrator"];
const OTHERS: Placement[] = ["event-handler", "other-domain"];

const LABELS: Record<Placement, string> = {
  domain: "Domain microservice",
  "domain-call": "Domain calls another domain directly",
  orchestrator: "Orchestrator",
  "event-handler": "Event handlers",
  "other-domain": "Another domain",
  "ui-unchanged": "UI unchanged",
};
const HINTS: Record<Placement, string> = {
  domain: "The feature's own domain service does it, on the tables it owns, with no other domain involved.",
  "domain-call": "The domain service calls another domain's API itself, without an orchestrator (only where the domain's Direct calls setting allows).",
  orchestrator: "Calls other domains and the domain service, and builds what the caller gets. No database.",
  "event-handler": "A side effect nobody waits for: an event is published and a handler does the work.",
  "other-domain": "Another domain decides it; this feature asks for the answer, through the orchestrator or a direct call.",
  "ui-unchanged": "Enforced only in the JSP or JavaScript; it stays as it is.",
};

export interface AllocationEvent {
  name: string;
  triggered_from: "domain" | "orchestrator";
  publisher: string;
  when?: string;
  handler: string;
  delegates_to?: string[];
}
export interface Functionality {
  id: string;
  name: string;
  legacy?: string;
  code_refs?: string[];
  requirements?: string[];
  placement: Placement;
  owner?: string;
  calls?: string[];
  via?: "orchestrator" | "domain-call";
  interactions?: string[];
  event?: AllocationEvent;
  depends_on?: string[];
  reason: string;
  review?: { amended?: boolean; original?: Placed; comment?: string };
  [key: string]: unknown;
}
/** Where a functionality goes in the monolith while the other domains it involves are not migrated (the backend's
 * app/legacy_modules.py): calls to another domain through the integration module's entry points; a reaction of a domain
 * still in the monolith (an event handler naming what does the work today) through the event-handler module. */
export function legacyModule(f: Functionality): "integration" | "event-handler" | null {
  if (f.placement === "event-handler") return f.event?.delegates_to?.length ? "event-handler" : null;
  if (f.placement === "other-domain") return "integration";
  if (CALLING.includes(f.placement) && (f.calls ?? []).length) return "integration";
  return null;
}
const LEGACY_LABELS = {
  integration: "Integration module entry point",
  "event-handler": "Event-handler module (calls integration entry points)",
} as const;

export interface Allocation {
  feature: string;
  domain: string;
  functionalities: Functionality[];
  [key: string]: unknown;
}
/** What a reviewer can change about one functionality. */
type Placed = Pick<Functionality, "placement" | "owner" | "calls" | "via" | "event">;

/** The file as an allocation, or null when it isn't one (it is then shown as plain YAML). */
export function parseAllocation(text: string): Allocation | null {
  try {
    const data = load(text) as Allocation;
    if (!data || typeof data !== "object" || !Array.isArray(data.functionalities)) return null;
    if (!data.functionalities.every((f) => f && typeof f === "object" && "placement" in f)) return null;
    return data;
  } catch {
    return null;
  }
}

const DETAIL: Record<Placement, string> = {
  domain: "Other domains",
  "domain-call": "Calls directly",
  orchestrator: "Calls",
  "event-handler": "Event",
  "other-domain": "Decided by",
  "ui-unchanged": "Other domains",
};

const placed = (f: Functionality): Placed => ({ placement: f.placement, owner: f.owner, calls: f.calls, via: f.via, event: f.event });
const same = (a: Placed, b: Placed) => JSON.stringify(placed(a as Functionality)) === JSON.stringify(placed(b as Functionality));

/** The allocation with each functionality's `review` brought up to date: amended (with the agent's original placement,
 * kept from an earlier amendment) when it differs from that original or carries a comment, else no review at all. */
export function withReviews(edited: Allocation, loaded: Allocation): Allocation {
  const before = new Map(loaded.functionalities.map((f) => [f.id, f]));
  return {
    ...edited,
    functionalities: edited.functionalities.map((f) => {
      const old = before.get(f.id);
      const original: Placed | undefined = old?.review?.original ?? (old ? placed(old) : undefined);
      const comment = f.review?.comment?.trim();
      const { review: _drop, ...rest } = f;
      const changed = original !== undefined && !same(placed(f), original);
      if (!changed && !comment) return rest as Functionality;
      return { ...rest, review: { amended: changed || undefined, original: changed ? original : undefined, comment: comment || undefined } };
    }),
  };
}

/** Problems that stop the edited allocation from being saved: an event handler needs its event, another domain a name. */
export function allocationProblems(a: Allocation): string[] {
  const out: string[] = [];
  for (const f of a.functionalities) {
    if (f.placement === "event-handler") {
      const e = f.event;
      const missing = (["name", "publisher", "handler"] as const).filter((k) => !e?.[k]?.trim());
      if (missing.length) out.push(`${f.id}: the event needs ${missing.join(", ")}`);
      if (!f.owner?.trim()) out.push(`${f.id}: name the domain whose handler does it`);
    }
    if (f.placement === "other-domain" && !f.owner?.trim()) out.push(`${f.id}: name the domain that decides it`);
    if (f.placement === "domain-call" && !(f.calls ?? []).length) out.push(`${f.id}: name the domain(s) it calls directly`);
    if (f.placement !== "domain" && f.placement !== "ui-unchanged" && !(f.interactions ?? []).length)
      out.push(`${f.id}: name the interactions it covers (from cross-domain.yaml)`);
  }
  const published = a.functionalities.filter((f) => f.placement === "event-handler" && f.event?.triggered_from === "orchestrator");
  if (published.length && !a.functionalities.some((f) => f.placement === "orchestrator"))
    out.push(`${published.map((f) => f.id).join(", ")} publish from the orchestrator, but nothing is placed on it`);
  return out;
}

/** Drop the keys left undefined, so the saved YAML has no `null`s where nothing was given. */
function clean<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function FunctionalAllocationView({
  content,
  editable,
  saving,
  error,
  onSave,
}: {
  content: string;
  editable: boolean;
  saving?: boolean;
  error?: string | null;
  onSave: (text: string) => void;
}) {
  const loaded = useMemo(() => parseAllocation(content), [content]);
  const [draft, setDraft] = useState<Allocation | null>(null);
  if (!loaded) return null;
  const shown = draft ?? loaded;
  const problems = draft ? allocationProblems(draft) : [];
  const amended = loaded.functionalities.filter((f) => f.review?.amended).length;

  const update = (id: string, change: (f: Functionality) => Functionality) =>
    setDraft((d) => d && { ...d, functionalities: d.functionalities.map((f) => (f.id === id ? change(f) : f)) });

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Functional allocation: {loaded.feature}</h3>
          <p className="text-xs text-subtle">
            Where each functionality goes once migrated. The design follows this as approved
            {amended ? `; ${amended} placement${amended === 1 ? "" : "s"} changed by a reviewer` : ""}.
          </p>
        </div>
        {editable && !draft && (
          <button className="text-xs font-medium text-accent hover:underline" onClick={() => setDraft(structuredClone(loaded))}>
            Change placements
          </button>
        )}
      </div>

      {draft ? (
        <EditTable allocation={draft} loaded={loaded} update={update} />
      ) : (
        PLACEMENTS.map((p) => <Group key={p} placement={p} items={shown.functionalities.filter((f) => f.placement === p)} />)
      )}

      {draft && (
        <div className="space-y-2">
          {problems.length > 0 && (
            <ul role="alert" className="list-disc rounded-md bg-warn/10 px-6 py-2 text-xs text-warn">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {error && <p className="text-xs text-bad">{error}</p>}
          <div className="flex justify-end gap-2">
            <button className="rounded-md px-3 py-1.5 text-sm hover:bg-muted" onClick={() => setDraft(null)}>
              Cancel
            </button>
            <button
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={saving || problems.length > 0}
              onClick={() => onSave(dump(clean(withReviews(draft, loaded)), { lineWidth: 120, noRefs: true }))}
            >
              Save placements
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Group({ placement, items }: { placement: Placement; items: Functionality[] }) {
  if (!items.length) return null;
  return (
    <section aria-label={LABELS[placement]}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-subtle">
        {LABELS[placement]} <span className="font-normal normal-case">· {items.length}</span>
      </h4>
      <p className="mb-1 text-xs text-subtle">{HINTS[placement]}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-subtle">
            <tr>
              <th className="py-1 pr-3 font-medium">Id</th>
              <th className="py-1 pr-3 font-medium">Functionality</th>
              <th className="py-1 pr-3 font-medium">{DETAIL[placement]}</th>
              <th className="py-1 pr-3 font-medium">Requirements</th>
              <th className="py-1 font-medium">Why</th>
            </tr>
          </thead>
          <tbody>
            {items.map((f) => (
              <tr key={f.id} className="border-t border-border align-top">
                <td className="py-1.5 pr-3 font-mono text-xs">{f.id}</td>
                <td className="py-1.5 pr-3">
                  <div>{f.name}</div>
                  {f.legacy && <div className="text-xs text-subtle">Today: {f.legacy}</div>}
                  {f.review?.amended && (
                    <span className="mt-1 inline-block rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">
                      Changed by reviewer{f.review.original ? ` (was ${f.review.original.placement})` : ""}
                    </span>
                  )}
                  {f.review?.comment && <div className="text-xs italic text-subtle">“{f.review.comment}”</div>}
                </td>
                <td className="py-1.5 pr-3 text-xs">
                  {f.event ? (
                    <div className="space-y-0.5">
                      <code className="rounded bg-muted px-1">{f.event.name}</code>
                      <div>
                        published by {f.event.publisher} ({f.event.triggered_from === "orchestrator" ? "from the orchestrator" : "from the domain"})
                        {f.event.when ? `, ${f.event.when.charAt(0).toLowerCase()}${f.event.when.slice(1)}` : ""}
                      </div>
                      <div>
                        handled by <strong>{f.owner}</strong>: {f.event.handler}
                      </div>
                      {f.event.delegates_to?.length ? <div className="text-subtle">until {f.owner} handles it: {f.event.delegates_to.join(", ")}</div> : null}
                    </div>
                  ) : f.placement === "other-domain" ? (
                    <span>
                      decided by <strong>{f.owner}</strong>, asked {f.via === "domain-call" ? "by the domain directly" : "by the orchestrator"}
                    </span>
                  ) : CALLING.includes(f.placement) ? (
                    (f.calls ?? []).join(", ") || "—"
                  ) : (
                    "—"
                  )}
                  {legacyModule(f) && (
                    <div className="mt-1">
                      <span
                        className="inline-block rounded-full bg-muted px-2 py-0.5 text-subtle"
                        title="Until the other domains are migrated, the feature reaches them through this temporary module of the monolith"
                      >
                        Until migrated: {LEGACY_LABELS[legacyModule(f)!]}
                      </span>
                    </div>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-xs">{(f.requirements ?? []).join(", ") || "—"}</td>
                <td className="py-1.5 text-xs">{f.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const input = "w-full rounded border border-border bg-bg px-2 py-1 text-xs";
const list = (text: string) => text.split(",").map((v) => v.trim()).filter(Boolean);

/** A comma-separated list: the text stays as typed (a trailing comma included) while the parsed list is passed on. */
function ListInput({ label, values, onChange }: { label: string; values: string[]; onChange: (values: string[]) => void }) {
  const [text, setText] = useState(values.join(", "));
  return (
    <input
      aria-label={label}
      className={input}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(list(e.target.value));
      }}
    />
  );
}

function EditTable({
  allocation,
  loaded,
  update,
}: {
  allocation: Allocation;
  loaded: Allocation;
  update: (id: string, change: (f: Functionality) => Functionality) => void;
}) {
  const before = new Map(loaded.functionalities.map((f) => [f.id, f]));
  return (
    <div className="space-y-3">
      {allocation.functionalities.map((f) => {
        const old = before.get(f.id);
        const changed = old !== undefined && !same(placed(f), placed(old));
        const setEvent = (key: keyof AllocationEvent, value: string) =>
          update(f.id, (x) => ({ ...x, event: { ...(x.event as AllocationEvent), [key]: value } }));
        return (
          <fieldset key={f.id} className={`rounded-md border p-3 ${changed ? "border-accent" : "border-border"}`}>
            <legend className="px-1 text-xs">
              <span className="font-mono">{f.id}</span> {f.name}
            </legend>
            {f.legacy && <p className="mb-2 text-xs text-subtle">Today: {f.legacy}</p>}
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs">
                Placement
                <select
                  aria-label={`Placement of ${f.id}`}
                  className={input}
                  value={f.placement}
                  onChange={(e) => {
                    const placement = e.target.value as Placement;
                    update(f.id, (x) => {
                      const next: Functionality = { ...x, placement };
                      // Keep only the fields the new placement has (the file is refused otherwise)
                      if (placement === "event-handler")
                        next.event = x.event ?? old?.event ?? { name: "", triggered_from: "domain", publisher: allocation.domain, when: "", handler: "" };
                      else delete next.event;
                      if (!OTHERS.includes(placement)) delete next.owner;
                      if (!CALLING.includes(placement)) delete next.calls;
                      // The domain's own work involves no other domain: it covers no interaction (the file is refused otherwise)
                      if (placement === "domain" || placement === "ui-unchanged") delete next.interactions;
                      else if (!next.interactions?.length && old?.interactions?.length) next.interactions = old.interactions;
                      if (placement === "other-domain") next.via = x.via ?? "orchestrator";
                      else delete next.via;
                      return next;
                    });
                  }}
                >
                  {PLACEMENTS.map((p) => (
                    <option key={p} value={p}>
                      {LABELS[p]}
                    </option>
                  ))}
                </select>
              </label>
              {OTHERS.includes(f.placement) && (
                <label className="text-xs">
                  {f.placement === "event-handler" ? "Handler's domain" : "Domain that decides it"}
                  <input aria-label={`Owner of ${f.id}`} className={input} value={f.owner ?? ""} onChange={(e) => update(f.id, (x) => ({ ...x, owner: e.target.value }))} />
                </label>
              )}
              {CALLING.includes(f.placement) && (
                <label className="text-xs">
                  {f.placement === "domain-call" ? "Domains it calls directly (comma-separated)" : "Domains the orchestrator calls (comma-separated)"}
                  <ListInput label={`Calls of ${f.id}`} values={f.calls ?? []} onChange={(calls) => update(f.id, (x) => ({ ...x, calls }))} />
                </label>
              )}
              {f.placement !== "domain" && f.placement !== "ui-unchanged" && (
                <label className="text-xs">
                  Interactions it covers (ids from cross-domain.yaml, comma-separated)
                  <ListInput label={`Interactions of ${f.id}`} values={f.interactions ?? []} onChange={(interactions) => update(f.id, (x) => ({ ...x, interactions }))} />
                </label>
              )}
              {f.placement === "other-domain" && (
                <label className="text-xs">
                  Asked through
                  <select aria-label={`Via for ${f.id}`} className={input} value={f.via ?? "orchestrator"} onChange={(e) => update(f.id, (x) => ({ ...x, via: e.target.value as Functionality["via"] }))}>
                    {VIAS.map((v) => (
                      <option key={v} value={v}>
                        {v === "orchestrator" ? "The orchestrator" : "A direct call from the domain"}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {f.placement === "event-handler" && f.event && (
                <>
                  <label className="text-xs">
                    Event name
                    <input aria-label={`Event of ${f.id}`} className={input} value={f.event.name} onChange={(e) => setEvent("name", e.target.value)} />
                  </label>
                  <label className="text-xs">
                    Published from
                    <select aria-label={`Published from for ${f.id}`} className={input} value={f.event.triggered_from} onChange={(e) => setEvent("triggered_from", e.target.value)}>
                      {TRIGGERS.map((t) => (
                        <option key={t} value={t}>
                          {t === "domain" ? "The domain (its own change triggers it)" : "The orchestrator (a cross-domain operation completes)"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs">
                    Publishing domain
                    <input aria-label={`Publisher of ${f.id}`} className={input} value={f.event.publisher} onChange={(e) => setEvent("publisher", e.target.value)} />
                  </label>
                  <label className="text-xs">
                    When
                    <input aria-label={`When for ${f.id}`} className={input} value={f.event.when ?? ""} onChange={(e) => setEvent("when", e.target.value)} />
                  </label>
                  <label className="text-xs sm:col-span-2">
                    What the handler does
                    <input aria-label={`Handler of ${f.id}`} className={input} value={f.event.handler} onChange={(e) => setEvent("handler", e.target.value)} />
                  </label>
                  <label className="text-xs sm:col-span-2">
                    Existing method(s) the temporary handler calls until that domain handles it (Class#method, comma-separated)
                    <ListInput
                      label={`Delegates of ${f.id}`}
                      values={f.event.delegates_to ?? []}
                      onChange={(delegates_to) => update(f.id, (x) => ({ ...x, event: { ...(x.event as AllocationEvent), delegates_to } }))}
                    />
                  </label>
                </>
              )}
              <label className="text-xs sm:col-span-2">
                Reviewer's comment
                <input
                  aria-label={`Comment on ${f.id}`}
                  className={input}
                  placeholder={changed ? "Why the placement changed (the design agent reads this)" : "Optional"}
                  value={f.review?.comment ?? ""}
                  onChange={(e) => update(f.id, (x) => ({ ...x, review: { ...x.review, comment: e.target.value } }))}
                />
              </label>
            </div>
            <p className="mt-1 text-xs text-subtle">Agent's reason: {f.reason}</p>
          </fieldset>
        );
      })}
    </div>
  );
}
