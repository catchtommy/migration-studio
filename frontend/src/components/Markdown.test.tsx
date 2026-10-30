import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { Markdown } from "./Markdown";

const renderDiagram = vi.fn(async (_id: string, code: string) => {
  if (code.includes("oops")) throw new Error("Parse error on line 2");
  return { svg: `<svg data-testid="diagram"><text>${code.split("\n")[1]?.trim()}</text></svg>` };
});
vi.mock("mermaid", () => ({ default: { initialize: vi.fn(), render: (id: string, code: string) => renderDiagram(id, code) } }));

describe("Markdown", () => {
  it("draws ```mermaid blocks as diagrams and leaves other code blocks as code", async () => {
    render(<Markdown>{"# Flow\n\n```mermaid\ngraph TD\n  A-->B\n```\n\n```java\nclass A {}\n```"}</Markdown>);
    expect(await screen.findByTestId("diagram")).toHaveTextContent("A-->B");
    expect(renderDiagram).toHaveBeenCalledWith(expect.any(String), "graph TD\n  A-->B");
    expect(screen.getByText("class A {}")).toBeInTheDocument();
  });

  it("shows the source with the error when a diagram can't be drawn", async () => {
    render(<Markdown>{"```mermaid\ngraph TD\n  oops\n```"}</Markdown>);
    expect(await screen.findByText(/couldn't be drawn: Parse error on line 2/)).toBeInTheDocument();
    expect(screen.getByText(/oops/, { selector: "code" })).toBeInTheDocument();
  });
});
