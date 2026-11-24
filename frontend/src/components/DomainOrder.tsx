import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type DomainSummary } from "../lib/api";
import { DomainAvatar } from "./DomainAvatar";

/** Move the item at `from` to `to`. */
export function moved<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const out = [...items];
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

/** Set the order domains are migrated in: drag a row (or use its arrows), then save. `suggested`: the dependency map's
 * order, offered as a starting point. */
export function DomainOrder({ domains, suggested, onDone }: { domains: DomainSummary[]; suggested: string[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [order, setOrder] = useState(() => domains.map((d) => d.key));
  const [dragging, setDragging] = useState<number | null>(null);
  const byKey = new Map(domains.map((d) => [d.key, d]));
  const save = useMutation({
    mutationFn: () => api.reorderDomains(order),
    onSuccess: (saved) => {
      queryClient.setQueryData(["domains"], saved);
      queryClient.invalidateQueries({ queryKey: ["discovery"] });
      onDone();
    },
  });
  const useSuggested = () => {
    const known = suggested.filter((k) => byKey.has(k));
    setOrder([...known, ...order.filter((k) => !known.includes(k))]);
  };
  const changed = order.join() !== domains.map((d) => d.key).join();

  return (
    <section className="card space-y-3 border-t-4 border-t-accent p-4" aria-label="Migration order">
      <div>
        <h2 className="font-semibold">Migration order</h2>
        <p className="mt-0.5 text-sm text-subtle">Drag the domains into the order you'll migrate them, first at the top, or use the arrows. Domains are listed in this order.</p>
      </div>
      <ol className="divide-y divide-border rounded-md border border-border">
        {order.map((key, i) => {
          const d = byKey.get(key)!;
          return (
            <li
              key={key}
              draggable
              aria-label={d.name}
              onDragStart={(e) => {
                setDragging(i);
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", key); // Firefox starts a drag only with data
              }}
              onDragOver={(e) => {
                e.preventDefault(); // allows dropping here
                if (dragging !== null && dragging !== i) {
                  setOrder((o) => moved(o, dragging, i));
                  setDragging(i);
                }
              }}
              onDrop={(e) => e.preventDefault()}
              onDragEnd={() => setDragging(null)}
              className={`flex cursor-grab items-center gap-3 px-3 py-2 text-sm active:cursor-grabbing ${dragging === i ? "bg-accent/10" : "bg-surface"}`}
            >
              <span aria-hidden className="select-none text-subtle">
                ⠿
              </span>
              <span className="w-6 text-right text-xs tabular-nums text-subtle">{i + 1}</span>
              <DomainAvatar name={d.name} domainKey={d.key} size="h-6 w-6 text-[11px]" />
              <span className="flex-1 font-medium">{d.name}</span>
              <code className="text-xs text-subtle">{d.key}</code>
              <button type="button" aria-label={`Move ${d.name} up`} disabled={i === 0} onClick={() => setOrder((o) => moved(o, i, i - 1))} className="rounded px-1.5 hover:bg-muted disabled:opacity-30">
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move ${d.name} down`}
                disabled={i === order.length - 1}
                onClick={() => setOrder((o) => moved(o, i, i + 1))}
                className="rounded px-1.5 hover:bg-muted disabled:opacity-30"
              >
                ↓
              </button>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => save.mutate()} disabled={save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {save.isPending ? "Saving…" : "Save order"}
        </button>
        <button onClick={onDone} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
        {suggested.length > 0 && (
          <button onClick={useSuggested} className="text-sm text-accent hover:underline" title="From the cross-domain dependencies: domains others depend on the least go first">
            Use the suggested order
          </button>
        )}
        {changed && !save.isPending && <span className="text-xs text-subtle">Not saved yet</span>}
        {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
      </div>
    </section>
  );
}
