import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import type { ArtifactSummary } from "../lib/api";
import { chosen, DocumentPicker, shortPath, type DocumentChoice } from "./DocumentPicker";

const doc = (id: number, path: string, kind = "yaml"): ArtifactSummary => ({
  id, stage: "integration_design", attempt: 1, name: path.split("/").pop()!, path, kind, created_at: "", updated_at: "",
});
const DOCS = [
  doc(1, ".migration/loan/approve/integration-design.md", "markdown"),
  doc(2, ".migration/loan/approve/integration-decisions.yaml"),
  doc(3, ".migration/loan/approve/contracts/loan.approved.yaml"),
];

function Harness({ onChoice }: { onChoice?: (c: DocumentChoice) => void }) {
  const [value, setValue] = useState<DocumentChoice>("all");
  return (
    <>
      <DocumentPicker docs={DOCS} value={value} onChange={(c) => (setValue(c), onChoice?.(c))} />
      <ul aria-label="shown">{chosen(DOCS, value).map((d) => <li key={d.id}>{d.path}</li>)}</ul>
    </>
  );
}

test("shortPath drops the run's documents folder", () => {
  expect(shortPath(".migration/loan/approve/contracts/a.yaml")).toBe("contracts/a.yaml");
  expect(shortPath("README.md")).toBe("README.md");
});

test("shows all documents until one is picked", () => {
  render(<Harness />);
  expect(screen.getByRole("button", { name: "Choose a document" })).toHaveTextContent("All documents (3)");
  expect(screen.getByLabelText("shown").children).toHaveLength(3);
});

test("search filters and a click picks one document", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Choose a document" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Search documents" }), { target: { value: "contracts" } });
  const options = screen.getAllByRole("option");
  expect(options).toHaveLength(1);
  fireEvent.click(options[0]);
  expect(screen.getByLabelText("shown").children).toHaveLength(1);
  expect(screen.getByLabelText("shown")).toHaveTextContent("contracts/loan.approved.yaml");
  expect(screen.queryByRole("listbox")).toBeNull();
});

test("keyboard: arrows move, Enter picks, no match says so", () => {
  const picked: DocumentChoice[] = [];
  render(<Harness onChoice={(c) => picked.push(c)} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose a document" }));
  const search = screen.getByRole("combobox", { name: "Search documents" });
  fireEvent.keyDown(search, { key: "ArrowDown" }); // past "All documents" to the first document
  fireEvent.keyDown(search, { key: "Enter" });
  expect(picked).toEqual([1]);
  fireEvent.click(screen.getByRole("button", { name: "Choose a document" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Search documents" }), { target: { value: "nothing-like-this" } });
  expect(screen.getByText(/No document matches/)).toBeInTheDocument();
});
