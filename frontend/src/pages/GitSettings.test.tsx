import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { WorkflowGit } from "../lib/api";
import { GitSettings } from "./WorkflowsPage";

vi.mock("../lib/hooks", () => ({}));

test("one branch per domain by default; switching follows the default pattern but keeps a custom one", () => {
  const onChange = vi.fn();
  const { rerender } = render(<GitSettings value={{ commit: true, branch_per: "domain", branch_pattern: "migration/{domain}/modules" }} onChange={onChange} />);
  expect(screen.getByLabelText(/^Domain/)).toBeChecked();
  fireEvent.click(screen.getByLabelText(/^Feature/));
  expect(onChange).toHaveBeenLastCalledWith({ commit: true, branch_per: "feature", branch_pattern: "migration/{domain}/{feature}" });

  const custom: WorkflowGit = { commit: true, branch_per: "domain", branch_pattern: "team/{domain}" };
  rerender(<GitSettings value={custom} onChange={onChange} />);
  fireEvent.click(screen.getByLabelText(/^Feature/));
  expect(onChange).toHaveBeenLastCalledWith({ ...custom, branch_per: "feature" });
});
