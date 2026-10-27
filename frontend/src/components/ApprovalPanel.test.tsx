import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, type ChecklistItem, type StageDef } from "../lib/api";
import { ApprovalPanel } from "./ApprovalPanel";

vi.mock("../lib/hooks", () => ({ useCan: () => true, useMe: () => ({ data: { user: { display_name: "Ada" } } }) }));

const stages: StageDef[] = [
  { key: "as_is_doc", title: "AS-IS documentation", kind: "agent", description: "", reviews: [] },
  { key: "gate_docs", title: "Check docs", kind: "gate", description: "", reviews: ["as_is_doc"] },
];

const show = (checklist: ChecklistItem[]) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ApprovalPanel runId="r1" gate={{ gate: "gate_docs", reviews: ["as_is_doc"], checklist }} stages={stages} />
    </QueryClientProvider>,
  );

const sql: ChecklistItem = { item: "Checked the SQL", done: false, by: null, at: null };

test("nothing but the choice is shown until the reviewer picks approve or reject", () => {
  show([sql]);
  expect(screen.getByRole("button", { name: "Approve…" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reject…" })).toBeInTheDocument();
  expect(screen.queryByText(/Checklist/)).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("approving shows the checklist and waits until every item is ticked", async () => {
  const tick = vi.spyOn(api, "tickChecklist").mockResolvedValue([]);
  show([{ item: "Read the document", done: true, by: "Ada", at: "2026-10-27T10:00:00Z" }, sql]);
  fireEvent.click(screen.getByRole("button", { name: "Approve…" }));
  expect(screen.getByRole("group", { name: "Checklist" })).toHaveTextContent("1 of 2 done");
  expect(screen.getByText("Tick every item to approve.")).toBeInTheDocument();
  expect(screen.getByText(/Comments \(optional\)/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Reject/ })).toBeNull(); // no re-run choices when approving
  expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Checked the SQL"));
  await waitFor(() => expect(tick).toHaveBeenCalledWith("r1", { item: "Checked the SQL", done: true }));
});

test("a fully ticked checklist, or none, allows approving", () => {
  show([{ ...sql, done: true, by: "Ada", at: "2026-10-27T10:00:00Z" }]);
  fireEvent.click(screen.getByRole("button", { name: "Approve…" }));
  expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
});

test("rejecting shows the re-run choices, not the checklist, and needs comments", () => {
  show([sql]);
  fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
  expect(screen.queryByText(/Checklist/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByRole("button", { name: /Reject & re-run now/ })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Cover the batch job" } });
  expect(screen.getByRole("button", { name: /Reject & re-run now/ })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Approve…" })).toBeInTheDocument();
});
