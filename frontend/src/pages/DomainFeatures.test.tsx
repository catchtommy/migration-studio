import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { api, type Domain } from "../lib/api";
import { DomainFeatures } from "./DomainFeatures";

vi.mock("../lib/hooks", () => ({
  useCan: () => true,
  useDomains: () => ({ data: [] }),
  useWorkflows: () => ({
    data: [
      { key: "standard", name: "Standard migration", scope: "operation" },
      { key: "lean", name: "Lean", scope: "operation" },
    ],
  }),
}));

const broker = { key: "broker", name: "Broker", default_workflow: "lean", agent_inputs: {} } as unknown as Domain;

function show() {
  vi.spyOn(api, "discovery").mockResolvedValue({ domains: [] } as never);
  vi.spyOn(api, "project").mockResolvedValue({ settings: {} } as never);
  vi.spyOn(api, "features").mockResolvedValue([
    { key: "view-broker", name: "View broker", description: "", entry_points: ["BrokerController.view"], tables: [], calls: [], migration: null },
  ] as never);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/domains/broker"]}>
        <Routes>
          <Route path="/domains/broker" element={<DomainFeatures domain={broker} />} />
          <Route path="/runs/:id" element={<p>Run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("Migrate opens the start form under the feature, with the domain's workflow chosen", async () => {
  show();
  const create = vi.spyOn(api, "createRun").mockResolvedValue({ id: "run-1" } as never);
  fireEvent.click(await screen.findByRole("button", { name: "Migrate" }));
  const form = screen.getByRole("form", { name: "Start migrating View broker" });
  expect(within(form).getByLabelText("Workflow")).toHaveValue("lean");
  expect(create).not.toHaveBeenCalled(); // nothing starts until Start migration

  fireEvent.change(within(form).getByLabelText("Workflow"), { target: { value: "standard" } });
  fireEvent.click(within(form).getByRole("button", { name: "Start migration" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith({ domain: "broker", operation: "view-broker", workflow: "standard", inputs: undefined }));
  expect(await screen.findByText("Run page")).toBeInTheDocument();
});

test("Cancel closes the form without starting anything", async () => {
  show();
  const create = vi.spyOn(api, "createRun");
  fireEvent.click(await screen.findByRole("button", { name: "Migrate" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("form", { name: "Start migrating View broker" })).toBeNull();
  expect(create).not.toHaveBeenCalled();
});
