import { render, screen } from "@testing-library/react";
import type { FunctionalCoverage as Coverage } from "../lib/api";
import { FunctionalCoverage } from "./FunctionalCoverage";

const coverage: Coverage = {
  available: true,
  ids: [
    { id: "FR-001", kind: "FR", owner: "domain", scenarios: ["TS-001"], covered_by: [{ step: "independent_tests", title: "Independent tests", by: "test_fr_001", outside_owner: false }], not_covered: [], gap: false, counted: true, proven_by_owner_suite: true },
    { id: "FR-002", kind: "FR", owner: "orchestrator", scenarios: ["TS-002"], covered_by: [{ step: "independent_tests", title: "Independent tests", by: "test_fr_002", outside_owner: true }], not_covered: [], gap: true, counted: false, proven_by_owner_suite: false },
    { id: "BR-001", kind: "BR", owner: "ui unchanged", scenarios: ["TS-001"], covered_by: [], not_covered: [], gap: false, counted: false, proven_by_owner_suite: false },
  ],
  summary: { FR: { total: 2, covered: 1 }, BR: { total: 1, covered: 0 }, MSG: { total: 0, covered: 0 } },
  reported_by: [{ step: "modernise_and_test", title: "Modernisation" }],
  gaps: ["FR-002"],
  without_scenario: [],
  analysis_problems: ["MSG-02 in as-is.md but not in functional-spec.md section 9: the same ids in both"],
  allocation_problems: [],
  wording: ["functional-spec.md may contain class names: LoanService"],
};

test("at the release gate it shows the counts and the gaps first", () => {
  render(<FunctionalCoverage coverage={coverage} gate="gate_release" />);
  expect(screen.getByText(/FR 1 of 2 · BR 0 of 1 · MSG 0 of 0/)).toBeInTheDocument();
  expect(screen.getByText(/Not covered for its owner: FR-002/)).toBeInTheDocument();
  expect(screen.getByText("(outside its owner, not counted)")).toBeInTheDocument(); // the domain suite listed it
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows[0]).toHaveTextContent("FR-002"); // the gap first
  expect(screen.getByText("UI, unchanged")).toBeInTheDocument(); // never a gap
  expect(screen.queryByText(/don't agree/)).toBeNull(); // Analysis's problems belong to its own gate
});

test("at the analysis gate it shows the documents' problems and the wording report", () => {
  render(<FunctionalCoverage coverage={coverage} gate="gate_analysis" />);
  expect(screen.getByText(/MSG-02 in as-is.md but not in functional-spec.md/)).toBeInTheDocument();
  expect(screen.getByText("Every requirement and rule has a test scenario.")).toBeInTheDocument();
  expect(screen.getByText(/may contain class names: LoanService/)).toBeInTheDocument();
  expect(screen.queryByText(/Not covered by any step/)).toBeNull();
});

test("a run without a specification says so", () => {
  render(<FunctionalCoverage coverage={{ available: false, message: "No functional specification for this run." }} gate="gate_release" />);
  expect(screen.getByText("No functional specification for this run.")).toBeInTheDocument();
});

test("each test gate shows its owner's ids only", () => {
  const { unmount } = render(<FunctionalCoverage coverage={coverage} gate="gate_tests" />);
  expect(screen.getByText(/The domain's ids:/)).toBeInTheDocument();
  expect(screen.queryByText("FR-002")).toBeNull(); // the orchestrator's
  expect(screen.queryByText(/Not covered for its owner/)).toBeNull();
  unmount();
  render(<FunctionalCoverage coverage={coverage} gate="gate_orchestrator" />);
  expect(screen.getByText(/The orchestrator's ids:/)).toBeInTheDocument();
  expect(screen.getByText(/Not covered for its owner: FR-002/)).toBeInTheDocument();
});
