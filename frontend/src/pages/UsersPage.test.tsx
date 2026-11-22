import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, setCurrentProject, type Role, type User } from "../lib/api";
import { UsersPage } from "./UsersPage";

const roles: Role[] = [
  { key: "admin", name: "Admin", description: "Everything", permissions: ["users.manage"], locked: true, users: 1 },
  { key: "developer", name: "Developer", description: "Runs migrations", permissions: ["runs.start"], locked: false, users: 0 },
];
const ada: User = { id: 1, username: "ada", display_name: "Ada Lovelace", active: true, roles: ["admin"], created_at: "2026-11-01T10:00:00Z", last_login_at: null, projects: [] };

function show() {
  setCurrentProject("default");
  vi.spyOn(api, "roles").mockResolvedValue(roles);
  vi.spyOn(api, "users").mockResolvedValue([ada]);
  vi.spyOn(api, "me").mockResolvedValue({ user: { id: 1, username: "ada", display_name: "Ada Lovelace", roles: ["admin"], permissions: ["users.manage"] }, needs_bootstrap: false } as never);
  vi.spyOn(api, "projects").mockResolvedValue([{ key: "default", name: "Default project", permissions: [] }] as never);
  vi.spyOn(api, "permissions").mockResolvedValue([{ key: "runs.start", label: "Run migrations", description: "" }] as never);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <UsersPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("the users are listed", async () => {
  show();
  expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
});

test("a new user is added with access to the current project", async () => {
  const create = vi.spyOn(api, "createUser").mockResolvedValue({ ...ada, id: 7, username: "grace" });
  const member = vi.spyOn(api, "setMember").mockResolvedValue([] as never);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Add user" }));
  const add = screen.getAllByRole("button", { name: "Add user" }).at(-1)!;
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Grace Hopper" } });
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "Grace" } });
  fireEvent.change(screen.getByLabelText(/Initial password/), { target: { value: "short" } });
  expect(add).toBeDisabled(); // under 8 characters
  fireEvent.change(screen.getByLabelText(/Initial password/), { target: { value: "long-enough" } });
  await waitFor(() => expect(screen.getByLabelText(/Access to Default project/)).toBeInTheDocument());
  fireEvent.click(add);
  await waitFor(() => expect(member).toHaveBeenCalledWith("default", 7, "developer"));
  expect(create).toHaveBeenCalledWith({ display_name: "Grace Hopper", username: "grace", password: "long-enough", roles: [] }); // lower case
});

test("an error adding a user is shown", async () => {
  vi.spyOn(api, "createUser").mockRejectedValue(new Error("There is already a user grace"));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Add user" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Grace" } });
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "grace" } });
  fireEvent.change(screen.getByLabelText(/Initial password/), { target: { value: "long-enough" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Add user" }).at(-1)!);
  expect(await screen.findByText("There is already a user grace")).toBeInTheDocument();
});

test("the roles tab lists the roles with their permissions", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Roles & permissions" }));
  expect(await screen.findByText("Developer")).toBeInTheDocument();
  expect(await screen.findByText("Run migrations")).toBeInTheDocument();
});
