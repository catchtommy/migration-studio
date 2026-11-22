import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { api, ApiError } from "../lib/api";
import { CreateAdminPage, SignInPage } from "./SignInPage";

const show = (page: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{page}</QueryClientProvider>);
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

afterEach(() => vi.restoreAllMocks());

test("signing in needs a username and a password, then sends them", async () => {
  const login = vi.spyOn(api, "login").mockResolvedValue({ user: {} } as never);
  show(<SignInPage />);
  const button = screen.getByRole("button", { name: "Sign in" });
  expect(button).toBeDisabled();
  type("Username", "ada");
  expect(button).toBeDisabled();
  type("Password", "secret-pass");
  fireEvent.click(button);
  await waitFor(() => expect(login).toHaveBeenCalledWith("ada", "secret-pass"));
});

test("a wrong password is reported", async () => {
  vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, "Wrong username or password"));
  show(<SignInPage />);
  type("Username", "ada");
  type("Password", "nope-nope");
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("Wrong username or password")).toBeInTheDocument();
});

test("the first admin needs a name, a username and a password typed twice alike", async () => {
  const bootstrap = vi.spyOn(api, "bootstrap").mockResolvedValue({ user: {} } as never);
  show(<CreateAdminPage />);
  const button = screen.getByRole("button", { name: "Create admin and sign in" });
  type("Your name", "Ada Lovelace");
  type(/^Username/, "ada");
  type(/^Password/, "short");
  type("Confirm password", "short");
  expect(button).toBeDisabled(); // under 8 characters
  type(/^Password/, "long-enough");
  type("Confirm password", "long-enougj");
  expect(screen.getByText("The passwords don't match")).toBeInTheDocument();
  expect(button).toBeDisabled();
  type("Confirm password", "long-enough");
  fireEvent.click(button);
  await waitFor(() => expect(bootstrap).toHaveBeenCalledWith({ username: "ada", display_name: "Ada Lovelace", password: "long-enough" }));
});
