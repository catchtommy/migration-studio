import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, type Contract } from "../lib/api";
import { ContractsPage } from "./ContractsPage";

const contract = (over: Partial<Contract>): Contract => ({
  key: "loan.get-loan", provider: "loan", kind: "api", name: "Get loan", version: "1", description: "", spec: null, spec_path: null,
  consumers: [], status: "approved", run_id: null, created_at: "2026-11-22T10:00:00Z", updated_at: "2026-11-22T10:00:00Z", ...over,
});

function show() {
  vi.spyOn(api, "contracts").mockResolvedValue([
    contract({}),
    contract({ key: "billing.invoice-issued", provider: "billing", kind: "event", name: "Invoice issued", consumers: ["loan"] }),
    contract({ key: "customer.update", provider: "customer", kind: "command", name: "Update customer", status: "provided-by-monolith" }),
  ]);
  vi.spyOn(api, "domains").mockResolvedValue([
    { key: "loan", name: "Loan" }, { key: "billing", name: "Billing" }, { key: "customer", name: "Customer" },
  ] as never);
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ContractsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("every contract is listed with its provider", async () => {
  show();
  expect(await screen.findByText("Get loan")).toBeInTheDocument();
  expect(screen.getByText("Invoice issued")).toBeInTheDocument();
  expect(screen.getByText("Update customer")).toBeInTheDocument();
});

test("filtering by domain keeps what it provides and what it uses", async () => {
  show();
  await screen.findByText("Get loan");
  fireEvent.change(screen.getByLabelText("Domain"), { target: { value: "loan" } });
  expect(screen.getByText("Get loan")).toBeInTheDocument(); // provides it
  expect(screen.getByText("Invoice issued")).toBeInTheDocument(); // consumes it
  expect(screen.queryByText("Update customer")).toBeNull();
});

test("filtering by kind and status", async () => {
  show();
  await screen.findByText("Get loan");
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "event" } });
  expect(screen.getByText("Invoice issued")).toBeInTheDocument();
  expect(screen.queryByText("Get loan")).toBeNull();
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "provided-by-monolith" } });
  expect(screen.getByText("Update customer")).toBeInTheDocument();
  expect(screen.queryByText("Invoice issued")).toBeNull();
});
