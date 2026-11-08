import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { api, type Domain, type MoveReview } from "../lib/api";
import { DomainFeatures } from "./DomainFeatures";

const domainsMock = vi.hoisted(() => vi.fn(() => ({ data: [] as unknown[] })));

vi.mock("../lib/hooks", async (actual) => ({
  defaultLabel: ((await actual()) as typeof import("../lib/hooks")).defaultLabel,
  useProjectDefaultWorkflow: () => "standard",
  useCan: () => true,
  useIsAdmin: () => true,
  useDomains: domainsMock,
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

describe("moving features", () => {
  const review = (over: Partial<MoveReview> = {}): MoveReview => ({
    source: "broker",
    target: "lending",
    new_domain: false,
    blockers: [],
    features: [
      { key: "view-broker", name: "View broker", description: "", entry_points: ["BrokerController.view"], tables: ["BROKER"], calls: [], used_by: [], ticked: true, blocked: null, migration: null },
    ],
    tables: [
      { table: "BROKER", owners: ["broker"], moving_features: ["view-broker"], remaining_features: [], other_features: [], selected: true, locked: false, note: "", group: "used" },
      { table: "RATE", owners: ["payments"], moving_features: ["view-broker"], remaining_features: [], other_features: [], selected: false, locked: true, note: "Owned by payments", group: "used" },
    ],
    packages: [{ package: "com.old.broker", selected: false }],
    references: [{ id: "calls:x", description: "caller in Payments: also calls Lending", selected: true }],
    warnings: ["Calls between features aren't recorded"],
    check_by_hand: { calling_features: [], dependencies: [], contracts: [], listing_paths: [] },
    copied: null,
    summary: { features: 1, tables: ["BROKER"], staying_tables: ["RATE"] },
    fingerprint: "fp",
    ...over,
  });

  const lending = { key: "lending", name: "Lending" } as Domain;

  function open(features: unknown[] = [{ key: "view-broker", name: "View broker", description: "", entry_points: [], tables: [], calls: [], migration: null }]) {
    domainsMock.mockReturnValue({ data: [broker, lending] });
    vi.spyOn(api, "discovery").mockResolvedValue({ domains: [] } as never);
    vi.spyOn(api, "project").mockResolvedValue({ settings: {} } as never);
    vi.spyOn(api, "features").mockResolvedValue(features as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/domains/broker"]}>
          <Routes>
            <Route path="/domains/broker" element={<DomainFeatures domain={broker} />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  async function toReview() {
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select View broker to move" }));
    fireEvent.click(screen.getByRole("button", { name: "Move 1 selected…" }));
    fireEvent.change(screen.getByLabelText("Domain"), { target: { value: "lending" } });
    await screen.findByRole("region", { name: "Tables" });
  }

  test("a started feature can't be selected", async () => {
    open([{ key: "view-broker", name: "View broker", description: "", entry_points: [], tables: [], calls: [], migration: { id: "r", status: "running" } }]);
    expect(await screen.findByRole("checkbox", { name: "Select View broker to move" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select all features not started" })).toBeDisabled();
  });

  test("nothing is reviewed until a destination is chosen, and confirming needs the tick", async () => {
    const preview = vi.spyOn(api, "previewMove").mockResolvedValue(review());
    const move = vi.spyOn(api, "moveFeatures").mockResolvedValue({ target: "lending", new_domain: false, moved: ["view-broker"] });
    open();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select View broker to move" }));
    fireEvent.click(screen.getByRole("button", { name: "Move 1 selected…" }));
    expect(preview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm move" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Domain"), { target: { value: "lending" } });
    const confirm = await screen.findByRole("button", { name: "Confirm move" });
    expect(confirm).toBeDisabled(); // the calls aren't acknowledged yet
    expect(move).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "I have checked calls between the moved and remaining features" }));
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(move).toHaveBeenCalledWith("broker", expect.objectContaining({ features: ["view-broker"], target: "lending", tables: ["BROKER"], packages: [], fingerprint: "fp", acknowledged_calls: true })),
    );
    const status = await screen.findByText(/Moved 1 feature to/);
    expect(within(status).getByRole("link", { name: "Lending" })).toHaveAttribute("href", "/domains/lending?tab=features");
  });

  test("ticks are sent to a fresh review, and a locked table can't be ticked", async () => {
    const preview = vi.spyOn(api, "previewMove").mockResolvedValue(review());
    open();
    await toReview();
    expect(screen.getByRole("checkbox", { name: "Move table RATE" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Move table BROKER" }));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("broker", expect.objectContaining({ tables: [] })));
    fireEvent.click(screen.getByRole("checkbox", { name: "Move View broker" }));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("broker", expect.objectContaining({ unticked: ["view-broker"], tables: [] })));
    fireEvent.click(screen.getByRole("checkbox", { name: "caller in Payments: also calls Lending" }));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("broker", expect.objectContaining({ skipped_references: ["calls:x"], unticked: ["view-broker"] })));
    fireEvent.click(screen.getByRole("checkbox", { name: "Move package com.old.broker" }));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("broker", expect.objectContaining({ packages: ["com.old.broker"], skipped_references: ["calls:x"] })));
  });

  test("the review stays on screen while it refreshes, and confirm waits for the fresh one", async () => {
    let answer: (r: MoveReview) => void = () => {};
    const preview = vi.spyOn(api, "previewMove").mockResolvedValueOnce(review());
    open();
    await toReview();
    fireEvent.click(screen.getByRole("checkbox", { name: "I have checked calls between the moved and remaining features" }));
    preview.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    fireEvent.click(screen.getByRole("checkbox", { name: "Move package com.old.broker" }));
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("region", { name: "Tables" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm move" })).toBeDisabled();
    answer(review({ packages: [{ package: "com.old.broker", selected: true }] }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm move" })).toBeEnabled());
  });

  test("a tick shows at once, before the review answers", async () => {
    const preview = vi.spyOn(api, "previewMove").mockResolvedValueOnce(review());
    open();
    await toReview();
    preview.mockImplementation(() => new Promise(() => {}));
    const broker = screen.getByRole("checkbox", { name: "Move table BROKER" });
    fireEvent.click(broker);
    expect(broker).not.toBeChecked();
    const feature = screen.getByRole("checkbox", { name: "Move View broker" });
    fireEvent.click(feature);
    expect(feature).not.toBeChecked();
    const update = screen.getByRole("checkbox", { name: "caller in Payments: also calls Lending" });
    fireEvent.click(update);
    expect(update).not.toBeChecked();
    const pack = screen.getByRole("checkbox", { name: "Move package com.old.broker" });
    fireEvent.click(pack);
    expect(pack).toBeChecked();
  });

  test("two quick ticks both count", async () => {
    const preview = vi.spyOn(api, "previewMove").mockResolvedValue(
      review({ packages: [{ package: "com.a", selected: false }, { package: "com.b", selected: false }] }),
    );
    open();
    await toReview();
    fireEvent.click(screen.getByRole("checkbox", { name: "Move package com.a" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Move package com.b" }));
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith("broker", expect.objectContaining({ packages: ["com.a", "com.b"] })));
  });

  test("a blocker keeps confirm disabled, a new domain's review says repositories aren't copied", async () => {
    vi.spyOn(api, "previewMove").mockResolvedValue(
      review({ blockers: ["Feature listing is working"], new_domain: true, copied: { context: "", default_workflow: null, agent_inputs: {} } }),
    );
    open();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select View broker to move" }));
    fireEvent.click(screen.getByRole("button", { name: "Move 1 selected…" }));
    fireEvent.click(screen.getByRole("radio", { name: "A new domain" }));
    fireEvent.change(screen.getByLabelText(/^Key/), { target: { value: "Bad Key" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Lending" } });
    await new Promise((r) => setTimeout(r, 300));
    expect(api.previewMove).not.toHaveBeenCalled(); // not a key yet
    fireEvent.change(screen.getByLabelText(/^Key/), { target: { value: "lending" } });
    expect(await screen.findByText("Feature listing is working")).toBeInTheDocument();
    expect(screen.getByText(/Repositories aren't copied/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "I have checked calls between the moved and remaining features" }));
    expect(screen.getByRole("button", { name: "Confirm move" })).toBeDisabled();
  });

  test("a refused move shows why and stays open", async () => {
    vi.spyOn(api, "previewMove").mockResolvedValue(review());
    vi.spyOn(api, "moveFeatures").mockRejectedValue(new Error("The data changed since this review: review the move again"));
    open();
    await toReview();
    fireEvent.click(screen.getByRole("checkbox", { name: "I have checked calls between the moved and remaining features" }));
    const confirm = screen.getByRole("button", { name: "Confirm move" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(await screen.findByText(/review the move again/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Tables" })).toBeInTheDocument();
  });
});
