import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { nextBranch, RestartPanel } from "./RestartPanel";

vi.mock("../lib/hooks", () => ({ useCan: () => true }));

const stages: StageDef[] = [
  { key: "as_is_doc", title: "AS-IS documentation", kind: "agent", description: "", reviews: [] },
  { key: "modular_monolith", title: "Module in monolith", kind: "agent", description: "", reviews: [] },
];

const show = (extra: Partial<RunDetail>) => {
  const run = { id: "r1", status: "awaiting_approval", branch: "feature/loan-approve", base_sha: "abc", repositories: [], ...extra } as unknown as RunDetail;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RestartPanel run={run} stage={stages[1]} stages={stages} completed />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /Re-run from here/ }));
  fireEvent.click(screen.getByLabelText(/Start these steps fresh/));
};

test("the next version of a branch name", () => {
  expect(nextBranch("feature/loan-approve")).toBe("feature/loan-approve-v2");
  expect(nextBranch("feature/loan-approve-v2")).toBe("feature/loan-approve-v3");
});

test("a fresh re-run can go on a new branch from the latest base when each feature has its own branch", async () => {
  const restart = vi.spyOn(api, "restart").mockResolvedValue({} as Awaited<ReturnType<typeof api.restart>>);
  show({ branch_per: "feature" });
  expect(screen.getByLabelText(/Continue on/)).toBeChecked();
  fireEvent.click(screen.getByLabelText(/New branch from the latest base branch/));
  expect(screen.getByLabelText("New branch")).toHaveValue("feature/loan-approve-v2");
  fireEvent.click(screen.getByRole("button", { name: /Start fresh 1 step/ }));
  await waitFor(() => expect(restart).toHaveBeenCalledWith("r1", "modular_monolith", undefined, true, "feature/loan-approve-v2"));
});

test("no new branch where the domain's features share one", () => {
  show({ branch_per: "domain" });
  expect(screen.queryByText(/New branch from the latest base branch/)).toBeNull();
});
