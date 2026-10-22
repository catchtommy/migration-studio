import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { ResetFeature } from "./ResetFeature";

const admin = { value: true };
vi.mock("../lib/hooks", () => ({ useIsAdmin: () => admin.value }));
const resetFeature = vi.fn(async () => ({ deleted: 1 }));
vi.mock("../lib/api", () => ({ api: { resetFeature: (...args: unknown[]) => resetFeature(...(args as [])) } }));

const show = (status = "awaiting_approval") =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ResetFeature domain="loan" feature="create-loan" name="Create loan" status={status} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  admin.value = true;
  resetFeature.mockClear();
});

test("a sharp warning says what is deleted, kept and not undone, and asks for the feature's key", async () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  // At the page's top level, not inside the feature row's no-wrap table cell (where its text overflowed the box).
  expect(screen.getByRole("dialog").parentElement).toBe(document.body);
  expect(screen.getByText("Deleted for good")).toBeInTheDocument();
  expect(screen.getByText("Kept")).toBeInTheDocument();
  expect(screen.getByText("Not undone in git")).toBeInTheDocument();
  expect(screen.queryByText("Stopped first")).toBeNull(); // nothing is working

  const go = screen.getByRole("button", { name: "Delete its runs and reset" });
  expect(go).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "create" } });
  expect(go).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "create-loan" } });
  fireEvent.click(go);
  await waitFor(() => expect(resetFeature).toHaveBeenCalledWith("loan", "create-loan"));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("a working run is said to be stopped first; cancel and Escape change nothing", () => {
  show("running");
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(screen.getByText("Stopped first")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(resetFeature).not.toHaveBeenCalled();
});

test("only admins see it", () => {
  admin.value = false;
  show();
  expect(screen.queryByRole("button", { name: "Reset" })).toBeNull();
});
