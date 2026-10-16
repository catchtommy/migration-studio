import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Me } from "../lib/api";
import { Field, inputClass as input } from "./Field";

/** The signed-in user in the header: change password, sign out. */
export function AccountMenu({ user }: { user: Me }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [form, setForm] = useState({ current: "", next: "" });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const signOut = useMutation({ mutationFn: api.logout, onSettled: () => queryClient.resetQueries() });
  const change = useMutation({
    mutationFn: () => api.changePassword(form.current, form.next),
    onSuccess: () => {
      setChanging(false);
      setForm({ current: "", next: "" });
    },
  });

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-xs text-white/90 ring-1 ring-white/20 hover:bg-white/20"
      >
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/25 text-[10px] font-semibold uppercase">
          {user.display_name.slice(0, 1)}
        </span>
        {user.display_name}
      </button>
      {open && (
        <div className="card absolute right-0 z-20 mt-2 w-72 space-y-3 p-3 text-sm">
          <div>
            <span className="font-medium">{user.display_name}</span>
            <span className="block text-xs text-subtle">
              {user.username} · {user.roles.join(", ") || "no roles"}
            </span>
          </div>
          {changing ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                change.mutate();
              }}
            >
              <Field label="Current password">
                <input
                  className={input}
                  type="password"
                  autoComplete="current-password"
                  value={form.current}
                  onChange={(e) => setForm({ ...form, current: e.target.value })}
                />
              </Field>
              <Field label="New password" hint="At least 8 characters">
                <input
                  className={input}
                  type="password"
                  autoComplete="new-password"
                  value={form.next}
                  onChange={(e) => setForm({ ...form, next: e.target.value })}
                />
              </Field>
              {change.error && <p className="text-xs text-bad">{(change.error as Error).message}</p>}
              <button
                type="submit"
                disabled={!form.current || form.next.length < 8 || change.isPending}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Change password
              </button>
            </form>
          ) : (
            <button className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-muted" onClick={() => setChanging(true)}>
              Change password
            </button>
          )}
          {change.isSuccess && !changing && <p className="text-xs text-ok">Password changed.</p>}
          <Link to="/account" onClick={() => setOpen(false)} className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-muted">
            My settings (git access)
          </Link>
          <button className="block w-full rounded-md px-2 py-1.5 text-left text-bad hover:bg-bad/10" onClick={() => signOut.mutate()}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
