import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Settings } from "../lib/api";
import { Field, inputClass as input } from "../components/Field";

export function SettingsPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<Settings | null>(null);
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const save = useMutation({
    mutationFn: (values: Settings) => api.updateSettings(values),
    onSuccess: (saved) => queryClient.setQueryData(["settings"], saved),
  });

  if (!form) return <p className="text-subtle">Loading…</p>;
  const set = (key: string, value: unknown) => setForm({ ...form, [key]: value });

  return (
    <form
      className="max-w-2xl space-y-8"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(form);
      }}
    >
      <h1 className="text-2xl font-semibold">Settings</h1>

      <section className="space-y-4">
        <h2 className="font-semibold">Source</h2>
        <Field label="Monolith repository" hint="Git URL or local path. Each run clones it into its own workspace. Leave empty for a blank workspace.">
          <input className={input} value={form.monolith_repo as string} onChange={(e) => set("monolith_repo", e.target.value)} />
        </Field>
        <Field label="Oracle test connection" hint="Read-only JDBC URL used for schema metadata and integration tests.">
          <input className={input} value={form.oracle_test_url as string} onChange={(e) => set("oracle_test_url", e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="JDK 8 home" hint="Used by agents set to the legacy JDK">
            <input className={input} value={form.jdk_legacy_home as string} onChange={(e) => set("jdk_legacy_home", e.target.value)} />
          </Field>
          <Field label="JDK 25 home" hint="Used by agents set to the modern JDK">
            <input className={input} value={form.jdk_modern_home as string} onChange={(e) => set("jdk_modern_home", e.target.value)} />
          </Field>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="font-semibold">Agents</h2>
        <p className="text-sm text-subtle">
          Each stage's agent (mode, model, instructions, tools) is configured on the{" "}
          <Link to="/agents" className="text-accent hover:underline">
            Agents
          </Link>{" "}
          page.
        </p>
        <Field label="Self-check attempts" hint="How many times an agent is sent back when a required document is missing.">
          <input type="number" min={1} max={10} className={`${input} max-w-32`} value={form.max_attempts as number} onChange={(e) => set("max_attempts", Number(e.target.value))} />
        </Field>
      </section>

      <div className="flex items-center gap-3">
        <button type="submit" disabled={save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Save settings
        </button>
        {save.isSuccess && <span className="text-sm text-ok">Saved</span>}
        {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
      </div>
    </form>
  );
}
