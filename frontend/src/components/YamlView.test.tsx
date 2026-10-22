import { fireEvent, render, screen, within } from "@testing-library/react";
import { humanise, parseYaml } from "./YamlView";
import { SpecView } from "./SpecView";

// Shaped like the studio's own files (backend/app/agents/defaults.py: DOMAIN_DEPENDENCIES_SCHEMA, DOMAIN_DESIGN_SCHEMA).
const DEPENDENCIES = `# domain-dependencies.yaml
feature: create-loan
domain: loan
decision:
  scope: cross-domain
  reason: Reads the customer's KYC status
dependencies:
  - domain: customer
    relation: calls
    direction: outbound
    strength: hard
    touchpoints:
      - kind: service
        ref: com.acme.customer.CustomerService#getKycStatus
        owner: customer
      - kind: table
        ref: CUSTOMER_KYC
        owner: customer
considered_unrelated: [payments, reports]
shared_code: []
`;

const DESIGN = `domain: loan
owned_tables: [LOAN, LOAN_SCHEDULE]
schema_changes: []
exceptions: []
`;

const show = (content: string) => render(<SpecView content={content} yaml={<pre>raw</pre>} />);

test("keys read as words", () => {
  expect(humanise("owned_tables")).toBe("Owned tables");
  expect(humanise("considered-unrelated")).toBe("Considered unrelated");
});

test("a dependencies file reads as a summary, a section per key and tables for its records", () => {
  show(DEPENDENCIES);
  expect(screen.getByRole("tab", { name: "View" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("create-loan")).toBeInTheDocument(); // the summary of top-level values
  expect(screen.getByRole("heading", { name: /Dependencies/ })).toBeInTheDocument();
  const tables = screen.getAllByRole("table");
  expect(tables).toHaveLength(2); // the dependencies, and their touchpoints inside
  expect(within(tables[0]).getByText("Touchpoints")).toBeInTheDocument();
  expect(within(tables[1]).getByText("com.acme.customer.CustomerService#getKycStatus").tagName).toBe("CODE");
  expect(screen.getByText("payments")).toBeInTheDocument(); // a list of values as chips
});

test("lists that must stay empty read as none", () => {
  show(DESIGN);
  expect(screen.getAllByText("none")).toHaveLength(2);
  expect(screen.getByText("LOAN_SCHEDULE")).toBeInTheDocument();
});

test("the source is a tab away, with its comments", () => {
  show(DEPENDENCIES);
  fireEvent.click(screen.getByRole("tab", { name: "YAML" }));
  expect(screen.getByText("# domain-dependencies.yaml")).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
});

test("invalid YAML says so and shows the text", () => {
  expect(parseYaml("decision: [")).toHaveProperty("error");
  show("decision: [\n");
  expect(screen.getByText(/Not valid YAML/)).toBeInTheDocument();
});

test("text that isn't a YAML document, and API contracts, keep their own views", () => {
  show("Just a line of text");
  expect(screen.getByText("raw")).toBeInTheDocument();
  expect(parseYaml("openapi: 3.1.0\ninfo: {title: x}\n")).not.toBeNull();
  show("openapi: 3.1.0\ninfo:\n  title: Loans\n");
  expect(screen.getByRole("tab", { name: "Swagger UI" })).toBeInTheDocument();
});
