import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import { ArtifactViewer } from "./ArtifactViewer";

const BROKEN = "openapi: 3.1.0\nservers:\n  - url: http://localhost:8080\n paths: [\n";
const artifact = { id: 1, stage: "modulith_design", attempt: 1, name: "openapi.yaml", path: ".migration/loan/x/openapi.yaml", kind: "openapi", content: BROKEN };
const updateArtifact = vi.fn(async (_id: number, content: string) => ({ ...artifact, content }));
vi.mock("../lib/api", () => ({
  api: { artifact: async () => artifact, updateArtifact: (id: number, content: string) => updateArtifact(id, content) },
  saveFile: () => {},
}));
// A viewer that fails on the document, as Swagger UI can on a malformed spec.
vi.mock("./SpecView", () => ({
  SpecView: () => {
    throw new Error("Unexpected token in the spec");
  },
}));

const show = (editable: boolean) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ArtifactViewer artifactId={1} editable={editable} />
    </QueryClientProvider>,
  );

test("a document its viewer can't render shows as text, and can still be edited and fixed", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {}); // React reports the caught error
  show(true);
  expect(await screen.findByText(/can't be shown formatted \(Unexpected token in the spec\)/)).toBeInTheDocument();
  expect(screen.getByText(/servers:/)).toBeInTheDocument(); // the text itself is there

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const box = screen.getByRole("textbox", { name: "Edit openapi.yaml" });
  expect(screen.getByRole("status")).toHaveTextContent("Not valid YAML yet");
  const fixed = "openapi: 3.1.0\nservers:\n  - url: https://loans.internal/api\npaths: {}\n"; // the URL edited too
  fireEvent.change(box, { target: { value: fixed } });
  expect(screen.getByRole("status")).toHaveTextContent("Valid YAML");
  fireEvent.click(screen.getByRole("button", { name: /Save/ }));
  await waitFor(() => expect(updateArtifact).toHaveBeenCalledWith(1, fixed));
});

test("without the right to edit, the text still shows and no Edit is offered", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  show(false);
  expect(await screen.findByText(/can't be shown formatted/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});
