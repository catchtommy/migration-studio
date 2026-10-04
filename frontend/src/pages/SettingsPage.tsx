import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Settings } from "../lib/api";
import { CopilotAccount } from "../components/CopilotAccount";
import { Field, inputClass as input } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { SlidersIcon } from "../components/icons";

/** Where Copilot sign-in happens: github.com, or a GitHub Enterprise Cloud host with data residency (*.ghe.com). */
function GitHubHost({ value }: { value: string }) {
  const queryClient = useQueryClient();
  const [host, setHost] = useState(value);
  useEffect(() => setHost(value), [value]);
  const save = useMutation({
    mutationFn: () => api.updateSettings({ github_host: host }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      queryClient.invalidateQueries({ queryKey: ["copilotStatus"] });
    },
  });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="min-w-72 flex-1">
        <Field label="GitHub host" hint="https://github.com, or your GitHub Enterprise Cloud address such as https://your-company.ghe.com">
          <input className={input} value={host} onChange={(e) => setHost(e.target.value)} />
        </Field>
      </div>
      <button
        type="submit"
        disabled={!host.trim() || host === value || save.isPending}
        className="mb-[1.4rem] rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50 disabled:opacity-50"
      >
        Save host
      </button>
      {save.error && <p className="w-full text-sm text-bad">{(save.error as Error).message}</p>}
    </form>
  );
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<Settings | null>(null);
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const save = useMutation({
    mutationFn: (values: Settings) => api.updateSettings(values),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      queryClient.invalidateQueries({ queryKey: ["copilotStatus"] }); // network settings restart the runtime
      queryClient.invalidateQueries({ queryKey: ["agentCatalog"] });
    },
  });
  const setProvider = useMutation({
    mutationFn: (provider: string) => api.updateSettings({ ai_provider: provider }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      queryClient.invalidateQueries({ queryKey: ["agentCatalog"] });
      setForm((f) => f && { ...f, ai_provider: saved.ai_provider });
    },
  });

  if (!form) return <p className="text-subtle">Loading…</p>;
  const set = (key: string, value: unknown) => setForm({ ...form, [key]: value });

  return (
    <div className="max-w-2xl space-y-8">
      <PageTitle icon={<SlidersIcon />} title="Settings" subtitle="AI provider, source repository and network." tone="from-slate-500 to-slate-700" />

      <section className="space-y-4">
        <h2 className="font-semibold">AI provider</h2>
        <Field label="Agents set to AI run with" hint="Changes apply to stages that start afterwards.">
          <select className={`${input} max-w-xs`} value={form.ai_provider as string} disabled={setProvider.isPending} onChange={(e) => setProvider.mutate(e.target.value)}>
            <option value="copilot">GitHub Copilot (your account)</option>
            <option value="claude">Claude API</option>
          </select>
        </Field>
        {form.ai_provider === "copilot" ? (
          <>
            <GitHubHost value={data!.github_host as string} />
            <CopilotAccount githubHost={data!.github_host as string} />
          </>
        ) : (
          <p className="text-sm text-subtle">The Claude API uses the ANTHROPIC_API_KEY (or other Anthropic credentials) in the backend's environment.</p>
        )}
      </section>

      <form
        className="space-y-8"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
      >
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

        <section className="space-y-4">
          <h2 className="font-semibold">Corporate network</h2>
          <p className="text-sm text-subtle">
            For the GitHub Copilot connection. HTTPS from this app already trusts the operating system's certificates, which usually include your company's root
            certificate. Saving changes here restarts the Copilot connection.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="HTTPS proxy" hint="e.g. http://proxy.mycompany.com:8080; leave empty for a direct connection">
              <input className={input} value={form.https_proxy as string} onChange={(e) => set("https_proxy", e.target.value)} />
            </Field>
            <Field label="No proxy for" hint="Comma-separated hosts, e.g. .mycompany.com,localhost">
              <input className={input} value={form.no_proxy as string} onChange={(e) => set("no_proxy", e.target.value)} />
            </Field>
          </div>
          <Field label="Extra CA certificates" hint="Path to a PEM file with your company's root certificate(s), if the Copilot connection reports certificate errors.">
            <input className={input} value={form.extra_ca_file as string} onChange={(e) => set("extra_ca_file", e.target.value)} />
          </Field>
          <Field label="Copilot CLI path" hint="Path to an IT-provided copilot executable, if the Copilot runtime cannot be downloaded on your network.">
            <input className={input} value={form.copilot_cli_path as string} onChange={(e) => set("copilot_cli_path", e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!form.copilot_disable_websockets} onChange={(e) => set("copilot_disable_websockets", e.target.checked)} />
            Don't use WebSockets (for proxies that block them)
          </label>
        </section>

        <div className="flex items-center gap-3">
          <button type="submit" disabled={save.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Save settings
          </button>
          {save.isSuccess && <span className="text-sm text-ok">Saved</span>}
          {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
        </div>
      </form>
    </div>
  );
}
