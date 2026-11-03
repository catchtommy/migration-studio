import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, type RunDetail, type StageDef } from "../lib/api";
import { ChangeStepPanel, changedSince } from "./ChangeStepPanel";
import { RestartPanel } from "./RestartPanel";

vi.mock("../lib/hooks", () => ({ useCan: () => true }));

const step = (key: string, title: string, kind: "agent" | "gate" = "agent", reviews: string[] = []): StageDef =>
  ({ key, title, kind, description: "", reviews }) as StageDef;
const stages: StageDef[] = [
  step("analysis", "Analysis"),
  step("gate_analysis", "Approve analysis", "gate", ["analysis"]),
  step("modulith_design", "Modulith design"),
  step("gate_design", "Approve design", "gate", ["modulith_design"]),
  step("modular_monolith", "Module in monolith"),
  step("microservice_scaffold", "Spring Boot microservice"),
  step("gate_tests", "Approve domain service", "gate", ["modular_monolith", "microservice_scaffold"]),
];
const done = (stage: string, at: string) => ({ id: Math.random(), stage, attempt: 1, status: "completed", started_at: at }) as never;

const run = (status = "awaiting_approval", extra: Partial<RunDetail> = {}): RunDetail =>
  ({
    id: "r1",
    status,
    current_stage: "microservice_scaffold",
    branch: "feature/x",
    stages: [done("analysis", "2026-11-01T10:00"), done("modulith_design", "2026-11-01T11:00"), done("modular_monolith", "2026-11-01T12:00"), done("microservice_scaffold", "2026-11-01T13:00")],
    changeable: { analysis: "Analysis", modulith_design: "Modulith design" },
    rerun_blocked: {},
    ...extra,
  }) as RunDetail;

const show = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

test("an approved documents step can be changed, choosing which later steps update", async () => {
  const change = vi.spyOn(api, "changeStep").mockResolvedValue({} as never);
  show(<ChangeStepPanel run={run()} stage={stages[2]} stages={stages} />);
  fireEvent.click(screen.getByRole("button", { name: "✎ Change this step" }));
  const box = (name: string) => screen.getByRole("checkbox", { name: new RegExp(`^${name}`) }) as HTMLInputElement;
  expect(box("Module in monolith").checked && box("Spring Boot microservice").checked).toBe(true); // all by default
  fireEvent.click(box("Spring Boot microservice"));
  expect(screen.getByText(/Spring Boot microservice builds on Module in monolith, which updates/)).toBeInTheDocument();
  fireEvent.click(box("Module in monolith")); // untick its base too: no warning left
  expect(screen.queryByText(/builds on/)).toBeNull();
  fireEvent.click(box("Module in monolith")); // ticking a step ticks what builds on it
  expect(box("Spring Boot microservice").checked).toBe(true);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Retry the KYC call once" } });
  fireEvent.click(screen.getByRole("button", { name: "Change and carry forward" }));
  await waitFor(() =>
    expect(change).toHaveBeenCalledWith("r1", "modulith_design", "Retry the KYC call once", ["modular_monolith", "microservice_scaffold"]),
  );
});

test("a code step has no Change action, and a working run says the step stops first", () => {
  const { unmount } = show(<ChangeStepPanel run={run()} stage={stages[4]} stages={stages} />);
  expect(screen.queryByRole("button", { name: "✎ Change this step" })).toBeNull();
  unmount();
  show(<ChangeStepPanel run={run("running")} stage={stages[0]} stages={stages} />);
  fireEvent.click(screen.getByRole("button", { name: "✎ Change this step" }));
  expect(screen.getByText(/The working step \(Spring Boot microservice\) stops at its next turn first/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Stop and change" })).toBeDisabled(); // no change written yet
});

test("while the run works, Re-run from here says why and points to Change this step", () => {
  show(<RestartPanel run={run("running")} stage={stages[0]} stages={stages} completed />);
  expect(screen.getByRole("button", { name: "↻ Re-run from here" })).toBeDisabled();
  expect(screen.getByText(/use Change this step \(the working step stops first\)/)).toBeInTheDocument();
});

test("a step kept through a later change of an earlier step is marked", () => {
  const changed = run("completed", {
    stages: [...run().stages, { ...(done("modulith_design", "2026-11-02T09:00") as object), attempt: 2 } as never],
  });
  expect(changedSince(changed, stages, "modular_monolith")).toBe("Modulith design");
  expect(changedSince(run(), stages, "modular_monolith")).toBeNull();
});

test("a step that runs again anyway is ticked and locked, with the reason", () => {
  const failed = run("failed", {
    current_stage: "microservice_scaffold",
    stages: [...run().stages, { ...(done("microservice_scaffold", "2026-11-02T09:00") as object), attempt: 2, status: "failed" } as never],
  });
  show(<ChangeStepPanel run={failed} stage={stages[2]} stages={stages} />);
  fireEvent.click(screen.getByRole("button", { name: "✎ Change this step" }));
  const box = screen.getByRole("checkbox", { name: /^Spring Boot microservice/ }) as HTMLInputElement;
  expect(box.checked && box.disabled).toBe(true);
  expect(screen.getByText("runs again: its earlier work is no longer in effect")).toBeInTheDocument();
});

test("the panels say when the monolith has moved on since the run started", () => {
  show(<ChangeStepPanel run={run("awaiting_approval", { monolith_behind: 3 })} stage={stages[0]} stages={stages} />);
  fireEvent.click(screen.getByRole("button", { name: "✎ Change this step" }));
  expect(screen.getByText(/its base branch has 3 newer commits/)).toBeInTheDocument();
});
