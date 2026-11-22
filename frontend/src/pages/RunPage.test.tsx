import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { RunPage } from "./RunPage";

vi.mock("../lib/hooks", async (original) => ({
  ...(await original<typeof import("../lib/hooks")>()),
  useRunEvents: () => [],
  useCan: () => true,
  useIsAdmin: () => true,
  useMe: () => ({ data: { user: { display_name: "Ada", permissions: [] } } }),
}));

const stages: StageDef[] = [
  { key: "analysis", title: "Analysis", kind: "agent", description: "", reviews: [] },
  { key: "gate_analysis", title: "Approve analysis", kind: "gate", description: "", reviews: ["analysis"] },
];

const run = (over: Partial<RunDetail> = {}): RunDetail =>
  ({
    id: "r1", domain: "loan", operation: "approve", scope: "operation", workflow: "standard", workflow_stages: stages, status: "running",
    current_stage: "analysis", branch: "migration/loan/approve", base_branch: "main", repository: "https://git.example.com/m.git", repositories: null,
    legacy: null, delivery: null, error: null, created_at: "2026-11-22T10:00:00Z", updated_at: "2026-11-22T10:00:00Z", workspace: null, inputs: null,
    stages: [{ id: 1, stage: "analysis", attempt: 1, status: "running", start_sha: null, end_sha: null, feedback: null, error: null, started_at: "2026-11-22T10:00:00Z", finished_at: null }],
    approvals: [], pending_gate: null, pending_branches: null, pending_commit: null, pending_rerun: null, base_sha: "abcdef1234567890", monolith_behind: null,
    workflow_update: null, last_event_id: 0, ...over,
  }) as RunDetail;

function show(detail: RunDetail | Error) {
  if (detail instanceof Error) vi.spyOn(api, "run").mockRejectedValue(detail);
  else vi.spyOn(api, "run").mockResolvedValue(detail);
  vi.spyOn(api, "questions").mockResolvedValue([]);
  vi.spyOn(api, "workflows").mockResolvedValue([{ key: "standard", name: "Standard migration" }] as never);
  vi.spyOn(api, "artifacts").mockResolvedValue([]);
  return render(
    <MemoryRouter initialEntries={["/runs/r1"]}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Routes>
          <Route path="/runs/:runId" element={<RunPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("the run's feature, branch, workflow and monolith commit are shown", async () => {
  show(run());
  expect(await screen.findByText("approve")).toBeInTheDocument();
  expect(screen.getByText("migration/loan/approve")).toBeInTheDocument();
  expect(await screen.findByText(/Standard migration workflow/)).toBeInTheDocument();
  expect(screen.getByText("abcdef1234")).toBeInTheDocument();
});

test("a run waiting at a gate offers the approval", async () => {
  show(run({ status: "awaiting_approval", current_stage: "gate_analysis", pending_gate: { gate: "gate_analysis", reviews: ["analysis"], checklist: [] } }));
  expect(await screen.findByRole("button", { name: "Approve…" })).toBeInTheDocument();
});

test("a failed run shows why", async () => {
  show(run({ status: "failed", error: "The model timed out" }));
  expect(await screen.findByText("The model timed out")).toBeInTheDocument();
});

test("newer commits on the monolith's base branch are pointed out", async () => {
  show(run({ monolith_behind: 3 }));
  expect(await screen.findByText(/has 3 newer commits since this run started/)).toBeInTheDocument();
});

test("the operation's inputs given to the agents can be looked at", async () => {
  show(run({ inputs: { paths: ["src/LoanController.java"], notes: "Mind the batch job" } }));
  expect(await screen.findByText(/Operation inputs/)).toBeInTheDocument();
  expect(screen.getByText("src/LoanController.java")).toBeInTheDocument();
  expect(screen.getByText("Mind the batch job")).toBeInTheDocument();
});

test("a run that can't be loaded says why", async () => {
  show(new Error("Run not found"));
  expect(await screen.findByText("Run not found")).toBeInTheDocument();
});
