import { fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getAllByRole("row").some((r) => r.textContent?.includes("FR-002"))).toBe(false); // the orchestrator's
  expect(screen.queryByText(/Not covered for its owner/)).toBeNull();
  unmount();
  render(<FunctionalCoverage coverage={coverage} gate="gate_orchestrator" />);
  expect(screen.getByText(/The orchestrator's ids:/)).toBeInTheDocument();
  expect(screen.getByText(/Not covered for its owner: FR-002/)).toBeInTheDocument();
});

test("the domain gate lists the orchestrator module's tests, and a supporting row is marked", () => {
  const withModule: Coverage = {
    ...coverage,
    ids: [
      { ...coverage.ids[0], covered_by: [...coverage.ids[0].covered_by, { step: "modernise_and_test", title: "Modernisation", by: "unit_fr_001", outside_owner: false, counts: false }] },
      { ...coverage.ids[1], covered_by: [{ step: "monolith_orchestrator", title: "Orchestrator in monolith", by: "OrderTest", outside_owner: false }] },
    ],
  } as Coverage;
  render(<FunctionalCoverage coverage={withModule} gate="gate_tests" />);
  expect(screen.getByText(/Orchestrator module \(1 of the orchestrator's ids\)/)).toBeInTheDocument();
  expect(screen.getByText(/OrderTest/)).toBeInTheDocument();
  expect(screen.getByText("(supporting: not the owner's acceptance suite)")).toBeInTheDocument();
});

test("once the code is lifted into the service, the service's test is shown and the module's is the earlier one", () => {
  const lifted: Coverage = {
    ...coverage,
    ids: [
      {
        ...coverage.ids[0],
        id: "BR-001",
        kind: "BR",
        covered_by: [
          { step: "modular_monolith", title: "Module in monolith", by: "LimitTest", outside_owner: false, counts: false, superseded_by: "Spring Boot microservice" },
          { step: "microservice_scaffold", title: "Spring Boot microservice", by: "LimitTest (copied)", outside_owner: false, counts: true },
        ],
      },
    ],
  } as Coverage;
  render(<FunctionalCoverage coverage={lifted} gate="gate_tests" />);
  expect(screen.getByText(/Spring Boot microservice: LimitTest \(copied\)/)).toBeInTheDocument();
  expect(screen.getByText("(replaced by Spring Boot microservice)")).toBeInTheDocument();
});

test("the box starts closed with a one-line status, and opens on a click", () => {
  const { container } = render(<FunctionalCoverage coverage={coverage} gate="gate_release" />);
  const box = container.querySelector("details")!;
  expect(box.open).toBe(false);
  expect(screen.getByText("FR 1/2 · BR 0/1 · MSG 0/0 · 1 not covered")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Functional coverage"));
  expect(box.open).toBe(true);
});
