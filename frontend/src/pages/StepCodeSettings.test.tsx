import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { StepCodeSettings } from "./WorkflowsPage";

vi.mock("../lib/hooks", () => ({}));

test("a built-in code agent's step without Changes code is warned about", () => {
  const step = { kind: "agent", key: "modular_monolith", uses: null, include_content: false, code: null } as never;
  const { rerender } = render(<StepCodeSettings label="Step 5 code" step={step} onChange={() => {}} />);
  expect(screen.getByRole("alert")).toHaveTextContent("the code it changes is undone");
  rerender(<StepCodeSettings label="Step 5 code" step={{ ...(step as object), code: { changes_code: true } } as never} onChange={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull();
  rerender(<StepCodeSettings label="Step 1 code" step={{ ...(step as object), key: "analysis" } as never} onChange={() => {}} />);
  expect(screen.queryByRole("alert")).toBeNull(); // documents only by nature
});
