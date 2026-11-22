import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api } from "../lib/api";
import { ProjectsPage } from "./ProjectsPage";

const switched: string[] = [];
vi.mock("../lib/projectContext", async (original) => ({
  ...(await original<typeof import("../lib/projectContext")>()),
  useSwitchProject: () => (key: string) => switched.push(key),
}));
let canCreate = true;
vi.mock("../lib/hooks", async (original) => ({ ...(await original<typeof import("../lib/hooks")>()), useCan: () => canCreate }));

const project = (key: string, member: boolean) => ({ key, name: key.toUpperCase(), description: "", runs: 2, members: 1, my_role: member ? "admin" : null, member, permissions: [] });

function show() {
  vi.spyOn(api, "projects").mockResolvedValue([project("default", true), project("other", false)] as never);
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <ProjectsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  switched.length = 0;
  canCreate = true;
});
afterEach(() => vi.restoreAllMocks());

test("projects are listed, those one belongs to apart from the others, and one opens by clicking it", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: /DEFAULT/ }));
  expect(switched).toEqual(["default"]);
  expect(screen.getByText("Projects you're not in")).toBeInTheDocument();
  expect(screen.getByText("OTHER")).toBeInTheDocument();
});

test("a new project gets its key from its name and opens once created", async () => {
  const create = vi.spyOn(api, "createProject").mockResolvedValue({ key: "loan-servicing" } as never);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "New project" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Loan Servicing" } });
  fireEvent.change(screen.getByLabelText(/Monolith repository/), { target: { value: " https://git.example.com/loans.git " } });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  await waitFor(() => expect(switched).toEqual(["loan-servicing"]));
  expect(create.mock.calls[0][0]).toMatchObject({ key: "loan-servicing", name: "Loan Servicing", settings: { monolith_repo: "https://git.example.com/loans.git" } });
});

test("only those who manage projects can create one", async () => {
  canCreate = false;
  show();
  await screen.findByRole("button", { name: /DEFAULT/ });
  expect(screen.queryByRole("button", { name: "New project" })).toBeNull();
});
