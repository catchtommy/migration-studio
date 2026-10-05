import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Domain, type Feature, type FeatureInput } from "../lib/api";
import { useDomains } from "../lib/hooks";
import { Field, inputClass } from "../components/Field";
import { StatusBadge } from "../components/StatusBadge";
import { DomainAvatar } from "../components/DomainAvatar";

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
const UNFINISHED = ["pending", "running", "awaiting_input", "awaiting_approval", "changes_requested", "failed"];

/** A link to start migrating a feature: the New migration form, with the domain and feature chosen. */
export const migrateUrl = (domain: string, feature: string) => `/?domain=${encodeURIComponent(domain)}&feature=${encodeURIComponent(feature)}`;

function FeatureForm({ domain, feature, onDone }: { domain: Domain; feature?: Feature; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [key, setKey] = useState(feature?.key ?? "");
  const [name, setName] = useState(feature?.name ?? "");
  const [description, setDescription] = useState(feature?.description ?? "");
  const [entryPoints, setEntryPoints] = useState((feature?.entry_points ?? []).join("\n"));
  const [tables, setTables] = useState((feature?.tables ?? []).join("\n"));
  const [calls, setCalls] = useState((feature?.calls ?? []).join("\n"));
  const save = useMutation({
    mutationFn: () => {
      const body: FeatureInput = { name: name.trim(), description, entry_points: lines(entryPoints), tables: lines(tables), calls: lines(calls) };
      return feature ? api.updateFeature(domain.key, feature.key, body) : api.addFeature(domain.key, { ...body, key: key.trim(), name: name.trim() });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["features", domain.key] });
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      onDone();
    },
  });
  return (
    <form
      className="card space-y-3 border-t-4 border-t-accent p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="font-semibold">{feature ? `Edit ${feature.name}` : "Add a feature"}</h3>
      <div className="grid gap-3 sm:grid-cols-[1fr_14rem]">
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Create loan" />
        </Field>
        <Field label="Key" hint={feature ? "The migration's operation name; can't be changed." : "Becomes the migration's operation name."}>
          <input className={`${inputClass} font-mono text-xs`} value={key} disabled={!!feature} onChange={(e) => setKey(e.target.value)} placeholder="create-loan" />
        </Field>
      </div>
      <Field label="Description">
        <textarea rows={2} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Entry points" hint="One per line: classes, methods, URLs, iBatis statements. The migration's agents start here.">
          <textarea rows={4} className={`${inputClass} font-mono text-xs`} value={entryPoints} onChange={(e) => setEntryPoints(e.target.value)} />
        </Field>
        <Field label="Tables" hint="One per line.">
          <textarea rows={4} className={`${inputClass} font-mono text-xs`} value={tables} onChange={(e) => setTables(e.target.value)} />
        </Field>
        <Field label="Calls domains" hint="Keys of other domains it reaches into, one per line.">
          <textarea rows={4} className={`${inputClass} font-mono text-xs`} value={calls} onChange={(e) => setCalls(e.target.value)} />
        </Field>
      </div>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={!name.trim() || !key.trim() || save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {feature ? "Save feature" : "Add feature"}
        </button>
        <button type="button" onClick={onDone} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
        {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
      </div>
    </form>
  );
}

function FeatureRow({ domain, feature, names, onEdit }: { domain: Domain; feature: Feature; names: Map<string, string>; onEdit: () => void }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.deleteFeature(domain.key, feature.key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["features", domain.key] });
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
  });
  const run = feature.migration;
  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-3">
        <span className="font-medium">{feature.name}</span>
        <code className="ml-2 text-xs text-subtle">{feature.key}</code>
        {feature.missing_from_latest && (
          <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-xs font-medium text-warn" title="Kept because it is being migrated, but the latest approved feature list doesn't have it.">
            Not in latest list
          </span>
        )}
        {feature.description && <p className="mt-0.5 max-w-md text-xs text-subtle">{feature.description}</p>}
      </td>
      <td className="px-4 py-3">
        <ul className="space-y-0.5 font-mono text-xs">
          {feature.entry_points.map((p) => (
            <li key={p} className="break-all">
              {p}
            </li>
          ))}
        </ul>
      </td>
      <td className="px-4 py-3 font-mono text-xs">{feature.tables.join(", ") || <span className="text-subtle">–</span>}</td>
      <td className="px-4 py-3">
        <span className="flex flex-wrap gap-1">
          {feature.calls.length === 0 && <span className="text-subtle">–</span>}
          {feature.calls.map((d) => (
            <Link key={d} to={`/domains/${d}`} className="inline-flex items-center gap-1 text-xs hover:text-accent" title={`Calls ${names.get(d) ?? d}`}>
              <DomainAvatar name={names.get(d) ?? d} domainKey={d} size="h-5 w-5 text-[10px]" />
              {names.get(d) ?? d}
            </Link>
          ))}
        </span>
      </td>
      <td className="px-4 py-3">
        {run ? (
          <Link to={`/runs/${run.id}`} className="inline-block hover:opacity-80">
            <StatusBadge status={run.status} />
          </Link>
        ) : (
          <Link to={migrateUrl(domain.key, feature.key)} className="inline-block rounded-md bg-accent px-3 py-1 text-xs font-medium text-white">
            Migrate
          </Link>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right">
        <button onClick={onEdit} className="rounded-md px-2 py-1 text-xs text-subtle hover:bg-muted hover:text-fg">
          Edit
        </button>
        {!run && (
          <button
            onClick={() => window.confirm(`Remove ${feature.name} from the feature list?`) && remove.mutate()}
            className="rounded-md px-2 py-1 text-xs text-subtle hover:bg-bad/10 hover:text-bad"
          >
            Remove
          </button>
        )}
      </td>
    </tr>
  );
}

