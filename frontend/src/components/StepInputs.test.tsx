import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, type RunDetail, type StageDef, type StageExecution } from "../lib/api";
import { StageDetail } from "./StageDetail";
import { documentLink, StepInputs } from "./StepInputs";

let allowed = true;
vi.mock("../lib/hooks", () => ({ useCan: (p: string) => p !== "runs.inspect" || allowed }));

const design = {
  id: 7,
  stage: "modulith_design",
  attempt: 1,
  status: "completed",
  started_at: "2026-11-01T10:00",
  inputs: [
    { path: ".migration/loan/create/as-is.md", stage: "analysis", stage_title: "Analysis", attempt: 2, artifact_id: 41, newer_attempt: null, changed: true },
    { path: ".migration/loan/create/cross-domain.yaml", stage: "dependency_analysis", stage_title: "Domain dependencies", attempt: 1, artifact_id: 42, newer_attempt: 2, changed: false },
  ],
} as StageExecution;

const show = (ui: React.ReactNode, url = "/runs/r1") =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[url]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

test("lists the documents a step used, with their versions and links, and its full prompt", async () => {
  vi.spyOn(api, "stagePrompt").mockResolvedValue({
    system: "You design how ONE feature moves…\n\n## Guardrails\n- Never…",
    brief: "## Domain guidance\nKeep the KYC call synchronous.",
    tools: ["read_file", "write_file"],
    mode: "ai",
    provider: "copilot",
  });
  show(<StepInputs runId="r1" execution={design} />);
  expect(screen.getByText("as-is.md")).toBeInTheDocument();
  expect(screen.getByText("attempt 2")).toBeInTheDocument();
  expect(screen.getByText("changed since this step's last attempt")).toBeInTheDocument();
  expect(screen.getByText("⚠ a newer attempt (2) exists")).toBeInTheDocument();
  const links = screen.getAllByRole("link", { name: "Open" });
  expect(links[0].getAttribute("href")).toContain("step=analysis&attempt=2&doc=41");
  expect(await screen.findByText(/## Guardrails/)).toBeInTheDocument();
  expect(screen.getByText(/Keep the KYC call synchronous/)).toBeInTheDocument();
  expect(screen.getByText("Tools (2)")).toBeInTheDocument();
  expect(screen.getByText(/appended to Copilot's own/)).toBeInTheDocument();
});

test("an attempt from before the recording says so", async () => {
  vi.spyOn(api, "stagePrompt").mockRejectedValue(new Error("Not recorded"));
  show(<StepInputs runId="r1" execution={{ ...design, inputs: null }} />);
  await waitFor(() => expect(screen.getAllByText(/Not recorded for this attempt/)).toHaveLength(2));
});

const stages = [
  { key: "analysis", title: "Analysis", kind: "agent", description: "", reviews: [] },
  { key: "modulith_design", title: "Modulith design", kind: "agent", description: "", reviews: [] },
] as unknown as StageDef[];
const analysis = (attempt: number, id: number) => ({ id, stage: "analysis", attempt, status: "completed", started_at: `2026-11-0${attempt}T10:00` }) as StageExecution;
const run = {
  id: "r1",
  status: "running",
  current_stage: "modulith_design",
  stages: [analysis(1, 1), analysis(2, 2), design],
  approvals: [],
  workflow_stages: stages,
  changeable: {},
  rerun_blocked: {},
} as unknown as RunDetail;

test("the Inputs & prompt tab shows only to users allowed to inspect", () => {
  vi.spyOn(api, "artifacts").mockResolvedValue([]);
  vi.spyOn(api, "stagePrompt").mockResolvedValue({ system: "", brief: "", tools: [], mode: "stub", provider: null });
  allowed = false;
  const { unmount } = show(<StageDetail stage={stages[1]} stages={stages} run={run} events={[]} questions={[]} />);
  expect(screen.queryByRole("button", { name: "Inputs & prompt" })).toBeNull();
  unmount();
  allowed = true;
  show(<StageDetail stage={stages[1]} stages={stages} run={run} events={[]} questions={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Inputs & prompt" }));
  expect(screen.getByText("Documents used")).toBeInTheDocument();
});

test("a document link opens that attempt of the step", () => {
  vi.spyOn(api, "artifacts").mockResolvedValue([]);
  show(<StageDetail stage={stages[0]} stages={stages} run={run} events={[]} questions={[]} />, `/runs/r1${documentLink("analysis", 1, 41)}`);
  expect(screen.getByRole("button", { name: /Attempt 1/ }).className).toContain("bg-fg");
  expect(screen.getByRole("button", { name: /Attempt 2/ }).className).not.toContain("bg-fg");
});
