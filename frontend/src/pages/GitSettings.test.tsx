import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { GitSettings } from "./WorkflowsPage";

vi.mock("../lib/hooks", () => ({}));

test("each feature runs on its own branch: no branching choice, only the branch name", () => {
  const onChange = vi.fn();
  render(<GitSettings value={{ commit: true, branch_pattern: "migration/{domain}/{feature}" }} onChange={onChange} />);
  expect(screen.getByText(/Each feature runs on its own branch/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/^Domain/)).toBeNull();
  fireEvent.change(screen.getByDisplayValue("migration/{domain}/{feature}"), { target: { value: "team/{domain}/{feature}" } });
  expect(onChange).toHaveBeenLastCalledWith({ commit: true, branch_pattern: "team/{domain}/{feature}" });
});
