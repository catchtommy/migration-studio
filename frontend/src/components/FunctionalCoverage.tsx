import type { FunctionalCoverage as Coverage } from "../lib/api";

const OWNER: Record<string, string> = {
  domain: "Domain",
  orchestrator: "Orchestrator",
  "other domain": "Other domain",
  "ui unchanged": "UI, unchanged",
};

// The test gates review one owner's ids each: the domain service's, then the orchestrator service's.
const GATE_OWNER: Record<string, string> = { gate_tests: "domain", gate_orchestrator: "orchestrator" };

/** The functional specification's coverage (app/functional_spec.py), a report: nothing here blocks approving. At the
 * analysis gate, whether the three documents agree; at the design gate, the allocation; at a test gate, its owner's
 * ids (a requirement counts only when the owner's acceptance suite proves it); at the release, every id. Gaps first. */
export function FunctionalCoverage({ coverage, gate }: { coverage: Coverage; gate: string }) {
  if (!coverage.available) return <p className="text-sm text-subtle">{coverage.message}</p>;
  const problems = gate === "gate_analysis" ? coverage.analysis_problems : gate === "gate_design" ? coverage.allocation_problems : [];
  const later = gate !== "gate_analysis" && gate !== "gate_design";
  const owner = GATE_OWNER[gate];
  // A test gate: its owner's ids (and those without an owner, in a run without an allocation)
  const ids = owner ? coverage.ids.filter((i) => i.owner === owner || i.owner === null) : coverage.ids;
  const gaps = ids.filter((i) => i.gap).map((i) => i.id);
  const count = (k: string) => ({ total: ids.filter((i) => i.kind === k).length, covered: ids.filter((i) => i.kind === k && i.counted).length });
  return (
    <section aria-label="Functional coverage" className="mt-4 rounded-md border border-border bg-surface p-3 text-sm">
      <h3 className="font-medium">Functional coverage</h3>
      <p className="mt-0.5 text-xs text-subtle">
        The functional specification's requirements (FR), business rules (BR) and messages (MSG), and what covers each. A report: it doesn't stop you approving.
      </p>
      {problems.length > 0 && (
        <div className="mt-2 rounded border border-bad/40 bg-bad/5 p-2">
          <p className="font-medium text-bad">The documents don't agree (they may have been edited here):</p>
          <ul className="mt-1 list-disc pl-5">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {gate === "gate_analysis" && (
        <>
          <p className="mt-2">
            {coverage.without_scenario.length === 0
              ? "Every requirement and rule has a test scenario."
              : `Without a test scenario: ${coverage.without_scenario.join(", ")}`}
          </p>
          {coverage.wording.length > 0 && (
            <div className="mt-2 rounded border border-warn/40 bg-warn/5 p-2">
              <p className="font-medium">The specification may contain technical details (it is for business users):</p>
              <ul className="mt-1 list-disc pl-5">
                {coverage.wording.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {later && (
        <>
          <p className="mt-2">
            {owner && <span className="font-medium">The {owner}'s ids: </span>}
            {(["FR", "BR", "MSG"] as const).map((k) => `${k} ${count(k).covered} of ${count(k).total}`).join(" · ")}
            {coverage.reported_by.length > 0 && <span className="text-subtle"> — reported by {coverage.reported_by.map((r) => r.title).join(", ")}</span>}
          </p>
          {gaps.length > 0 && (
            <p className="mt-1 font-medium text-bad">
              Not covered for its owner: {gaps.join(", ")}
              <span className="block text-xs font-normal text-subtle">A requirement counts only when its owner's acceptance suite proves it.</span>
            </p>
          )}
        </>
      )}
      {(later || gate === "gate_design") && ids.length > 0 && (
        <details className="mt-2" open={later && gaps.length > 0}>
          <summary className="cursor-pointer text-subtle">Each id</summary>
          <table className="mt-1 w-full text-left">
            <thead className="text-xs text-subtle">
              <tr>
                <th className="py-1 font-medium">Id</th>
                <th className="py-1 font-medium">Owner</th>
                <th className="py-1 font-medium">Covered by</th>
              </tr>
            </thead>
            <tbody>
              {[...ids]
                .sort((a, b) => Number(b.gap) - Number(a.gap))
                .map((i) => (
                  <tr key={i.id} className={`border-t border-border ${i.gap ? "text-bad" : ""}`}>
                    <td className="py-1 font-mono text-xs">{i.id}</td>
                    <td className="py-1">{i.owner ? OWNER[i.owner] ?? i.owner : "–"}</td>
                    <td className="py-1">
                      {i.covered_by.length === 0 && (i.gap ? "nothing yet" : "–")}
                      {i.covered_by.map((c) => (
                        <span key={c.step} className={`block ${c.outside_owner ? "text-subtle line-through" : ""}`} title={c.outside_owner ? "Listed by a step of the other owner: not counted" : undefined}>
                          {c.title}: {c.by}
                          {c.outside_owner && <span className="ml-1 text-xs no-underline">(outside its owner, not counted)</span>}
                        </span>
                      ))}
                      {i.not_covered.map((c) => (
                        <span key={c.step} className="block text-xs text-subtle">
                          {c.title}: not covered{c.reason ? ` (${c.reason})` : ""}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
