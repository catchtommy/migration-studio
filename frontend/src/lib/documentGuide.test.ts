import { documentGuide } from "./documentGuide";

test("documents are described by name, wherever they sit, and contract specs by their folder", () => {
  expect(documentGuide(".migration/client/update-bank/functional-allocation.yaml")?.what).toMatch(/Where each functionality goes/);
  expect(documentGuide("orchestrator-openapi.yaml")?.usage).toMatch(/Orchestrator microservice step implements/);
  expect(documentGuide(".migration/client/x/contracts/fraud.get-open-cases.yaml")?.what).toMatch(/spec of one contract/);
  expect(documentGuide("notes.txt")).toBeUndefined();
});
