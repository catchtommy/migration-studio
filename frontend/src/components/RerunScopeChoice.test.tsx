import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { RerunScopeChoice } from "./RerunScopeChoice";

test("the requested change only is chosen by default, and redoing the step is a deliberate choice", () => {
  const onChange = vi.fn();
  render(<RerunScopeChoice value="amend" onChange={onChange} stepTitle="Modulith design" />);
  expect(screen.getByRole("radio", { name: /Only the requested change/ })).toBeChecked();
  expect(screen.getByText(/contracts and code, and the steps after it up to this gate, stay as they are/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: /Redo the step from scratch/ }));
  expect(onChange).toHaveBeenCalledWith("redo");
});
