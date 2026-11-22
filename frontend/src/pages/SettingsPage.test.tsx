import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api } from "../lib/api";
import { SettingsPage } from "./SettingsPage";

vi.mock("../lib/hooks", async (original) => ({ ...(await original<typeof import("../lib/hooks")>()), useCan: () => true }));

const settings = {
  ai_provider: "claude", github_host: "https://github.com", https_proxy: "", no_proxy: "", extra_ca_file: "", copilot_cli_path: "", copilot_disable_websockets: false,
};

function show() {
  vi.spyOn(api, "settings").mockResolvedValue(settings as never);
  vi.spyOn(api, "databaseStatus").mockResolvedValue({ configured: true, source: "env", dialect: "oracle", config: null, version: "19.0.0" } as never);
  vi.spyOn(api, "dataFolder").mockResolvedValue({ path: "/srv/studio/data", source: "env" } as never);
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <SettingsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

test("the network settings are saved together", async () => {
  const update = vi.spyOn(api, "updateSettings").mockImplementation(async (v) => ({ ...settings, ...v }) as never);
  show();
  fireEvent.change(await screen.findByLabelText(/HTTPS proxy/), { target: { value: "http://proxy.example.com:8080" } });
  fireEvent.change(screen.getByLabelText(/No proxy for/), { target: { value: ".example.com" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /Don't use WebSockets/ }));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Saved")).toBeInTheDocument();
  expect(update.mock.calls[0][0]).toMatchObject({ https_proxy: "http://proxy.example.com:8080", no_proxy: ".example.com", copilot_disable_websockets: true });
});

test("a refused setting says why", async () => {
  vi.spyOn(api, "updateSettings").mockRejectedValue(new Error("Unknown global settings: x"));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Unknown global settings: x")).toBeInTheDocument();
});

test("switching the AI provider saves it at once", async () => {
  const update = vi.spyOn(api, "updateSettings").mockImplementation(async (v) => ({ ...settings, ...v }) as never);
  vi.spyOn(api, "copilotStatus").mockResolvedValue({ available: true, signed_in: false } as never);
  show();
  expect(await screen.findByText(/needs ANTHROPIC_API_KEY/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Agents set to AI run with/), { target: { value: "copilot" } });
  await waitFor(() => expect(update).toHaveBeenCalledWith({ ai_provider: "copilot" }));
  expect(await screen.findByLabelText(/GitHub host/)).toBeInTheDocument();
});

test("the database in use is shown", async () => {
  show();
  expect(await screen.findByText(/19\.0\.0/)).toBeInTheDocument();
});
