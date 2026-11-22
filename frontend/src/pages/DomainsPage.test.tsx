import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, type DiscoveryOverview, type RunStatus } from "../lib/api";
import { DomainsPage } from "./DomainsPage";

vi.mock("../lib/hooks", async (original) => ({ ...(await original<typeof import("../lib/hooks")>()), useCan: () => true }));

const overview = (status: RunStatus): DiscoveryOverview => ({
  bounded_contexts: { id: "b1", status: "completed", current_stage: null, created_at: "2026-11-22T10:00:00Z" },
  contexts_inputs: null,
  contexts_approved: true,
  domains: [{ key: "loan", name: "Loan", features: 3, run: { id: "f1", status, current_stage: "gate_features", created_at: "2026-11-22T10:00:00Z" } }],
  not_found: [],
});

function show(status: RunStatus) {
  const discovery = vi.spyOn(api, "discovery").mockResolvedValue(overview(status));
  vi.spyOn(api, "domains").mockResolvedValue([{ key: "loan", name: "Loan", description: "", context: "", agent_inputs: {}, default_workflow: null, owned_tables: [], owned_packages: [], features: 3, runs: 1, completed: 0 }] as never);
  vi.spyOn(api, "dependencyMap").mockResolvedValue({ domains: ["loan"], edges: [], order: [] } as never);
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DomainsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return discovery;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test.each([
  ["running", true],
  ["pending", true],
  ["awaiting_approval", false], // waiting for a person: can be days, nothing changes by itself
  ["changes_requested", false],
] as [RunStatus, boolean][])("while a feature listing is %s the page asks again: %s", async (status, polls) => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const discovery = show(status);
  expect((await screen.findAllByText("Loan")).length).toBeGreaterThan(0);
  const asked = discovery.mock.calls.length;
  await vi.advanceTimersByTimeAsync(3500);
  expect(discovery.mock.calls.length > asked).toBe(polls);
});
