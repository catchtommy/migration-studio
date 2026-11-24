import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, type DomainSummary } from "../lib/api";
import { DomainOrder, moved } from "./DomainOrder";

const domain = (key: string): DomainSummary =>
  ({ key, name: key[0].toUpperCase() + key.slice(1), description: "", context: "", agent_inputs: {}, default_workflow: null, owned_tables: [], owned_packages: [], features: 0, runs: 0, completed: 0 }) as never;

const show = (suggested: string[] = []) => {
  const onDone = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DomainOrder domains={["loan", "fees", "customer"].map(domain)} suggested={suggested} onDone={onDone} />
    </QueryClientProvider>,
  );
  return onDone;
};
const rows = () => screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"));

afterEach(() => vi.restoreAllMocks());

test("moved", () => {
  expect(moved(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
  expect(moved(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  expect(moved(["a", "b"], 0, 5)).toEqual(["a", "b"]);
});

test("domains are reordered with the arrows and the order is saved", async () => {
  const reorder = vi.spyOn(api, "reorderDomains").mockResolvedValue([]);
  const onDone = show();
  fireEvent.click(screen.getByRole("button", { name: "Move Customer up" }));
  expect(rows()).toEqual(["Loan", "Customer", "Fees"]);
  expect(screen.getByRole("button", { name: "Move Loan up" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save order" }));
  await waitFor(() => expect(reorder).toHaveBeenCalledWith(["loan", "customer", "fees"]));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
});

test("a domain is dragged into place", () => {
  show();
  const [loan, , customer] = screen.getAllByRole("listitem");
  fireEvent.dragStart(customer, { dataTransfer: { setData: () => {}, effectAllowed: "" } });
  fireEvent.dragOver(loan);
  fireEvent.dragEnd(loan);
  expect(rows()).toEqual(["Customer", "Loan", "Fees"]);
});

test("the suggested order can be the starting point", () => {
  show(["fees", "gone"]);
  fireEvent.click(screen.getByRole("button", { name: "Use the suggested order" }));
  expect(rows()).toEqual(["Fees", "Loan", "Customer"]);
});
