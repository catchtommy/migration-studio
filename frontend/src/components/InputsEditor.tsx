import type { AgentInputs } from "../lib/api";
import { Field, inputClass } from "./Field";

export const EMPTY_INPUTS: AgentInputs = { paths: [], notes: "" };

/** Where an agent should look (one path, glob, class or method per line) and free-form notes. */
export function InputsEditor({ value, onChange, pathsLabel = "Where to look", notesLabel = "Notes", pathsHint, notesHint }: {
  value: AgentInputs;
  onChange: (value: AgentInputs) => void;
  pathsLabel?: string;
  notesLabel?: string;
  pathsHint?: string;
  notesHint?: string;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label={pathsLabel} hint={pathsHint ?? "One per line: directories, globs, classes or methods."}>
        <textarea
          rows={4}
          className={`${inputClass} font-mono text-xs`}
          value={value.paths.join("\n")}
          onChange={(e) => onChange({ ...value, paths: e.target.value.split("\n") })}
          placeholder={"src/main/java/com/acme/customer/**\nCustomerController.create\nsqlmap/Customer.xml"}
        />
      </Field>
      <Field label={notesLabel} hint={notesHint}>
        <textarea rows={4} className={inputClass} value={value.notes} onChange={(e) => onChange({ ...value, notes: e.target.value })} />
      </Field>
    </div>
  );
}
