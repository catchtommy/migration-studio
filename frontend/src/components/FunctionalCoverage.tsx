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
  // The domain service's gate also approves the orchestrator module: its tests of the orchestrator's ids, for reference
  const moduleIds = coverage.ids.filter((i) => i.owner === "orchestrator");
  const count = (k: string) => ({ total: ids.filter((i) => i.kind === k).length, covered: ids.filter((i) => i.kind === k && i.counted).length });
  // The one-line status shown while the box is closed (it opens on a click)
  const status: { text: string; tone: "ok" | "warn" | "bad" } =
    gate === "gate_analysis"
      ? problems.length
        ? { text: `${problems.length} problem${problems.length === 1 ? "" : "s"}`, tone: "bad" }
        : coverage.without_scenario.length
          ? { text: `${coverage.without_scenario.length} without a test scenario`, tone: "warn" }
          : { text: "the documents agree", tone: "ok" }
      : gate === "gate_design"
        ? problems.length
          ? { text: `${problems.length} problem${problems.length === 1 ? "" : "s"}`, tone: "bad" }
          : { text: "every id allocated", tone: "ok" }
        : {
            text:
              (["FR", "BR", "MSG"] as const).map((k) => `${k} ${count(k).covered}/${count(k).total}`).join(" · ") +
              (gaps.length ? ` · ${gaps.length} not covered` : ""),
            tone: gaps.length ? "bad" : "ok",
          };
  const toneClass = { ok: "border-ok/40 bg-ok/10 text-ok", warn: "border-warn/40 bg-warn/10", bad: "border-bad/40 bg-bad/10 text-bad" }[status.tone];
  return (
    <section aria-label="Functional coverage" className="mt-4 rounded-md border border-border bg-surface text-sm">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 p-3">
          <span aria-hidden className="text-subtle transition-transform group-open:rotate-90">▸</span>
          <span className="font-medium">Functional coverage</span>
          {owner && <span className="text-subtle">· the {owner}'s ids</span>}
          <span className={`ml-auto rounded-full border px-2 py-0.5 text-xs ${toneClass}`}>{status.text}</span>
        </summary>
        <div className="border-t border-border p-3 pt-2">
          <p className="text-xs text-subtle">
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
            <div className="mt-2 max-h-96 overflow-auto">
              <table className="w-full text-left">
                <thead className="sticky top-0 bg-surface text-xs text-subtle">
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
                            <span
                              key={c.step}
                              className={`block ${c.outside_owner ? "text-subtle line-through" : c.superseded_by ? "text-xs text-subtle" : ""}`}
                              title={c.outside_owner ? "Listed by a step of the other owner: not counted" : undefined}
                            >
                              {c.superseded_by && "Earlier: "}
                              {c.title}: {c.by}
                              {c.outside_owner && <span className="ml-1 text-xs no-underline">(outside its owner, not counted)</span>}
                              {c.superseded_by && <span className="ml-1">(replaced by {c.superseded_by})</span>}
                              {!c.outside_owner && !c.superseded_by && c.counts === false && (
                                <span className="ml-1 text-xs text-subtle">(supporting: not the owner's acceptance suite)</span>
                              )}
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
            </div>
          )}
          {gate === "gate_tests" && moduleIds.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-subtle">Orchestrator module ({moduleIds.length} of the orchestrator's ids)</summary>
              <p className="mt-1 text-xs text-subtle">
                Approved at this gate with the domain; the orchestrator's ids are judged at Approve orchestrator service, by its acceptance suite.
              </p>
              <ul className="mt-1 space-y-0.5">
                {moduleIds.map((i) => {
                  const mine = i.covered_by.filter((c) => c.step === "monolith_orchestrator");
                  return (
                    <li key={i.id}>
                      <span className="font-mono text-xs">{i.id}</span>: {mine.map((c) => c.by).join("; ") || "not covered by the module's tests"}
                    </li>
                  );
                })}
              </ul>
            </details>
          )}
        </div>
      </details>
    </section>
  );
}
