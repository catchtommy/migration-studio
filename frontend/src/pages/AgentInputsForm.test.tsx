import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, type Agent, type Domain } from "../lib/api";
import { AgentInputsForm } from "./DomainPage";

vi.mock("../lib/hooks", () => ({
  useCan: () => true,
  useAgents: () => ({ data: [] }),
  useDomains: () => ({ data: [] }),
  useWorkflows: () => ({
    data: [
      { key: "lean", name: "Lean migration", scope: "operation", steps: [{ kind: "agent", key: "microservice_scaffold" }, { kind: "agent", key: "as_is_doc" }] },
      { key: "standard", name: "Standard migration", scope: "operation", steps: [{ kind: "agent", key: "as_is_doc" }, { kind: "gate", key: "g" }, { kind: "agent", key: "boundary_design" }] },
      { key: "list_features", name: "List features", scope: "domain", steps: [{ kind: "agent", key: "deep_dive" }] },
    ],
  }),
}));

const agents = [
  { key: "as_is_doc", title: "AS-IS documentation" },
  { key: "boundary_design", title: "Boundary design" },
  { key: "microservice_scaffold", title: "Spring Boot microservice" },
  { key: "deep_dive", title: "Deep dive" },
  { key: "orchestrator_service", title: "Orchestrator microservice" },
] as Agent[];

const show = (domain: Partial<Domain>) => {
  vi.spyOn(api, "project").mockResolvedValue({ settings: { monolith_repo: "https://github.com/acme/mono.git" } } as never);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AgentInputsForm
          domain={{ key: "broker", name: "Broker", context: "", agent_inputs: {}, workflows: [{ key: "lean", name: "Lean migration", agents: ["microservice_scaffold", "as_is_doc"], code_agents: ["microservice_scaffold"] }], ...domain } as Domain}
          agents={agents}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

test("the agents of the domain's workflows are shown, in order; a filter picks another workflow or all", () => {
  try {
    localStorage.clear();
  } catch {
    // no storage: the default view
  }
  show({ agent_inputs: { deep_dive: { paths: [], notes: "kept from before" } } });
  const titles = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent?.replace(/^\d+/, ""));
  expect(titles()).toEqual(["Spring Boot microservice", "AS-IS documentation", "Deep dive"]); // + one with saved inputs
  const filter = screen.getByLabelText("Show agents of");
  expect(filter).toHaveDisplayValue("Lean migration (used by Broker)");
  expect([...(filter as HTMLSelectElement).options].map((o) => o.text)).toEqual([
    "Lean migration (used by Broker)", "Standard migration", "All agents"]); // each once, migrations only
  fireEvent.change(filter, { target: { value: "standard" } });
  expect(titles()).toEqual(["AS-IS documentation", "Boundary design"]);
  fireEvent.change(filter, { target: { value: "all" } });
  expect(titles()).toHaveLength(5);
});

test("an agent that changes code in the domain's workflow needs its repository before the inputs can be saved", async () => {
  show({});
  expect(screen.queryByLabelText("AS-IS documentation repository")).toBeNull(); // documents only: no repository
  const repo = screen.getByLabelText("Spring Boot microservice repository");
  expect(await screen.findByText("Set the repository of Spring Boot microservice")).toBeInTheDocument();
  fireEvent.change(document.querySelector("textarea")!, { target: { value: "x" } }); // the shared context: a change
  expect(screen.getByRole("button", { name: "Save inputs" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Use the monolith" }));
  expect(repo).toHaveValue("https://github.com/acme/mono.git");
  expect(screen.queryByText(/Set the repository of/)).toBeNull();
  expect(screen.getByRole("button", { name: "Save inputs" })).toBeEnabled();
});


test("a domain using several workflows gets a combined option first", () => {
  show({ workflows: [
    { key: "lean", name: "Lean migration", agents: ["microservice_scaffold"], code_agents: [] },
    { key: "standard", name: "Standard migration", agents: ["as_is_doc"], code_agents: [] },
  ] });
  expect([...(screen.getByLabelText("Show agents of") as HTMLSelectElement).options].map((o) => o.text)).toEqual([
    "Broker's workflows (Lean migration, Standard migration)", "Lean migration (used by Broker)",
    "Standard migration (used by Broker)", "All agents"]);
});


test("the orchestrator's repository is optional: only a cross-domain feature needs it", () => {
  show({ workflows: [{ key: "lean", name: "Lean migration", agents: ["microservice_scaffold", "orchestrator_service"],
                       code_agents: ["microservice_scaffold", "orchestrator_service"], when_needed: ["orchestrator_service"] }],
         agent_inputs: { microservice_scaffold: { paths: [], notes: "", repository: "https://github.com/acme/broker-api.git" } } });
  expect(screen.queryByText(/Set the repository of/)).toBeNull(); // the orchestrator's is not required
});
