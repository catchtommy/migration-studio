import { diffLines } from "diff";

export interface Side {
  n: number; // 1-based line number
  text: string;
}

/** One row of a side-by-side diff: unchanged, removed, added, or changed (a removed line next to an added one). */
export type Row =
  | { kind: "same"; left: Side; right: Side }
  | { kind: "del"; left: Side }
  | { kind: "add"; right: Side }
  | { kind: "mod"; left: Side; right: Side }
  | { kind: "skip"; count: number; from: number }; // unchanged lines folded away (index into the full rows)

const split = (text: string) => {
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop(); // a trailing newline isn't a line
  return lines;
};

/** Side-by-side rows from `before` to `after` (null: the file doesn't exist on that side). */
export function sideBySide(before: string | null, after: string | null): Exclude<Row, { kind: "skip" }>[] {
  const rows: Exclude<Row, { kind: "skip" }>[] = [];
  let l = 1;
  let r = 1;
  const parts = diffLines(before ?? "", after ?? "");
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part.added && !part.removed) {
      for (const text of split(part.value)) rows.push({ kind: "same", left: { n: l++, text }, right: { n: r++, text } });
      continue;
    }
    if (part.removed && parts[i + 1]?.added) {
      const removed = split(part.value);
      const added = split(parts[i + 1].value);
      for (let k = 0; k < Math.max(removed.length, added.length); k++) {
        if (k < removed.length && k < added.length) rows.push({ kind: "mod", left: { n: l++, text: removed[k] }, right: { n: r++, text: added[k] } });
        else if (k < removed.length) rows.push({ kind: "del", left: { n: l++, text: removed[k] } });
        else rows.push({ kind: "add", right: { n: r++, text: added[k] } });
      }
      i++;
      continue;
    }
    for (const text of split(part.value)) {
      if (part.removed) rows.push({ kind: "del", left: { n: l++, text } });
      else rows.push({ kind: "add", right: { n: r++, text } });
    }
  }
  return rows;
}

/** Folds runs of unchanged lines further than `context` lines from a change. */
export function fold(rows: Exclude<Row, { kind: "skip" }>[], context = 3, open: Set<number> = new Set()): Row[] {
  const near = rows.map(() => false);
  rows.forEach((row, i) => {
    if (row.kind !== "same") for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k++) near[k] = true;
  });
  const out: Row[] = [];
  for (let i = 0; i < rows.length; ) {
    if (near[i] || rows[i].kind !== "same") {
      out.push(rows[i++]);
      continue;
    }
    let j = i;
    while (j < rows.length && !near[j] && rows[j].kind === "same") j++;
    if (open.has(i) || j - i < 2) out.push(...rows.slice(i, j));
    else out.push({ kind: "skip", count: j - i, from: i });
    i = j;
  }
  return out;
}

/** A merge result with conflict markers, as plain text and conflicts between "this run" and the base branch. */
export type Segment = { kind: "text"; text: string } | { kind: "conflict"; ours: string; theirs: string };

export function parseConflicts(merged: string): Segment[] {
  const segments: Segment[] = [];
  const lines = merged.split("\n");
  let text: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith("<<<<<<< ")) {
      text.push(lines[i]);
      continue;
    }
    const ours: string[] = [];
    const theirs: string[] = [];
    let j = i + 1;
    while (j < lines.length && !lines[j].startsWith("=======")) ours.push(lines[j++]);
    j++;
    while (j < lines.length && !lines[j].startsWith(">>>>>>> ")) theirs.push(lines[j++]);
    if (j >= lines.length) {
      text.push(...lines.slice(i)); // not a complete conflict: keep it as text
      break;
    }
    if (text.length) segments.push({ kind: "text", text: text.join("\n") });
    text = [];
    segments.push({ kind: "conflict", ours: ours.join("\n"), theirs: theirs.join("\n") });
    i = j;
  }
  if (text.length) segments.push({ kind: "text", text: text.join("\n") });
  return segments;
}

export type Choice = "ours" | "theirs" | "both" | "both-reversed" | null;

/** The file with each conflict replaced by the chosen side(s); unchosen conflicts keep their markers. */
export function resolve(segments: Segment[], choices: Choice[], baseLabel: string): string {
  let c = 0;
  const parts = segments.map((s) => {
    if (s.kind === "text") return s.text;
    const choice = choices[c++];
    const join = (...xs: string[]) => xs.filter((x) => x !== "").join("\n");
    const chosen =
      choice === "ours" ? s.ours : choice === "theirs" ? s.theirs : choice === "both" ? join(s.ours, s.theirs) : choice === "both-reversed" ? join(s.theirs, s.ours) : null;
    if (chosen === null) return `<<<<<<< this run\n${s.ours}\n=======\n${s.theirs}\n>>>>>>> ${baseLabel}`;
    return chosen === "" ? null : chosen; // an empty side leaves no blank line behind
  });
  return parts.filter((p) => p !== null).join("\n");
}

export const hasMarkers = (text: string) => /^(<{7}|={7}|>{7})( |$)/m.test(text);
