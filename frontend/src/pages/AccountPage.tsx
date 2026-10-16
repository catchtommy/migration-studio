import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getCurrentProject, type AccountGit } from "../lib/api";
import { Field, inputClass as input } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { BranchIcon } from "../components/icons";

const primary = "rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50";
const button = "rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50 disabled:opacity-50";

function Identity({ data }: { data: AccountGit }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: data.name, email: data.email });
  useEffect(() => setForm({ name: data.name, email: data.email }), [data]);
  const save = useMutation({ mutationFn: () => api.setGitIdentity(form), onSuccess: (d) => queryClient.setQueryData(["accountGit"], d) });
  return (
    <form
      className="card space-y-4 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div>
        <h2 className="font-semibold">Commit author</h2>
        <p className="text-sm text-subtle">The name and email on the commits you make when you commit a run's changes.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint={`Empty: ${data.default_name}`}>
          <input className={input} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Email" hint="Use the email your git host knows you by">
          <input className={input} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
      </div>
      {save.error && <p className="text-sm text-bad">{(save.error as Error).message}</p>}
      {save.isSuccess && <p className="text-sm text-ok">Saved.</p>}
      <button type="submit" className={primary} disabled={save.isPending}>
        Save
      </button>
    </form>
  );
}

function Credentials({ data }: { data: AccountGit }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ host: "", username: "", token: "" });
  const done = (d: AccountGit) => queryClient.setQueryData(["accountGit"], d);
  const add = useMutation({ mutationFn: () => api.setGitCredential(form), onSuccess: (d) => (done(d), setForm({ host: "", username: "", token: "" })) });
  const remove = useMutation({ mutationFn: (id: number) => api.removeGitCredential(id), onSuccess: done });
  const project = getCurrentProject();
  const test = useMutation({ mutationFn: () => api.testGit(project!) });
  return (
    <section className="card space-y-4 p-4">
      <div>
        <h2 className="font-semibold">Access tokens</h2>
        <p className="text-sm text-subtle">
          Fetching and pushing a project's repository over HTTPS uses your token for its host, so the git host sees you and your permissions. Tokens are stored encrypted
          and never shown again. Without a token for a host (or for SSH repositories), the server's own git access is used.
        </p>
      </div>
      {data.credentials.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border">
          {data.credentials.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <span className="min-w-40 flex-1 font-medium">{c.host}</span>
              <span className="text-subtle">{c.username}</span>
              <span className="text-xs text-subtle">saved {new Date(c.updated_at).toLocaleDateString()}</span>
              <button className="rounded-md px-2 py-1 text-sm text-bad hover:bg-bad/10" onClick={() => confirm(`Remove your token for ${c.host}?`) && remove.mutate(c.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <Field label="Host" hint="e.g. github.com or mycompany.ghe.com">
          <input className={input} value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
        </Field>
        <Field label="Username" hint="Your username on that host">
          <input className={input} autoComplete="off" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </Field>
        <Field label="Token" hint="With read and write access to the repositories">
          <input className={input} type="password" autoComplete="new-password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} />
        </Field>
        <button type="submit" className={`${primary} mb-[1.4rem]`} disabled={!form.host || !form.username || !form.token || add.isPending}>
          Save token
        </button>
      </form>
      {(add.error || remove.error) && <p className="text-sm text-bad">{((add.error || remove.error) as Error).message}</p>}
      {project && (
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3 text-sm">
          <button className={button} disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? "Testing…" : "Test access to this project's repository"}
          </button>
          {test.data &&
            (test.data.ok ? (
              <span className="text-ok">
                Can reach {test.data.repository} {test.data.with_token ? "with your token" : "with the server's own access"}.
              </span>
            ) : (
              <span className="text-bad">{test.data.error}</span>
            ))}
          {test.error && <span className="text-bad">{(test.error as Error).message}</span>}
        </div>
      )}
    </section>
  );
}

/** The signed-in user's own settings: their git identity and access tokens. */
export function AccountPage() {
  const { data } = useQuery({ queryKey: ["accountGit"], queryFn: api.accountGit });
  return (
    <div className="space-y-6">
      <PageTitle icon={<BranchIcon />} title="My settings" subtitle="Your git identity and access, used when you commit a run's code changes." tone="from-emerald-500 to-teal-600" />
      {data ? (
        <>
          <Identity data={data} />
          <Credentials data={data} />
        </>
      ) : (
        <p className="text-subtle">Loading…</p>
      )}
    </div>
  );
}
