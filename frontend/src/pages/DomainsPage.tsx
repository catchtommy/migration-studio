import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useDomains } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { GridIcon } from "../components/icons";
import { DomainAvatar } from "../components/DomainAvatar";
import { PATTERN_CELL, PATTERN_NAMES, PatternChip } from "../components/Chips";

function NewDomainForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => api.createDomain({ key, name: name.trim(), description }),
    onSuccess: (d) => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      navigate(`/domains/${d.key}?tab=inputs`);
    },
  });

  return (
    <form
      className="space-y-3 card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h2 className="font-semibold">New domain</h2>
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <Field label="Name">
          <input
            className={inputClass}
            value={name}
            placeholder="e.g. Collateral"
            onChange={(e) => {
              setName(e.target.value);
              if (!keyEdited) setKey(slug(e.target.value, "-"));
            }}
          />
        </Field>
        <Field label="Key" hint="Used in branch names">
          <input
            className={`${inputClass} font-mono text-xs`}
            value={key}
            onChange={(e) => {
              setKeyEdited(true);
              setKey(e.target.value);
            }}
          />
        </Field>
      </div>
      <Field label="Description">
        <textarea rows={2} className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={!name.trim() || !key || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Add domain
        </button>
        {create.error && <span className="text-sm text-bad">{(create.error as Error).message}</span>}
      </div>
    </form>
  );
}

/** Which domain depends on which (from approved integration designs), and the suggested migration order. */
function MigrationOverview({ names }: { names: Map<string, string> }) {
  const { data: map } = useQuery({ queryKey: ["dependencyMap"], queryFn: api.dependencyMap });
  if (!map) return null;
  const involved = map.domains.filter((d) => map.edges.some((e) => e.source === d || e.target === d));
  const edge = (source: string, target: string) => map.edges.find((e) => e.source === source && e.target === target);
  const name = (key: string) => names.get(key) ?? key;
  const dominant = (patterns: Record<string, number>) => Object.entries(patterns).sort((a, b) => b[1] - a[1])[0][0];

  return (
    <section className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="card overflow-x-auto p-4">
        <h2 className="font-semibold">Cross-domain dependencies</h2>
        <p className="mt-0.5 text-xs text-subtle">Rows depend on columns (call their API, use their events or data). From approved integration designs.</p>
        {involved.length === 0 ? (
          <p className="mt-4 text-sm text-subtle">None recorded yet. They appear once a migration's cross-domain integration design is approved.</p>
        ) : (
          <>
            <table className="mt-3 border-separate border-spacing-1 text-xs">
              <thead>
                <tr>
                  <th />
                  {involved.map((d) => (
                    <th key={d} className="px-1 pb-1 align-bottom font-medium text-subtle">
                      <span className="inline-flex flex-col items-center gap-1">
                        <DomainAvatar name={name(d)} domainKey={d} size="h-6 w-6 text-[11px]" />
                        {name(d)}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {involved.map((row) => (
                  <tr key={row}>
                    <th className="whitespace-nowrap pr-2 text-right font-medium">{name(row)}</th>
                    {involved.map((col) => {
                      const e = edge(row, col);
                      return (
                        <td
                          key={col}
                          title={e ? `${name(row)} → ${name(col)}: ${Object.entries(e.patterns).map(([p, n]) => `${n} ${p}`).join(", ")}` : undefined}
                          className={`h-9 min-w-9 rounded-md text-center font-semibold tabular-nums ${row === col ? "bg-muted/60" : e ? PATTERN_CELL[dominant(e.patterns)] : "bg-muted/30"}`}
                        >
                          {e?.count ?? ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {PATTERN_NAMES.map((p) => (
                <PatternChip key={p} pattern={p} />
              ))}
            </div>
          </>
        )}
      </div>
      <div className="card p-4">
        <h2 className="font-semibold">Suggested migration order</h2>
        <p className="mt-0.5 text-xs text-subtle">Domains others depend on synchronously the least go first.</p>
        <ol className="mt-3 space-y-2.5">
          {map.order.map((step, i) => (
            <li key={step.domain} className="flex gap-2.5 text-sm">
              <span className="mt-0.5 w-4 shrink-0 text-right text-xs tabular-nums text-subtle">{i + 1}</span>
              <DomainAvatar name={name(step.domain)} domainKey={step.domain} size="h-6 w-6 text-[11px]" />
              <span>
                <Link to={`/domains/${step.domain}?tab=dependencies`} className="font-medium hover:text-accent">
                  {name(step.domain)}
                </Link>
                <span className="block text-xs text-subtle">{step.reason}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

export function DomainsPage() {
  const { data: domains, isLoading } = useDomains();
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-6">
      <PageTitle icon={<GridIcon />} title="Domains" subtitle="Tell each domain's agents where to look, and read the documentation they produced." tone="from-sky-500 to-indigo-500">
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New domain
          </button>
        )}
      </PageTitle>
      {adding && <NewDomainForm />}
      {domains && <MigrationOverview names={new Map(domains.map((d) => [d.key, d.name]))} />}
      {isLoading || !domains ? (
        <p className="text-subtle">Loading…</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {domains.map((d) => {
            const configured = Object.values(d.agent_inputs).filter((i) => i.paths.length || i.notes).length;
            return (
              <Link key={d.key} to={`/domains/${d.key}`} className="card block space-y-3 p-4 transition hover:-translate-y-0.5 hover:border-accent/50 hover:shadow-md">
                <div className="flex items-center gap-3">
                  <DomainAvatar name={d.name} domainKey={d.key} />
                  <h2 className="flex-1 font-semibold">{d.name}</h2>
                  <code className="text-xs text-subtle">{d.key}</code>
                </div>
                <p className="line-clamp-2 min-h-10 text-sm text-subtle">{d.description || "No description."}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-subtle">
                  <span>
                    {d.completed}/{d.runs} operations migrated
                  </span>
                  <span className={configured || d.context ? "" : "text-warn"}>
                    {configured || d.context ? `Inputs for ${configured} agent${configured === 1 ? "" : "s"}${d.context ? " + shared context" : ""}` : "No agent inputs yet"}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
