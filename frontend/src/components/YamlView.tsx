import { load } from "js-yaml";
import type { ReactNode } from "react";

/** The studio's YAML documents (dependencies, contracts, decisions, declarations, ...) laid out to be read: sections
 * per top-level key, tables for lists of records, chips for lists of values. Built from the shape of the data, so it
 * fits every such file. OpenAPI and AsyncAPI documents have their own viewers (SpecView). */

type Value = unknown;
type Rec = Record<string, Value>;  // a YAML mapping

const isMap = (v: Value): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v) && !(v instanceof Date);
const isScalar = (v: Value) => v === null || ["string", "number", "boolean"].includes(typeof v) || v instanceof Date;

/** The parsed document when it is a mapping or a list (a YAML document worth laying out), else null. */
export function parseYaml(text: string): { value: Rec | Value[] } | { error: string } | null {
  try {
    const value = load(text);
    return isMap(value) || Array.isArray(value) ? { value } : null;
  } catch (e) {
    return { error: (e as Error).message.split("\n")[0] };
  }
}

export function humanise(key: string): string {
  const words = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const CODE_LIKE = /(#|\/|\.java$|\.xml$|\.ya?ml$|^[a-z][\w-]*(\.[\w-]+){2,}$|^[\w.-]+:[\w.-]+$)/;

function Scalar({ value }: { value: Value }) {
  if (value === null || value === undefined || value === "") return <span className="text-subtle">—</span>;
  if (typeof value === "boolean")
    return (
      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${value ? "bg-ok/15 text-ok" : "bg-muted text-subtle"}`}>{value ? "yes" : "no"}</span>
    );
  const text = value instanceof Date ? value.toISOString().slice(0, 10) : String(value);
  if (typeof value === "string" && !text.includes(" ") && CODE_LIKE.test(text))
    return <code className="break-all rounded bg-muted px-1 py-0.5 font-mono text-xs">{text}</code>;
  return <span className="whitespace-pre-wrap break-words">{text}</span>;
}

const None = () => <span className="text-xs italic text-subtle">none</span>;

function Chips({ items }: { items: Value[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((item, i) => (
        <span key={i} className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs">
          <Scalar value={item} />
        </span>
      ))}
    </span>
  );
}

/** Lists of records with mostly the same keys, and not too many of them, read best as a table. */
function tableColumns(items: Value[]): string[] | null {
  if (!items.length || !items.every(isMap)) return null;
  const keys: string[] = [];
  for (const item of items as Rec[]) for (const k of Object.keys(item)) if (!keys.includes(k)) keys.push(k);
  const shared = keys.filter((k) => (items as Rec[]).filter((item) => k in item).length >= items.length / 2).length;
  return keys.length <= 6 && shared >= keys.length / 2 ? keys : null;
}

function Table({ items, columns, depth }: { items: Rec[]; columns: string[]; depth: number }) {
  return (
    <div className="max-w-full overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="bg-muted text-xs text-subtle">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-1.5 font-medium">
                {humanise(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={i} className="border-t border-border align-top">
              {columns.map((c) => (
                <td key={c} className="px-3 py-1.5">
                  {c in item ? <Node value={item[c]} depth={depth + 1} /> : <span className="text-subtle">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fields({ map, depth }: { map: Rec; depth: number }) {
  const entries = Object.entries(map);
  if (!entries.length) return <None />;
  return (
    <dl className={`grid gap-x-3 gap-y-1 text-sm ${depth > 1 ? "grid-cols-[max-content_1fr]" : "sm:grid-cols-[minmax(8rem,max-content)_1fr]"}`}>
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-xs font-medium text-subtle sm:pt-0.5">{humanise(k)}</dt>
          <dd className="min-w-0">
            <Node value={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function List({ items, depth }: { items: Value[]; depth: number }) {
  if (!items.length) return <None />;
  if (items.every(isScalar)) return <Chips items={items} />;
  const columns = tableColumns(items);
  if (columns) return <Table items={items as Rec[]} columns={columns} depth={depth} />;
  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="rounded-md border border-border bg-surface p-3">
          <Node value={item} depth={depth + 1} />
        </div>
      ))}
    </div>
  );
}

function Node({ value, depth }: { value: Value; depth: number }) {
  if (Array.isArray(value)) return <List items={value} depth={depth} />;
  if (isMap(value)) return <Fields map={value} depth={depth} />;
  return <Scalar value={value} />;
}

/** The readable view of a parsed document: a summary of its top-level values, then a section per list or mapping. */
export function YamlView({ value }: { value: Rec | Value[] }) {
  if (Array.isArray(value))
    return (
      <div className="p-4">
        <List items={value} depth={0} />
      </div>
    );
  const entries = Object.entries(value);
  const summary = entries.filter(([, v]) => isScalar(v));
  const sections = entries.filter(([, v]) => !isScalar(v));
  return (
    <div className="space-y-5 p-4">
      {summary.length > 0 && (
        <dl className="grid gap-x-4 gap-y-1 rounded-md bg-muted/60 p-3 text-sm sm:grid-cols-[minmax(8rem,max-content)_1fr]">
          {summary.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-xs font-medium text-subtle sm:pt-0.5">{humanise(k)}</dt>
              <dd className="min-w-0">
                <Scalar value={v} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {sections.map(([k, v]) => (
        <section key={k} className="space-y-2">
          <h3 className="text-sm font-semibold">
            {humanise(k)}
            {Array.isArray(v) && v.length > 0 && <span className="ml-2 text-xs font-normal text-subtle">{v.length}</span>}
          </h3>
          <Node value={v} depth={1} />
        </section>
      ))}
    </div>
  );
}

/** YAML source with light highlighting: comments, keys, strings, numbers and booleans. */
export function YamlSource({ text }: { text: string }) {
  return (
    <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5">
      {text.split("\n").map((line, i) => (
        <span key={i}>
          {highlight(line)}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function highlight(line: string): ReactNode[] {
  const comment = line.match(/^(\s*)#.*$/);
  if (comment) return [<span key="c" className="text-subtle">{line}</span>];
  const m = line.match(/^(\s*-?\s*)([^\s:#][^:#]*?)(:)(\s|$)(.*)$/);
  if (!m) return valueParts(line, "v");
  const [, lead, key, colon, space, rest] = m;
  return [
    <span key="l">{lead}</span>,
    <span key="k" className="text-accent">{key}</span>,
    <span key="p" className="text-subtle">{colon}</span>,
    <span key="s">{space}</span>,
    ...valueParts(rest, "r"),
  ];
}

function valueParts(text: string, prefix: string): ReactNode[] {
  const hash = text.search(/\s#/);
  const value = hash >= 0 ? text.slice(0, hash) : text;
  const note = hash >= 0 ? text.slice(hash) : "";
  const trimmed = value.trim();
  const cls = /^(['"]).*\1$/.test(trimmed)
    ? "text-ok"
    : /^-?\d+(\.\d+)?$/.test(trimmed) || /^(true|false|null|~)$/.test(trimmed)
      ? "text-warn"
      : "";
  return [
    <span key={`${prefix}v`} className={cls}>
      {value}
    </span>,
    ...(note ? [<span key={`${prefix}n`} className="text-subtle">{note}</span>] : []),
  ];
}
