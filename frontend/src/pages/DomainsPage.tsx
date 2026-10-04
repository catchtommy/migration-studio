import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useDomains } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { GridIcon } from "../components/icons";
import { DomainAvatar } from "../components/DomainAvatar";

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
