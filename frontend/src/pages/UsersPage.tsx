import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Permission, type Role, type User } from "../lib/api";
import { useMe } from "../lib/hooks";
import { slug } from "../lib/slug";
import { Field, inputClass as input } from "../components/Field";
import { PageTitle } from "../components/PageTitle";
import { UsersIcon } from "../components/icons";

const button = "rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50 disabled:opacity-50";
const primary = "rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50";

function RolePicker({ roles, value, onChange }: { roles: Role[]; value: string[]; onChange: (roles: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {roles.map((r) => {
        const on = value.includes(r.key);
        return (
          <button
            key={r.key}
            type="button"
            aria-pressed={on}
            title={r.description}
            onClick={() => onChange(on ? value.filter((k) => k !== r.key) : [...value, r.key])}
            className={`rounded-full border px-3 py-1 text-sm ${on ? "border-accent bg-accent/15 text-accent" : "border-border bg-surface hover:border-accent/50"}`}
          >
            {r.name}
          </button>
        );
      })}
    </div>
  );
}

function AddUser({ roles, onDone }: { roles: Role[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ display_name: "", username: "", password: "", roles: ["developer"] });
  const create = useMutation({
    mutationFn: () => api.createUser(form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["roles"] });
      onDone();
    },
  });
  return (
    <form
      className="card space-y-4 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h3 className="font-semibold">Add a user</h3>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Name">
          <input className={input} autoFocus value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </Field>
        <Field label="Username">
          <input className={input} autoComplete="off" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} />
        </Field>
        <Field label="Initial password" hint="At least 8 characters; give it to them privately">
          <input
            className={input}
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Roles">
        <RolePicker roles={roles} value={form.roles} onChange={(r) => setForm({ ...form, roles: r })} />
      </Field>
      {create.error && <p className="text-sm text-bad">{(create.error as Error).message}</p>}
      <div className="flex gap-2">
        <button type="submit" className={primary} disabled={!form.display_name || !form.username || form.password.length < 8 || create.isPending}>
          Add user
        </button>
        <button type="button" className={button} onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function UserRow({ user, roles, isMe }: { user: User; roles: Role[]; isMe: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ display_name: user.display_name, roles: user.roles, active: user.active });
  const [password, setPassword] = useState<string | null>(null);
  useEffect(() => setForm({ display_name: user.display_name, roles: user.roles, active: user.active }), [user]);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["users"] });
    queryClient.invalidateQueries({ queryKey: ["roles"] });
    if (isMe) queryClient.invalidateQueries({ queryKey: ["me"] });
  };
  const save = useMutation({ mutationFn: () => api.updateUser(user.id, form), onSuccess: () => (refresh(), setEditing(false)) });
  const reset = useMutation({ mutationFn: () => api.resetPassword(user.id, password!), onSuccess: () => setPassword(null) });
  const remove = useMutation({ mutationFn: () => api.deleteUser(user.id), onSuccess: refresh });
  const roleName = (key: string) => roles.find((r) => r.key === key)?.name ?? key;
  const error = save.error || reset.error || remove.error;

  return (
    <li className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-48 flex-1">
          <span className="font-medium">{user.display_name}</span>
          {isMe && <span className="ml-2 text-xs text-subtle">(you)</span>}
          <span className="block text-xs text-subtle">
            {user.username} · {user.last_login_at ? `last signed in ${new Date(user.last_login_at).toLocaleString()}` : "never signed in"}
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {user.roles.length ? (
            user.roles.map((r) => (
              <span key={r} className="rounded-full bg-accent/10 px-2 py-0.5 text-xs text-accent">
                {roleName(r)}
              </span>
            ))
          ) : (
            <span className="text-xs text-subtle">no roles</span>
          )}
        </div>
        {!user.active && <span className="rounded-full bg-bad/10 px-2 py-0.5 text-xs text-bad">deactivated</span>}
        <div className="flex gap-2">
          <button className={button} onClick={() => setEditing(!editing)}>
            {editing ? "Close" : "Edit"}
          </button>
          <button className={button} onClick={() => setPassword(password === null ? "" : null)}>
            Reset password
          </button>
          {!isMe && (
            <button
              className="rounded-md px-2 py-1.5 text-sm text-bad hover:bg-bad/10 disabled:opacity-50"
              disabled={remove.isPending}
              onClick={() => confirm(`Delete ${user.display_name}? Their past approvals keep their name.`) && remove.mutate()}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {editing && (
        <div className="space-y-3 rounded-md border border-border bg-bg p-3">
          <Field label="Name">
            <input className={`${input} max-w-sm`} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </Field>
          <Field label="Roles">
            <RolePicker roles={roles} value={form.roles} onChange={(r) => setForm({ ...form, roles: r })} />
          </Field>
          {!isMe && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
              Active (deactivated users can't sign in and are signed out)
            </label>
          )}
          <button className={primary} disabled={!form.display_name.trim() || save.isPending} onClick={() => save.mutate()}>
            Save
          </button>
        </div>
      )}

      {password !== null && (
        <form
          className="flex flex-wrap items-end gap-2 rounded-md border border-border bg-bg p-3"
          onSubmit={(e) => {
            e.preventDefault();
            reset.mutate();
          }}
        >
          <div className="min-w-64">
            <Field label={`New password for ${user.display_name}`} hint={isMe ? undefined : "They are signed out everywhere"}>
              <input className={input} type="password" autoComplete="new-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </div>
          <button type="submit" className={`${primary} mb-[1.4rem]`} disabled={password.length < 8 || reset.isPending}>
            Set password
          </button>
        </form>
      )}
      {reset.isSuccess && password === null && <p className="text-sm text-ok">Password changed.</p>}
      {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
    </li>
  );
}

function UsersTab({ roles }: { roles: Role[] }) {
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: api.users });
  const { data: auth } = useMe();
  const [adding, setAdding] = useState(false);
  return (
    <div className="space-y-4">
      {adding ? (
        <AddUser roles={roles} onDone={() => setAdding(false)} />
      ) : (
        <button className={primary} onClick={() => setAdding(true)}>
          Add user
        </button>
      )}
      <ul className="card divide-y divide-border">
        {users?.map((u) => (
          <UserRow key={u.id} user={u} roles={roles} isMe={u.id === auth?.user?.id} />
        ))}
      </ul>
    </div>
  );
}

