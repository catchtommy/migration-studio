import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CONTRACT_STATUSES, type Contract, type ContractStatus } from "../lib/api";
import { useDomains } from "../lib/hooks";
import { ContractStatusChip, KindChip } from "../components/Chips";
import { DomainAvatar } from "../components/DomainAvatar";
import { PageTitle } from "../components/PageTitle";
import { ShareIcon } from "../components/icons";

const select = "rounded-md border border-border bg-surface px-2 py-1.5 text-sm";

function DomainRef({ domain, names }: { domain: string; names: Map<string, string> }) {
  return (
    <Link to={`/domains/${domain}?tab=dependencies`} className="inline-flex items-center gap-1.5 hover:text-accent">
      <DomainAvatar name={names.get(domain) ?? domain} domainKey={domain} size="h-5 w-5 text-[10px]" />
      {names.get(domain) ?? domain}
    </Link>
  );
}

function ContractRow({ contract, names }: { contract: Contract; names: Map<string, string> }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const update = useMutation({
    mutationFn: (status: ContractStatus) => api.updateContract(contract.key, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["contracts"] }),
  });
  return (
    <>
      <tr className="border-t border-border align-top">
        <td className="px-4 py-3">
          <button className="text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
            <span className="font-medium hover:text-accent">{contract.name}</span>
            <code className="block text-xs text-subtle">
              {contract.key} · v{contract.version}
            </code>
          </button>
        </td>
        <td className="px-4 py-3">
          <KindChip kind={contract.kind} />
        </td>
        <td className="px-4 py-3 text-sm">
          <DomainRef domain={contract.provider} names={names} />
        </td>
        <td className="px-4 py-3 text-sm">
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {contract.consumers.length ? contract.consumers.map((c) => <DomainRef key={c} domain={c} names={names} />) : <span className="text-subtle">none yet</span>}
          </div>
        </td>
        <td className="px-4 py-3">
          <div className="flex flex-col items-start gap-1">
            <ContractStatusChip status={contract.status} />
            <select
              aria-label={`Status of ${contract.key}`}
              className="rounded border border-border bg-surface px-1 py-0.5 text-xs text-subtle"
              value={contract.status}
              disabled={update.isPending}
              onChange={(e) => update.mutate(e.target.value as ContractStatus)}
            >
              {CONTRACT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/-/g, " ")}
                </option>
              ))}
            </select>
          </div>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="bg-muted/50 px-4 py-3">
            {contract.description && <p className="mb-2 text-sm">{contract.description}</p>}
            {contract.spec ? (
              <pre className="max-h-96 overflow-auto rounded-lg border border-border bg-surface p-3 font-mono text-xs">{contract.spec}</pre>
            ) : (
              <p className="text-sm text-subtle">No spec file was attached to this contract.</p>
            )}
            {contract.run_id && (
              <Link to={`/runs/${contract.run_id}`} className="mt-2 inline-block text-xs text-accent hover:underline">
                Design that last recorded it →
              </Link>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export function ContractsPage() {
  const { data: domains = [] } = useDomains();
  const { data: contracts, isLoading } = useQuery({ queryKey: ["contracts"], queryFn: () => api.contracts() });
  const [domain, setDomain] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const names = new Map(domains.map((d) => [d.key, d.name]));
  const shown = (contracts ?? []).filter(
    (c) => (!domain || c.provider === domain || c.consumers.includes(domain)) && (!kind || c.kind === kind) && (!status || c.status === status),
  );

  return (
    <div className="space-y-6">
      <PageTitle
        icon={<ShareIcon />}
        title="Contracts"
        subtitle="APIs, events and commands between domains. Recorded when a migration's integration design is approved, and reused by later migrations."
        tone="from-fuchsia-500 to-pink-500"
      />
      <div className="flex flex-wrap gap-2">
        <select aria-label="Domain" className={select} value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">All domains</option>
          {domains.map((d) => (
            <option key={d.key} value={d.key}>
              {d.name}
            </option>
          ))}
        </select>
        <select aria-label="Kind" className={select} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          <option value="api">API</option>
          <option value="event">Event</option>
          <option value="command">Command</option>
        </select>
        <select aria-label="Status" className={select} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {CONTRACT_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/-/g, " ")}
            </option>
          ))}
        </select>
      </div>
      {isLoading ? (
        <p className="text-subtle">Loading…</p>
      ) : !contracts?.length ? (
        <div className="card p-6 text-sm text-subtle">
          No contracts yet. They appear here when a migration's <span className="font-medium text-fg">Cross-domain integration design</span> is approved.
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[48rem] text-left">
            <thead className="text-xs uppercase tracking-wide text-subtle">
              <tr>
                <th className="px-4 py-2 font-medium">Contract</th>
                <th className="px-4 py-2 font-medium">Kind</th>
                <th className="px-4 py-2 font-medium">Provider</th>
                <th className="px-4 py-2 font-medium">Consumers</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <ContractRow key={c.key} contract={c} names={names} />
              ))}
            </tbody>
          </table>
          {shown.length === 0 && <p className="px-4 py-6 text-sm text-subtle">No contracts match these filters.</p>}
        </div>
      )}
    </div>
  );
}
