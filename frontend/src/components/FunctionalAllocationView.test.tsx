import { fireEvent, render, screen, within } from "@testing-library/react";
import { load } from "js-yaml";
import { vi } from "vitest";
import { FunctionalAllocationView, allocationProblems, parseAllocation, type Allocation } from "./FunctionalAllocationView";

// The bank-account example: the legacy code adds a note to every loan of the client inline; once migrated, the
// client domain publishes an event and the loan domain's handler adds the notes.
const FILE = `feature: update-bank-account
domain: client
functionalities:
  - id: F1
    name: Update the client's bank account
    requirements: [FR-001]
    placement: domain
    owner: client
    reason: Writes only CLIENT_BANK
  - id: F2
    name: Add a note to every loan of the client
    legacy: Loops over the client's loans and inserts a note per loan
    requirements: [BR-003]
    placement: orchestrator
    calls: [loan]
    interactions: [X1]
    reason: Fetches the loans and writes notes
`;

const show = (onSave = vi.fn(), editable = true) => {
  render(<FunctionalAllocationView content={FILE} editable={editable} onSave={onSave} />);
  return onSave;
};

test("functionalities are grouped by where they go", () => {
  show();
  expect(within(screen.getByRole("region", { name: "Domain microservice" })).getByText("Update the client's bank account")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Orchestrator" })).getByText("Add a note to every loan of the client")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Event handlers" })).toBeNull();
});

test("a reviewer moves a functionality to an event handler; the change and the agent's placement are recorded", () => {
  const onSave = show();
  fireEvent.click(screen.getByRole("button", { name: "Change placements" }));
  fireEvent.change(screen.getByLabelText("Placement of F2"), { target: { value: "event-handler" } });
  const save = screen.getByRole("button", { name: "Save placements" });
  expect(save).toBeDisabled(); // the event isn't described yet
  expect(screen.getByRole("alert")).toHaveTextContent("F2: the event needs name, handler");

  fireEvent.change(screen.getByLabelText("Event of F2"), { target: { value: "client.bank-account-changed" } });
  fireEvent.change(screen.getByLabelText("Handler of F2"), { target: { value: "Fetch the client's loans and add a note to each" } });
  fireEvent.change(screen.getByLabelText("Owner of F2"), { target: { value: "loan" } });
  fireEvent.change(screen.getByLabelText("Comment on F2"), { target: { value: "Event-driven, not orchestrated" } });
  expect(save).toBeEnabled();
  fireEvent.click(save);

  const saved = load(onSave.mock.calls[0][0]) as Allocation;
  const f2 = saved.functionalities[1];
  expect(f2).toMatchObject({
    placement: "event-handler",
    owner: "loan",
    event: { name: "client.bank-account-changed", triggered_from: "domain", publisher: "client", handler: "Fetch the client's loans and add a note to each" },
    review: { amended: true, original: { placement: "orchestrator", calls: ["loan"] }, comment: "Event-driven, not orchestrated" },
  });
  expect(saved.functionalities[0].review).toBeUndefined(); // untouched
});

test("an event published from the orchestrator needs something on the orchestrator", () => {
  const a = parseAllocation(FILE)!;
  a.functionalities[1] = {
    ...a.functionalities[1],
    placement: "event-handler",
    owner: "loan",
    event: { name: "client.bank-account-changed", triggered_from: "orchestrator", publisher: "client", handler: "Adds the notes" },
  };
  expect(allocationProblems(a)).toEqual(["F2 publish from the orchestrator, but nothing is placed on it"]);
});

test("without the right to edit, placements can't be changed", () => {
  show(vi.fn(), false);
  expect(screen.queryByRole("button", { name: "Change placements" })).toBeNull();
});

test("a direct call names the domains it calls, typed as a comma-separated list", () => {
  const onSave = show();
  fireEvent.click(screen.getByRole("button", { name: "Change placements" }));
  fireEvent.change(screen.getByLabelText("Placement of F1"), { target: { value: "domain-call" } });
  expect(screen.getByRole("alert")).toHaveTextContent("F1: name the domain(s) it calls directly");
  const calls = screen.getByLabelText("Calls of F1");
  fireEvent.change(calls, { target: { value: "bank-directory," } });
  expect(calls).toHaveValue("bank-directory,"); // the comma stays while typing
  fireEvent.change(calls, { target: { value: "bank-directory, reference-data" } });
  expect(screen.getByRole("alert")).toHaveTextContent("F1: name the interactions it covers");
  fireEvent.change(screen.getByLabelText("Interactions of F1"), { target: { value: "X2" } });
  expect(screen.queryByLabelText("Owner of F1")).toBeNull(); // the domain's own: no other owner
  fireEvent.click(screen.getByRole("button", { name: "Save placements" }));
  const f1 = (load(onSave.mock.calls[0][0]) as Allocation).functionalities[0];
  expect(f1).toMatchObject({ placement: "domain-call", calls: ["bank-directory", "reference-data"], review: { amended: true, original: { placement: "domain" } } });
  expect(f1.owner).toBeUndefined();
});

test("another domain's rule says whether it is asked through the orchestrator or directly", () => {
  const onSave = show();
  fireEvent.click(screen.getByRole("button", { name: "Change placements" }));
  fireEvent.change(screen.getByLabelText("Placement of F2"), { target: { value: "other-domain" } });
  fireEvent.change(screen.getByLabelText("Owner of F2"), { target: { value: "loan" } });
  expect(screen.getByLabelText("Via for F2")).toHaveValue("orchestrator");
  fireEvent.click(screen.getByRole("button", { name: "Save placements" }));
  expect((load(onSave.mock.calls[0][0]) as Allocation).functionalities[1]).toMatchObject({ placement: "other-domain", owner: "loan", via: "orchestrator" });
});