/** The domain's features: what its migrations pick from. Listed by the feature-listing workflow, or by hand. */
export function DomainFeatures({ domain }: { domain: Domain }) {
  const queryClient = useQueryClient();
  const { data: domains = [] } = useDomains();
  const names = new Map(domains.map((d) => [d.key, d.name]));
  const { data: discovery } = useQuery({
    queryKey: ["discovery"],
    queryFn: api.discovery,
    refetchInterval: (q) => (q.state.data?.domains.some((d) => d.run && UNFINISHED.includes(d.run.status) && d.run.status !== "failed") ? 3000 : false),
  });
  const listing = discovery?.domains.find((d) => d.key === domain.key)?.run;
  const listingBusy = !!listing && UNFINISHED.includes(listing.status);
  const { data: features, isLoading } = useQuery({
    queryKey: ["features", domain.key, listing?.status],
    queryFn: () => api.features(domain.key),
  });
  const [editing, setEditing] = useState<string | null>(null); // feature key, or "" for a new one
  const start = useMutation({
    mutationFn: () => api.discoverFeatures(domain.key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["discovery"] }),
  });

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-64 flex-1 text-sm">
          <p className="font-medium">
            {features?.length ? `${features.length} feature${features.length === 1 ? "" : "s"}` : "No features listed yet"}
          </p>
          <p className="mt-0.5 text-subtle">
            Migrations start from this list. The <em>List domain features</em> workflow finds every feature in the domain's code; approve its list at the gate and
            the features appear here. You can also add or correct them by hand.
          </p>
        </div>
        {listing && listingBusy ? (
          <Link to={`/runs/${listing.id}`} className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted">
            Feature listing <StatusBadge status={listing.status} />
            <span className="text-accent">Open →</span>
          </Link>
        ) : (
          <button onClick={() => start.mutate()} disabled={start.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            {features?.length ? "Re-list features" : "List features"}
          </button>
        )}
        {editing === null && (
          <button onClick={() => setEditing("")} className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted">
            Add by hand
          </button>
        )}
        {start.error && <p className="w-full text-sm text-bad">{(start.error as Error).message}</p>}
      </div>

      {editing === "" && <FeatureForm domain={domain} onDone={() => setEditing(null)} />}
      {editing && features?.some((f) => f.key === editing) && (
        <FeatureForm key={editing} domain={domain} feature={features.find((f) => f.key === editing)} onDone={() => setEditing(null)} />
      )}

      {isLoading || !features ? (
        <p className="text-sm text-subtle">Loading…</p>
      ) : features.length === 0 ? (
        <p className="text-sm text-subtle">
          Features must be listed before any of {domain.name}'s operations can be migrated. Start with <strong>List features</strong>
          {listing ? "" : ", ideally after the bounded contexts are approved on the Domains page"}.
        </p>
      ) : (
        <section className="card overflow-x-auto">
          <table className="w-full min-w-[52rem] text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-subtle">
              <tr>
                <th className="px-4 py-2 font-medium">Feature</th>
                <th className="px-4 py-2 font-medium">Entry points</th>
                <th className="px-4 py-2 font-medium">Tables</th>
                <th className="px-4 py-2 font-medium">Calls</th>
                <th className="px-4 py-2 font-medium">Migration</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {features.map((f) => (
                <FeatureRow key={f.key} domain={domain} feature={f} names={names} onEdit={() => setEditing(f.key)} />
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
