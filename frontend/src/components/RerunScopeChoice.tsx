import type { RerunScope } from "../lib/api";

/** How a rejected step re-runs. Only the requested change is the default: the step's other documents, contracts and
 * code, and the steps after it up to the gate, stay as they are. */
export function RerunScopeChoice({ value, onChange, stepTitle }: { value: RerunScope; onChange: (scope: RerunScope) => void; stepTitle: string }) {
  const options: { scope: RerunScope; title: string; text: string }[] = [
    {
      scope: "amend",
      title: "Only the requested change",
      text: `${stepTitle} changes just what your comments ask, in place. Its other documents, contracts and code, and the steps after it up to this gate, stay as they are.`,
    },
    {
      scope: "redo",
      title: "Redo the step from scratch",
      text: `${stepTitle} starts over from where it began and writes its documents again, and the steps after it up to this gate run again too.`,
    },
  ];
  return (
    <fieldset className="grid gap-2 sm:grid-cols-2">
      <legend className="mb-1 text-xs font-medium text-subtle">When rejected, how {stepTitle} re-runs</legend>
      {options.map((o) => (
        <label
          key={o.scope}
          className={`flex cursor-pointer gap-2 rounded-md border p-3 text-sm ${value === o.scope ? "border-accent bg-surface ring-1 ring-accent" : "border-border bg-surface hover:border-accent/50"}`}
        >
          <input type="radio" name="rerun-scope" className="mt-1" checked={value === o.scope} onChange={() => onChange(o.scope)} />
          <span>
            <span className="block font-medium">{o.title}</span>
            <span className="block text-xs text-subtle">{o.text}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
