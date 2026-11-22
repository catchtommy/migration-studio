import { fireEvent, render, screen } from "@testing-library/react";
import type { AuditEvent } from "../lib/api";
import { ACTIVITY_PAGE, ActivityLog } from "./ActivityLog";

const event = (id: number, stage: string | null = null): AuditEvent =>
  ({ id, stage, kind: "log", message: `line ${id}`, created_at: "2026-11-22T10:00:00Z" }) as AuditEvent;

test("an empty log says so", () => {
  render(<ActivityLog events={[]} />);
  expect(screen.getByText("No activity yet.")).toBeInTheDocument();
});

test("a long log shows its latest lines, and earlier ones on request", () => {
  const events = Array.from({ length: ACTIVITY_PAGE + 50 }, (_, i) => event(i + 1));
  render(<ActivityLog events={events} />);
  expect(screen.queryByText("line 1")).toBeNull();
  expect(screen.getByText(`line ${ACTIVITY_PAGE + 50}`)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show earlier (50 more)" }));
  expect(screen.getByText("line 1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Show earlier/ })).toBeNull();
});

test("a step's activity shows only its own lines", () => {
  render(<ActivityLog events={[event(1, "analysis"), event(2, "design"), event(3, "analysis")]} stage="analysis" />);
  expect(screen.getByText("line 1")).toBeInTheDocument();
  expect(screen.getByText("line 3")).toBeInTheDocument();
  expect(screen.queryByText("line 2")).toBeNull();
});
