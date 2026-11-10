import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { CompareWithRelease, ReleaseBadge } from "./AgentReleaseCompare";

const comparison = {
  key: "modulith_design",
  title: "Modulith design",
  status: "edited",
  scope: "shared",
  updated_at: "",
  fields: [
    { field: "instructions", stored: "Read the analysis.\nDesign the domain.\nOld rule.\n", default: "Read the analysis.\nDesign the domain.\nNew rule.\n", same: false },
    { field: "outputs", stored: "domain-design.md", default: "domain-design.md", same: true },
    { field: "tools", stored: "read_file", default: "read_file\nedit_file", same: false },
  ],
};
vi.mock("../lib/api", () => ({ api: { compareAgent: async () => comparison } }));

test("the badge says how an agent stands against the release; the list shows only what needs attention", () => {
  const { rerender } = render(<ReleaseBadge status="edited" />);
  expect(screen.getByText("Edited since release")).toBeInTheDocument();
  rerender(<ReleaseBadge status="current" compact />);
  expect(screen.queryByText(/release/i)).toBeNull();
  rerender(<ReleaseBadge status="older-default" compact />);
  expect(screen.getByText("older")).toBeInTheDocument();
});

test("the comparison shows only the fields that differ, side by side, and names the ones that are the same", async () => {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CompareWithRelease agentKey="modulith_design" onClose={onClose} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Old rule.")).toBeInTheDocument();
  expect(screen.getByText("New rule.")).toBeInTheDocument();
  expect(screen.getByText("edit_file")).toBeInTheDocument(); // the release's extra tool
  expect(screen.getByText(/Same in both: Required documents/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Required documents" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(onClose).toHaveBeenCalled();
});
