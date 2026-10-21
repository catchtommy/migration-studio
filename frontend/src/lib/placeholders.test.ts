import { describe, expect, it } from "vitest";
import { expandPlaceholders, placeholders } from "./api";

describe("shared-text placeholders", () => {
  const instructions = "Intro {{legacy}}\n\n{{ rest_rules }}\nAgain {{legacy}} and {{nope}}; not {{Upper}} or {single}.";

  it("lists each placeholder once, in order of first use, as the backend reads them", () => {
    expect(placeholders(instructions)).toEqual(["legacy", "rest_rules", "nope"]);
    expect(placeholders("No placeholders here.")).toEqual([]);
  });

  it("fills in the known ones everywhere and leaves unknown ones as they are", () => {
    expect(expandPlaceholders(instructions, { legacy: "Java 8", rest_rules: "POST → 201" })).toBe(
      "Intro Java 8\n\nPOST → 201\nAgain Java 8 and {{nope}}; not {{Upper}} or {single}.",
    );
  });

  it("does not read $ patterns in a shared text as replacement syntax", () => {
    expect(expandPlaceholders("{{a}}", { a: "costs $1 and $& stays" })).toBe("costs $1 and $& stays");
  });
});
