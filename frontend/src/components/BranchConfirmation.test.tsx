import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { PendingBranches } from "../lib/api";
import { BranchConfirmation } from "./BranchConfirmation";

vi.mock("../lib/hooks", () => ({ useCan: () => true }));

const pending = (existing: PendingBranches["existing_branch"]): PendingBranches => ({
  stage: "orchestrator_service",
  stage_title: "Orchestrator microservice",
  feature_branch: "migration/customer/view-customer",
  repository: { key: "orch", name: "orchestrator-test", url: "https://git.example.com/orchestrator-test.git", base_branch: "main", checked_out: false, default_branch: "main" },
  existing_branch: existing,
});

const show = (p: PendingBranches) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BranchConfirmation runId="r1" pending={p} />
    </QueryClientProvider>,
  );

test("says when the feature branch already exists", () => {
  show(pending({ in_run: false, message: "Branch migration/customer/view-customer already exists in orchestrator-test. It continues on it." }));
  expect(screen.getByRole("status")).toHaveTextContent("An existing branch already exists.");
  expect(screen.getByRole("status")).toHaveTextContent("continues on it");
  fireEvent.change(screen.getByLabelText("Feature branch"), { target: { value: "migration/other" } });
  expect(screen.queryByRole("status")).toBeNull(); // about the proposed name only; a new name is checked on confirm
  expect(screen.getByText(/checked when you confirm/)).toBeInTheDocument();
});

test("says nothing when the branch is new", () => {
  show(pending(null));
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("button", { name: "Confirm and start Orchestrator microservice" })).toBeEnabled();
});
