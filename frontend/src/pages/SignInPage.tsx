import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Field, inputClass as input } from "../components/Field";
import { LogoMark } from "../components/icons";

function Frame({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-start justify-center px-4 py-16">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-600 to-fuchsia-600 text-white shadow-md">
            <LogoMark className="h-7 w-7" />
          </span>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-subtle">{subtitle}</p>
          </div>
        </div>
        <div className="card p-5">{children}</div>
      </div>
    </div>
  );
}

export function SignInPage() {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const signIn = useMutation({
    mutationFn: () => api.login(username, password),
    onSuccess: () => queryClient.resetQueries(), // nothing cached from a previous session is kept
  });
  return (
    <Frame title="Migration Studio" subtitle="Sign in to continue">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          signIn.mutate();
        }}
      >
        <Field label="Username">
          <input className={input} autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Password">
          <input className={input} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {signIn.error && <p className="text-sm text-bad">{(signIn.error as Error).message}</p>}
        <button
          type="submit"
          disabled={!username || !password || signIn.isPending}
          className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {signIn.isPending ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-xs text-subtle">Forgot your password? Ask an admin to reset it on the Users page.</p>
      </form>
    </Frame>
  );
}

/** No one can sign in yet: whoever opens the studio first creates the admin account. */
export function CreateAdminPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ username: "", display_name: "", password: "", confirm: "" });
  const create = useMutation({
    mutationFn: () => api.bootstrap({ username: form.username, display_name: form.display_name, password: form.password }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const mismatch = form.confirm.length > 0 && form.confirm !== form.password;
  return (
    <Frame title="Create the admin account" subtitle="The first account can add everyone else">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Field label="Your name">
          <input className={input} autoFocus value={form.display_name} onChange={set("display_name")} />
        </Field>
        <Field label="Username" hint="Letters, digits and . _ @ -">
          <input className={input} autoComplete="username" value={form.username} onChange={set("username")} />
        </Field>
        <Field label="Password" hint="At least 8 characters">
          <input className={input} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <Field label="Confirm password">
          <input className={input} type="password" autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />
        </Field>
        {mismatch && <p className="text-sm text-bad">The passwords don't match</p>}
        {create.error && <p className="text-sm text-bad">{(create.error as Error).message}</p>}
        <button
          type="submit"
          disabled={!form.username || !form.display_name || form.password.length < 8 || form.confirm !== form.password || create.isPending}
          className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Create admin and sign in
        </button>
      </form>
    </Frame>
  );
}
