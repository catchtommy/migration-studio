import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Domain, type Feature, type MoveRequest, type MoveReview } from "../lib/api";
import { Field, inputClass } from "./Field";
import { ResetFeature } from "./ResetFeature";

const DOMAIN_KEY = /^[a-z][a-z0-9_-]{0,63}$/;
const DEBOUNCE_MS = 250;

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5" aria-label={title}>
      <h4 className="text-sm font-semibold">{title}</h4>
      {hint && <p className="text-xs text-subtle">{hint}</p>}
      {children}
    </section>
  );
}

function Tick({ label, checked, disabled, onChange, children }: { label: string; checked: boolean; disabled?: boolean; onChange: (on: boolean) => void; children?: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function TableRow({ row, checked, onChange }: { row: MoveReview["tables"][number]; checked: boolean; onChange: (on: boolean) => void }) {
  const users = [...row.moving_features.map((f) => `${f} (moving)`), ...row.remaining_features.map((f) => `${f} (stays)`), ...row.other_features];
  return (
    <Tick label={`Move table ${row.table}`} checked={checked} disabled={row.locked} onChange={onChange}>
      <code className="text-xs font-medium">{row.table}</code>
      <span className="ml-2 text-xs text-subtle">{row.owners.length ? `owned by ${row.owners.join(", ")}` : "no owner"}</span>
      {users.length > 0 && <span className="block text-xs text-subtle">Used by {users.join(", ")}</span>}
      {row.note && <span className={`block text-xs ${row.locked ? "text-subtle" : "text-warn"}`}>{row.note}</span>}
    </Tick>
  );
}

/** Moving features to another domain: where to, then a review of everything that changes, each part of which can be
 * unticked. Nothing changes until Confirm, and only as the review shows it (the server refuses an out-of-date one). */
export function MoveFeatures({ domain, domains, features, onCancel, onMoved }: {
  domain: Domain;
  domains: Domain[];
  features: Feature[];
  onCancel: () => void;
  onMoved: (result: { target: string; new_domain: boolean; moved: string[] }) => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [target, setTarget] = useState("");
  const [fresh, setFresh] = useState({ key: "", name: "", description: "" });
  const [unticked, setUnticked] = useState<string[]>([]);
  const [tables, setTables] = useState<string[] | undefined>(); // undefined: as suggested
  const [packages, setPackages] = useState<string[] | undefined>();
  const [skipped, setSkipped] = useState<string[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const others = domains.filter((d) => d.key !== domain.key);
  const destination = mode === "existing" ? (target ? { target } : null) : DOMAIN_KEY.test(fresh.key) && fresh.name.trim() ? { new_domain: { ...fresh, name: fresh.name.trim() } } : null;
  const request: MoveRequest | null = useMemo(
    () => (destination ? { features: features.map((f) => f.key), unticked, tables, packages, skipped_references: skipped, ...destination } : null),
    [JSON.stringify(destination), features, unticked, tables, packages, skipped], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Another destination is another review: what was adjusted for the last one doesn't carry over.
  const destinationKey = JSON.stringify(mode === "existing" ? target : fresh.key);
  useEffect(() => {
    setTables(undefined);
    setPackages(undefined);
    setSkipped([]);
    setAcknowledged(false);
  }, [destinationKey]);

  const [debounced, setDebounced] = useState(request);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(request), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [request]);

  const review = useQuery({
    queryKey: ["features", domain.key, "move", debounced], // a reset of a feature refreshes it; an older answer is ignored
    queryFn: () => api.previewMove(domain.key, debounced!),
    enabled: !!debounced,
    placeholderData: keepPreviousData, // a tick doesn't lose the place in a long review; confirm waits for the fresh one
  });
  const wanted = mode === "existing" ? target : fresh.key;
  const data = request && review.data?.target === wanted ? review.data : null;
  const pending = !request || JSON.stringify(request) !== JSON.stringify(debounced) || review.isFetching;

  const move = useMutation({
    mutationFn: () => {
      const r = data!;
      return api.moveFeatures(domain.key, {
        ...request!,
        // What the review shows, as it can't be applied otherwise: a blocked feature stays, a locked table doesn't move
        unticked: [...new Set([...unticked, ...r.features.filter((f) => f.blocked).map((f) => f.key)])],
        tables: r.tables.filter((t) => t.selected).map((t) => t.table),
        packages: r.packages.filter((p) => p.selected).map((p) => p.package),
        fingerprint: r.fingerprint,
        acknowledged_calls: acknowledged,
      });
    },
    onSuccess: (result) => {
      for (const key of [["features"], ["domains"], ["discovery"], ["domain", domain.key], ["domain", result.target]]) queryClient.invalidateQueries({ queryKey: key });
      onMoved(result);
    },
    onError: () => queryClient.invalidateQueries({ queryKey: ["features", domain.key, "move"] }),
  });

  const toggle = (list: string[], value: string, on: boolean) => (on ? [...list, value] : list.filter((v) => v !== value));
  // From the choices so far once there are any, as a quick second tick may come before the review has the first
  const selectedTables = tables ?? data?.tables.filter((t) => t.selected).map((t) => t.table) ?? [];
  const selectedPackages = packages ?? data?.packages.filter((p) => p.selected).map((p) => p.package) ?? [];
  // A tick shows at once; the review's answer follows. A locked table can't be ticked whatever was chosen before.
  const tableChecked = (t: MoveReview["tables"][number]) => !t.locked && (tables ? tables.includes(t.table) : t.selected);
  const used = data?.tables.filter((t) => t.group === "used") ?? [];
  const idle = data?.tables.filter((t) => t.group === "other") ?? [];
  const checks = data?.check_by_hand;
  const canConfirm = !!data && !pending && data.blockers.length === 0 && data.summary.features > 0 && acknowledged && !move.isPending;
  const targetName = data && (data.new_domain ? fresh.name : (domains.find((d) => d.key === data.target)?.name ?? data.target));

  return (
    <section className="card space-y-4 border-t-4 border-t-accent p-4" aria-label="Move features">
      <div>
        <h3 className="font-semibold">
          Move {features.length} feature{features.length === 1 ? "" : "s"} out of {domain.name}
        </h3>
        <p className="mt-0.5 text-sm text-subtle">Choose where they go, then review what changes. Nothing moves until you confirm.</p>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" name="move-destination" checked={mode === "existing"} onChange={() => setMode("existing")} />
            An existing domain
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name="move-destination" checked={mode === "new"} onChange={() => setMode("new")} />
            A new domain
          </label>
        </div>
        {mode === "existing" ? (
          <Field label="Domain">
            <select className={inputClass} value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Choose a domain…</option>
              {others.map((d) => (
                <option key={d.key} value={d.key}>
                  {d.name}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Key" hint="Lower case letters, digits, dashes and underscores. It can't be changed later.">
              <input className={inputClass} value={fresh.key} onChange={(e) => setFresh({ ...fresh, key: e.target.value })} />
            </Field>
            <Field label="Name">
              <input className={inputClass} value={fresh.name} onChange={(e) => setFresh({ ...fresh, name: e.target.value })} />
            </Field>
            <div className="sm:col-span-2">
              <Field label="Description">
                <input className={inputClass} value={fresh.description} onChange={(e) => setFresh({ ...fresh, description: e.target.value })} />
              </Field>
            </div>
          </div>
        )}
      </div>

      {review.error && <p className="text-sm text-bad">{(review.error as Error).message}</p>}

      {data && (
        <div className="space-y-4 border-t border-border pt-4">
          {data.blockers.length > 0 && (
            <ul className="space-y-1 rounded-md bg-bad/10 p-3 text-sm text-bad" aria-label="Why this can't move yet">
              {data.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}

          <Section title="Features">
            {data.features.map((f) => (
              <Tick key={f.key} label={`Move ${f.name}`} checked={!f.blocked && !unticked.includes(f.key)} disabled={!!f.blocked} onChange={(on) => setUnticked(toggle(unticked, f.key, !on))}>
                <button type="button" className="text-left font-medium hover:underline" aria-expanded={expanded === f.key} onClick={() => setExpanded(expanded === f.key ? null : f.key)}>
                  {f.name}
                </button>
                <code className="ml-2 text-xs text-subtle">{f.key}</code>
                {f.migration && <ResetFeature domain={domain.key} feature={f.key} name={f.name} status={f.migration.status} />}
                {f.blocked && <span className="block text-xs text-warn">{f.blocked}</span>}
                {expanded === f.key && (
                  <dl className="mt-1 space-y-0.5 text-xs text-subtle">
                    {f.description && <dd>{f.description}</dd>}
                    <dd>Entry points: {f.entry_points.join(", ") || "–"}</dd>
                    <dd>Tables: {f.tables.join(", ") || "–"}</dd>
                    <dd>Calls: {f.calls.join(", ") || "–"}</dd>
                    <dd>Used by: {f.used_by?.map((u) => `${u.entry_point} (${u.domain})`).join(", ") || "–"}</dd>
                  </dl>
                )}
              </Tick>
            ))}
          </Section>

          <Section title="Tables" hint={`Ticked tables become ${targetName}'s. An unticked table stays with its owner, and the moved features reach it through a contract.`}>
            {used.length === 0 && <p className="text-sm text-subtle">The moving features list no tables.</p>}
            {used.map((t) => (
              <TableRow key={t.table} row={t} checked={tableChecked(t)} onChange={(on) => setTables(toggle(selectedTables, t.table, on))} />
            ))}
            {idle.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs text-subtle">Other tables of {domain.name} ({idle.length}): no moving feature lists them</summary>
                <div className="mt-1.5 space-y-1.5">
                  {idle.map((t) => (
                    <TableRow key={t.table} row={t} checked={tableChecked(t)} onChange={(on) => setTables(toggle(selectedTables, t.table, on))} />
                  ))}
                </div>
              </details>
            )}
          </Section>

          {data.packages.length > 0 && (
            <Section title="Packages" hint={`Tick the legacy packages that should become ${targetName}'s. They can't be worked out from the features.`}>
              {data.packages.map((p) => (
                <Tick key={p.package} label={`Move package ${p.package}`} checked={packages ? packages.includes(p.package) : p.selected} onChange={(on) => setPackages(toggle(selectedPackages, p.package, on))}>
                  <code className="text-xs">{p.package}</code>
                </Tick>
              ))}
            </Section>
          )}

          {data.references.length > 0 && (
            <Section title="Reference updates" hint="Edits to calls and used_by that the move makes true.">
              {data.references.map((r) => (
                <Tick key={r.id} label={r.description} checked={!skipped.includes(r.id)} onChange={(on) => setSkipped(toggle(skipped, r.id, !on))}>
                  {r.description}
                </Tick>
              ))}
            </Section>
          )}

          {checks && (checks.calling_features.length + checks.dependencies.length + checks.contracts.length + checks.listing_paths.length > 0) && (
            <Section title="Check by hand" hint={`These may be about the moved features, but the data doesn't say. Nothing here changes.`}>
              <ul className="list-disc space-y-0.5 pl-5 text-xs">
                {checks.calling_features.map((c) => (
                  <li key={`${c.domain}/${c.feature}`}>
                    {c.feature} in {c.domain} calls {domain.key}
                  </li>
                ))}
                {checks.dependencies.map((d, i) => (
                  <li key={i}>
                    {d.source} depends on {domain.key}: {d.interaction} ({d.status})
                  </li>
                ))}
                {checks.contracts.map((c) => (
                  <li key={c.key} className={c.matches_moved_feature ? "font-medium text-warn" : ""}>
                    Contract {c.key} ({c.status}){c.consumers.length ? `, used by ${c.consumers.join(", ")}` : ""}
                    {c.matches_moved_feature && ": its name matches a moved feature"}
                  </li>
                ))}
                {checks.listing_paths.length > 0 && <li>{domain.name}'s listing paths may still point at moved code: {checks.listing_paths.join(", ")}</li>}
              </ul>
            </Section>
          )}

          {data.copied && (
            <Section title="New domain">
              <p className="text-sm">
                It gets {domain.name}'s {data.copied.context ? "context, " : ""}
                {data.copied.default_workflow ? "default workflow, " : ""}agent paths and notes. <strong>Repositories aren't copied</strong>: set them in its Agent inputs before migrating.
              </p>
            </Section>
          )}

          <ul className="space-y-1 rounded-md bg-warn/10 p-3 text-sm" aria-label="Warnings">
            {data.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>

          <div className="space-y-2 border-t border-border pt-3">
            <p className="text-sm font-medium" role="status">
              Moving {data.summary.features} feature{data.summary.features === 1 ? "" : "s"} and {data.summary.tables.length} table{data.summary.tables.length === 1 ? "" : "s"} from {domain.name} to {targetName}
              {data.summary.staying_tables.length > 0 && `; ${data.summary.staying_tables.length} table${data.summary.staying_tables.length === 1 ? "" : "s"} they use will stay elsewhere`}
            </p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />I have checked calls between the moved and remaining features
            </label>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        {data && (
          <button onClick={() => move.mutate()} disabled={!canConfirm} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            {move.isPending ? "Moving…" : "Confirm move"}
          </button>
        )}
        <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
        {move.error && <span className="text-sm text-bad">{(move.error as Error).message}</span>}
      </div>
    </section>
  );
}
