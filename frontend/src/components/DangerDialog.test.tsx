import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { DangerDialog } from "./DangerDialog";

test("the warning shows each part in its colour and unlocks only with the exact text typed", () => {
  const onConfirm = vi.fn();
  render(
    <table>
      <tbody>
        <tr>
          <td className="whitespace-nowrap">
            <DangerDialog
              title="Delete the project Loans"
              sections={[
                { tone: "bad", title: "Deleted for good", items: ["12 runs"] },
                { tone: "warn", title: "Stopped first", text: "1 run is working" },
                { tone: "ok", title: "Kept", items: ["Other projects"] },
                { tone: "neutral", title: "Not undone in git", text: "Pushed code stays" },
              ]}
              confirmText="loans"
              actionLabel="Delete Loans and all its data"
              pendingLabel="Deleting…"
              pending={false}
              onConfirm={onConfirm}
              onClose={() => {}}
            />
          </td>
        </tr>
      </tbody>
    </table>,
  );
  const dialog = screen.getByRole("dialog");
  expect(dialog.parentElement).toBe(document.body); // out of the no-wrap cell it was opened from
  expect(screen.getByText("Deleted for good")).toHaveClass("text-bad");
  expect(screen.getByText("Stopped first")).toHaveClass("text-warn");
  expect(screen.getByText("Kept")).toHaveClass("text-ok");

  const go = screen.getByRole("button", { name: "Delete Loans and all its data" });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Loans" } }); // case matters
  expect(go).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "loans" } });
  fireEvent.click(go);
  expect(onConfirm).toHaveBeenCalledOnce();
});

test("when it can't be done yet, the reason shows and nothing can be confirmed", () => {
  const onClose = vi.fn();
  render(
    <DangerDialog
      title="Delete the Lean workflow"
      blocked="1 run(s) still use it: loan/approve (awaiting approval). Delete those runs first"
      sections={[{ tone: "bad", title: "Deleted for good", items: ["The workflow"] }]}
      confirmText="lean"
      actionLabel="Delete the workflow"
      pendingLabel="Deleting…"
      pending={false}
      onConfirm={() => {}}
      onClose={onClose}
    />,
  );
  expect(screen.getByText("Can't be deleted yet")).toHaveClass("text-bad");
  expect(screen.getByText(/loan\/approve \(awaiting approval\)/)).toBeInTheDocument();
  expect(screen.queryByText("Deleted for good")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete the workflow" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(onClose).toHaveBeenCalled();
});
