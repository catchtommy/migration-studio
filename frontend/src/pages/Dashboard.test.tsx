import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { api, setCurrentProject, type RunSummary, type StageDef } from "../lib/api";
import { Dashboard } from "./Dashboard";

vi.mock("../lib/hooks", async (original) => ({ ...(await original<typeof import("../lib/hooks")>()), useCan: () => true }));

const stages: StageDef[] = [{ key: "analysis", title: "Analysis", kind: "agent", description: "", reviews: [] }];
const summary = (over: Partial<RunSummary>): RunSummary => ({
  id: "r", domain: "loan", operation: "approve", scope: "operation", workflow: "standard", workflow_stages: stages, status: "running",
  current_stage: "analysis", created_at: "2026-11-22T10:00:00Z", updated_at: "2026-11-22T10:00:00Z", ...over,
});

function show(runs: RunSummary[]) {
  setCurrentProject("default");
  vi.spyOn(api, "runs").mockResolvedValue(runs);
  vi.spyOn(api, "domains").mockResolvedValue([
    { key: "loan", name: "Loan", features: 4, default_workflow: null },
    { key: "billing", name: "Billing", features: 0, default_workflow: null },
  ] as never);
  vi.spyOn(api, "workflows").mockResolvedValue([{ key: "standard", name: "Standard migration", scope: "operation" }] as never);
  vi.spyOn(api, "project").mockResolvedValue({ key: "default", settings: { default_workflow: "" } } as never);
  vi.spyOn(api, "features").mockResolvedValue([
    { key: "approve", name: "Approve", entry_points: [], migration: { id: "r1", status: "running" } },
    { key: "repay", name: "Repay", entry_points: ["src/Repay.java"], migration: null },
  ] as never);
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs/:runId" element={<p>Run page</p>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("the tiles count the runs, those waiting for a person and those done", async () => {
  show([
    summary({ id: "r1", status: "completed" }),
    summary({ id: "r2", operation: "repay", status: "awaiting_approval" }),
    summary({ id: "r3", operation: "close", status: "awaiting_input" }),
  ]);
  // a tile's count sits just above its label (a run's status badge can read "Completed" too)
  const tile = (label: string) => screen.getAllByText(label).find((e) => e.previousElementSibling?.tagName === "P")!.previousElementSibling;
  await waitFor(() => expect(tile("Runs")).toHaveTextContent("3")); // once the runs are in
  expect(tile("Completed")).toHaveTextContent("1");
  expect(tile("Awaiting approval")).toHaveTextContent("1");
  expect(tile("Need your input")).toHaveTextContent("1");
});

test("each domain shows its migrations and how many of its features are done", async () => {
  show([summary({ id: "r1", status: "completed" }), summary({ id: "r2", operation: "repay" })]);
  expect(await screen.findByText("1/4 features migrated")).toBeInTheDocument();
  expect(screen.getByText("No features listed")).toBeInTheDocument(); // Billing
  expect(screen.getByRole("link", { name: /repay/ })).toHaveAttribute("href", "/runs/r2");
});

test("discovery runs are listed apart from the migrations", async () => {
  show([summary({ id: "d1", domain: "loan", operation: "_features", scope: "domain", status: "completed" })]);
  expect(await screen.findByText("Discovery")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /loan · feature list/ })).toHaveAttribute("href", "/runs/d1");
});

test("a migration starts for a feature that has none yet, and opens its run", async () => {
  const create = vi.spyOn(api, "createRun").mockResolvedValue({ id: "new-run" } as never);
  show([]);
  const start = await screen.findByRole("button", { name: "Start migration" });
  await waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(start);
  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0][0]).toMatchObject({ domain: "loan", operation: "repay", workflow: "standard" }); // not "approve": it has a run
  expect(await screen.findByText("Run page")).toBeInTheDocument();
});

test("the dashboard asks again every few seconds only while an agent works", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    show([summary({ id: "r1", status: "running" })]);
    await screen.findByText("0/4 features migrated");
    const asked = (api.runs as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await vi.advanceTimersByTimeAsync(3500);
    expect((api.runs as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBeGreaterThan(asked);
  } finally {
    vi.useRealTimers();
  }
  expect(within(document.body).getByText("Runs")).toBeInTheDocument();
});
