import type { FunctionalCoverage as Coverage } from "../lib/api";

const OWNER: Record<string, string> = {
  domain: "Domain",
  orchestrator: "Orchestrator",
  "other domain": "Other domain",
  "ui unchanged": "UI, unchanged",
};

/** The functional specification's coverage (app/functional_spec.py), a report: nothing here blocks approving. At the
 * analysis gate, whether the three documents agree; at the design gate, the allocation; later, which step covered each
 * requirement, rule and message, with the gaps first. */
export function FunctionalCoverage({ coverage, gate }: { coverage: Coverage; gate: string }) {
  if (!coverage.available) return <p className="text-sm text-subtle">{coverage.message}</p>;
  const problems = gate === "gate_analysis" ? coverage.analysis_problems : gate === "gate_design" ? coverage.allocation_problems : [];
  const later = gate !== "gate_analysis" && gate !== "gate_design";
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
            {(["FR", "BR", "MSG"] as const).map((k) => `${k} ${coverage.summary[k].covered} of ${coverage.summary[k].total}`).join(" · ")}
            {coverage.reported_by.length > 0 && <span className="text-subtle"> — reported by {coverage.reported_by.map((r) => r.title).join(", ")}</span>}
          </p>
          {coverage.gaps.length > 0 && (
            <p className="mt-1 font-medium text-bad">Not covered by any step for its owner: {coverage.gaps.join(", ")}</p>
          )}
        </>
      )}
      {(later || gate === "gate_design") && coverage.ids.length > 0 && (
        <details className="mt-2" open={later && coverage.gaps.length > 0}>
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
              {[...coverage.ids]
                .sort((a, b) => Number(b.gap) - Number(a.gap))
                .map((i) => (
                  <tr key={i.id} className={`border-t border-border ${i.gap ? "text-bad" : ""}`}>
                    <td className="py-1 font-mono text-xs">{i.id}</td>
                    <td className="py-1">{i.owner ? OWNER[i.owner] ?? i.owner : "–"}</td>
                    <td className="py-1">
                      {i.covered_by.map((c) => `${c.title}: ${c.by}`).join("; ") || (i.gap ? "nothing yet" : "–")}
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
