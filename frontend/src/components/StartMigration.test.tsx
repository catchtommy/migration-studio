import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { Domain, Feature } from "../lib/api";
import { StartMigration } from "./StartMigration";

let projectDefault = "lean";
vi.mock("../lib/hooks", async (actual) => ({
  defaultLabel: ((await actual()) as typeof import("../lib/hooks")).defaultLabel,
  useProjectDefaultWorkflow: () => projectDefault,
  useWorkflows: () => ({
    data: [
      { key: "standard", name: "Standard migration", scope: "operation" },
      { key: "lean", name: "Lean migration", scope: "operation" },
    ],
  }),
}));
vi.mock("./Guarded", () => ({ Guarded: ({ children }: { children: React.ReactNode }) => children }));

const feature = { key: "approve", name: "Approve", entry_points: [] } as unknown as Feature;
const show = (domain: Partial<Domain>) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <StartMigration domain={{ key: "loan", name: "Loan", default_workflow: null, ...domain } as Domain} feature={feature} onCancel={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

test("a domain without a default of its own starts with the project's default", () => {
  show({});
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  expect(select.value).toBe("lean");
  expect(screen.getByRole("option", { name: "Lean migration (project default)" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Standard migration" })).toBeInTheDocument();
});

test("a domain's own default comes first, and the project's is still marked", () => {
  projectDefault = "lean";
  show({ default_workflow: "standard" });
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("standard");
  expect(screen.getByRole("option", { name: "Standard migration (domain default)" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Lean migration (project default)" })).toBeInTheDocument();
});
