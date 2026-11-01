import { render, screen } from "@testing-library/react";
import type { StageExecution } from "../lib/api";
import { runTokens, TokenUsage } from "./TokenUsage";

const attempt = (usage: StageExecution["usage"], n = 1) =>
  ({ id: n, stage: "analysis", attempt: n, status: "completed", start_sha: null, end_sha: null, feedback: null, error: null, started_at: "", finished_at: null, usage }) as StageExecution;

const usage = {
  calls: 3,
  input_tokens: 40_000,
  output_tokens: 6_000,
  cache_read_tokens: 25_000,
  cache_write_tokens: 4_000,
  models: ["claude-opus-5-5"],
  documents: {
    ".migration/loan/approve/as-is.md": { output_tokens: 3_500, writes: 2, size_tokens: 2_900 },
    ".migration/loan/approve/test-scenarios.md": { output_tokens: 1_200, writes: 1, size_tokens: 1_100 },
  },
  code: {},
};

test("a step's tokens: totals, then each document's share, the rest as reasoning and reading", () => {
  render(<TokenUsage execution={attempt(usage)} attempts={[attempt(usage)]} />);
  expect(screen.getByText("46,000")).toBeInTheDocument(); // total
  const rows = screen.getAllByRole("row").map((r) => r.textContent);
  expect(rows[1]).toContain("as-is.md");
  expect(rows[1]).toContain("3,500");
  expect(rows[2]).toContain("test-scenarios.md");
  expect(rows.at(-1)).toContain("1,300"); // 6,000 - 3,500 - 1,200
});

test("no model call: says so; the run's total adds up every attempt", () => {
  render(<TokenUsage execution={attempt(null)} attempts={[attempt(null)]} />);
  expect(screen.getByText(/No AI tokens recorded/)).toBeInTheDocument();
  expect(runTokens([attempt(usage, 1), attempt(null, 2), attempt(usage, 3)])).toEqual({ total: 92_000, input: 80_000, output: 12_000 });
});