/** Roles × permissions: tick what each role may do, then save. */
function RolesTab({ roles }: { roles: Role[] }) {
  const queryClient = useQueryClient();
  const { data: permissions } = useQuery({ queryKey: ["permissions"], queryFn: api.permissions });
  const [draft, setDraft] = useState<Record<string, Permission[]>>({});
  const [newRole, setNewRole] = useState({ name: "", description: "" });
  useEffect(() => setDraft(Object.fromEntries(roles.map((r) => [r.key, r.permissions]))), [roles]);
  const changed = roles.filter((r) => !r.locked && [...(draft[r.key] ?? [])].sort().join() !== [...r.permissions].sort().join());
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["roles"] });
    queryClient.invalidateQueries({ queryKey: ["me"] });
  };
  const save = useMutation({
    mutationFn: () => Promise.all(changed.map((r) => api.updateRole(r.key, { name: r.name, description: r.description, permissions: draft[r.key] }))),
    onSuccess: refresh,
  });
  const create = useMutation({
    mutationFn: () => api.createRole({ key: slug(newRole.name, "-"), name: newRole.name, description: newRole.description, permissions: [] }),
    onSuccess: () => (refresh(), setNewRole({ name: "", description: "" })),
  });
  const remove = useMutation({ mutationFn: (key: string) => api.deleteRole(key), onSuccess: refresh });
  const toggle = (role: string, p: Permission) =>
    setDraft((d) => ({ ...d, [role]: d[role]?.includes(p) ? d[role].filter((x) => x !== p) : [...(d[role] ?? []), p] }));
  const error = save.error || create.error || remove.error;

  return (
    <div className="space-y-4">
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-4 py-2 font-medium">Permission</th>
              {roles.map((r) => (
                <th key={r.key} className="px-3 py-2 text-center font-medium" title={r.description}>
                  {r.name}
                  <span className="block text-xs font-normal text-subtle">
                    {r.users} user{r.users === 1 ? "" : "s"}
                  </span>
                  {!r.locked && (
                    <button
                      className="text-xs font-normal text-bad hover:underline"
                      onClick={() => confirm(`Delete the ${r.name} role? Its users lose its permissions.`) && remove.mutate(r.key)}
                    >
                      delete
                    </button>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {permissions?.map((p) => (
              <tr key={p.key} className="border-b border-border last:border-0">
                <td className="px-4 py-2">
                  <span className="font-medium">{p.label}</span>
                  <span className="block text-xs text-subtle">{p.description}</span>
                </td>
                {roles.map((r) => (
                  <td key={r.key} className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${r.name}: ${p.label}`}
                      checked={r.locked || !!draft[r.key]?.includes(p.key)}
                      disabled={r.locked}
                      onChange={() => toggle(r.key, p.key)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3">
        <button className={primary} disabled={!changed.length || save.isPending} onClick={() => save.mutate()}>
          Save permissions
        </button>
        {changed.length > 0 && <span className="text-sm text-subtle">{changed.map((r) => r.name).join(", ")} changed</span>}
        {save.isSuccess && !changed.length && <span className="text-sm text-ok">Saved</span>}
      </div>
      <p className="text-xs text-subtle">Admin always has every permission. Changes apply to signed-in users straight away.</p>

      <form
        className="card flex flex-wrap items-end gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="min-w-48">
          <Field label="New role">
            <input className={input} placeholder="e.g. Test lead" value={newRole.name} onChange={(e) => setNewRole({ ...newRole, name: e.target.value })} />
          </Field>
        </div>
        <div className="min-w-64 flex-1">
          <Field label="Description">
            <input className={input} value={newRole.description} onChange={(e) => setNewRole({ ...newRole, description: e.target.value })} />
          </Field>
        </div>
        <button type="submit" className={button} disabled={slug(newRole.name, "-").length < 2 || create.isPending}>
          Add role
        </button>
      </form>
      {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
    </div>
  );
}

export function UsersPage() {
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: api.roles });
  const [tab, setTab] = useState<"users" | "roles">("users");
  const tabClass = (t: string) => `rounded-full px-3 py-1.5 text-sm ${tab === t ? "bg-accent/15 font-medium text-accent" : "text-subtle hover:text-fg"}`;
  return (
    <div className="space-y-6">
      <PageTitle icon={<UsersIcon />} title="Users & roles" subtitle="Who can sign in, and what each role may do." tone="from-sky-500 to-indigo-600" />
      <div className="flex gap-1">
        <button className={tabClass("users")} onClick={() => setTab("users")}>
          Users
        </button>
        <button className={tabClass("roles")} onClick={() => setTab("roles")}>
          Roles & permissions
        </button>
      </div>
      {roles && (tab === "users" ? <UsersTab roles={roles} /> : <RolesTab roles={roles} />)}
    </div>
  );
}
