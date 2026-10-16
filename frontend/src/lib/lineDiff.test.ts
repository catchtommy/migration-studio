import { fold, hasMarkers, parseConflicts, resolve, sideBySide } from "./lineDiff";

test("side-by-side rows pair changed lines and fold unchanged ones", () => {
  const before = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join("\n") + "\n";
  const after = before.replace("line 10\n", "line ten\n").replace("line 20\n", "line 20\nline 21\n");
  const rows = sideBySide(before, after);
  expect(rows.find((r) => r.kind === "mod")).toMatchObject({ left: { n: 10, text: "line 10" }, right: { n: 10, text: "line ten" } });
  expect(rows.find((r) => r.kind === "add")).toMatchObject({ right: { n: 21, text: "line 21" } });
  const folded = fold(rows);
  expect(folded[0]).toEqual({ kind: "skip", count: 6, from: 0 });
  expect(sideBySide(null, "a\n")).toEqual([{ kind: "add", right: { n: 1, text: "a" } }]);
});

test("conflicts are parsed and resolved per block", () => {
  const merged = "top\n<<<<<<< this run\nours\n=======\ntheirs\n>>>>>>> main\nmiddle\n<<<<<<< this run\n=======\nonly theirs\n>>>>>>> main\nend\n";
  const segments = parseConflicts(merged);
  expect(segments.filter((s) => s.kind === "conflict")).toHaveLength(2);
  expect(resolve(segments, ["both", "ours"], "main")).toBe("top\nours\ntheirs\nmiddle\nend\n");
  const partly = resolve(segments, ["theirs", null], "main");
  expect(hasMarkers(partly)).toBe(true);
  expect(partly.startsWith("top\ntheirs\nmiddle\n<<<<<<< this run")).toBe(true);
  expect(resolve(parseConflicts("no conflicts\n"), [], "main")).toBe("no conflicts\n");
});
