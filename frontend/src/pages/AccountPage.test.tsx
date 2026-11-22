import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, setCurrentProject } from "../lib/api";
import { AccountPage } from "./AccountPage";

const account = {
  name: "", email: "", default_name: "Ada Lovelace",
  credentials: [{ id: 3, host: "github.com", username: "ada", updated_at: "2026-11-20T10:00:00Z" }],
};

function show() {
  setCurrentProject("default");
  vi.spyOn(api, "accountGit").mockResolvedValue(account as never);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <AccountPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("the commit author is saved", async () => {
  const save = vi.spyOn(api, "setGitIdentity").mockResolvedValue({ ...account, name: "Ada", email: "ada@example.com" } as never);
  show();
  fireEvent.change(await screen.findByLabelText(/^Name/), { target: { value: "Ada" } });
  fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: "ada@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Saved.")).toBeInTheDocument();
  expect(save).toHaveBeenCalledWith({ name: "Ada", email: "ada@example.com" });
});

test("a token is saved for a host, and saved tokens are listed without the token", async () => {
  const add = vi.spyOn(api, "setGitCredential").mockResolvedValue(account as never);
  show();
  expect(await screen.findByText("github.com")).toBeInTheDocument();
  const button = screen.getByRole("button", { name: "Save token" });
  fireEvent.change(screen.getByLabelText(/^Host/), { target: { value: "corp.ghe.com" } });
  fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: "ada" } });
  expect(button).toBeDisabled(); // no token yet
  fireEvent.change(screen.getByLabelText(/^Token/), { target: { value: "ghp_secret" } });
  fireEvent.click(button);
  await waitFor(() => expect(add).toHaveBeenCalledWith({ host: "corp.ghe.com", username: "ada", token: "ghp_secret" }));
});

test("access to the project's repository is tested and the answer shown", async () => {
  vi.spyOn(api, "testGit").mockResolvedValue({ ok: false, repository: "https://git.example.com/m.git", with_token: true, error: "Authentication failed" });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Test access to this project's repository" }));
  expect(await screen.findByText("Authentication failed")).toBeInTheDocument();
});
