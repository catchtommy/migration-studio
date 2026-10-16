import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, api, type DatabaseConfig, type DatabaseDialect, type DatabaseStatus } from "../lib/api";
import { Field, inputClass as input } from "./Field";
import { parseOracleConnect } from "../lib/oracleConnect";

const DIALECTS: { key: DatabaseDialect; title: string; note: string }[] = [
  { key: "postgresql", title: "PostgreSQL", note: "Recommended for production" },
  { key: "oracle", title: "Oracle", note: "19c or later" },
  { key: "sqlite", title: "SQLite", note: "Local development only" },
];
const DEFAULT_PORTS: Record<DatabaseDialect, number | null> = { postgresql: 5432, oracle: 1521, sqlite: null };
const SSL_MODES = ["", "disable", "prefer", "require", "verify-ca", "verify-full"];

const empty = (dialect: DatabaseDialect): DatabaseConfig => ({ dialect, host: "", port: null, database: "", username: "", password: "", options: {}, oracle_client: "" });

/** Choosing the studio's database: used by the first-time setup screen and the Database section of Settings. */
export function DatabaseForm({
  current,
  switching,
  onDone,
}: {
  current: DatabaseConfig | null;
  switching: boolean; // a database is in use already: offer to copy its data
  onDone: (status: DatabaseStatus) => void;
}) {
  const [cfg, setCfg] = useState<DatabaseConfig>(() => (current && current.dialect !== "sqlite" ? { ...current, password: "" } : empty("postgresql")));
  const [split, setSplit] = useState(false); // a pasted Oracle connection string was split into the fields
  const [copyData, setCopyData] = useState(true);
  const [replace, setReplace] = useState(false);
  const [targetHasData, setTargetHasData] = useState(false);
  const set = (patch: Partial<DatabaseConfig>) => {
    setCfg((c) => ({ ...c, ...patch }));
    test.reset();
    setTargetHasData(false);
  };

  const test = useMutation({ mutationFn: () => api.testDatabase(cfg) });
  const save = useMutation({
    mutationFn: () => api.configureDatabase({ config: cfg, copy_data: switching && copyData, replace }),
    onSuccess: onDone,
    onError: (e) => setTargetHasData(e instanceof ApiError && e.status === 409 && e.message.includes("already has data")),
  });

  const sameServer = current && current.dialect === cfg.dialect && current.host === cfg.host && current.port === cfg.port && current.username === cfg.username;
  const keepsPassword = !!(sameServer && current?.has_password);
  const server = cfg.dialect !== "sqlite";
  const complete = !server || (cfg.host.trim() && cfg.database.trim() && cfg.username.trim());

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Database type">
        {DIALECTS.map((d) => (
          <button
            key={d.key}
            type="button"
            role="radio"
            aria-checked={cfg.dialect === d.key}
            onClick={() => set({ ...empty(d.key), ...(current?.dialect === d.key ? { ...current, password: "" } : {}) })}
            className={`rounded-lg border px-3 py-2 text-left text-sm transition ${cfg.dialect === d.key ? "border-accent bg-accent/10 ring-1 ring-accent" : "border-border bg-surface hover:border-accent/50"}`}
          >
            <span className="block font-medium">{d.title}</span>
            <span className="block text-xs text-subtle">{d.note}</span>
          </button>
        ))}
      </div>

      {server ? (
        <>
          <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
            <Field label="Host" hint={cfg.dialect === "oracle" ? "Server name or IP only (no jdbc:oracle:thin:@). Pasting a JDBC URL fills host, port and service name." : undefined}>
              <input
                className={input}
                value={cfg.host}
                placeholder="db.example.internal"
                onChange={(e) => {
                  // A pasted Oracle connection string (jdbc:oracle:thin:@//host:1521/SERVICE …) fills the three fields.
                  const parsed = cfg.dialect === "oracle" ? parseOracleConnect(e.target.value) : null;
                  if (parsed) {
                    set({ host: parsed.host, port: parsed.port ?? cfg.port, database: parsed.service || cfg.database });
                    setSplit(true);
                  } else {
                    set({ host: e.target.value.trim() });
                    setSplit(false);
                  }
                }}
              />
              {split && <span className="mt-1 block text-xs text-ok">Split the connection string into host, port and service name.</span>}
            </Field>
            <Field label="Port">
              <input
                className={input}
                inputMode="numeric"
                value={cfg.port ?? ""}
                placeholder={String(DEFAULT_PORTS[cfg.dialect])}
                onChange={(e) => set({ port: e.target.value ? Number(e.target.value.replace(/\D/g, "")) || null : null })}
              />
            </Field>
          </div>
          <Field
            label={cfg.dialect === "oracle" ? "Service name" : "Database"}
            hint={cfg.dialect === "oracle" ? "The service of the pluggable database, e.g. MIGSTUDIO or FREEPDB1." : "Create it first, with UTF8 encoding."}
          >
            <input className={input} value={cfg.database} onChange={(e) => set({ database: e.target.value.trim() })} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Username">
              <input className={input} autoComplete="off" value={cfg.username} onChange={(e) => set({ username: e.target.value.trim() })} />
            </Field>
            <Field label="Password" hint={keepsPassword ? "Leave blank to keep the saved password." : undefined}>
              <input
                className={input}
                type="password"
                autoComplete="new-password"
                value={cfg.password ?? ""}
                onChange={(e) => set({ password: e.target.value })}
              />
            </Field>
          </div>
          {cfg.dialect === "postgresql" && (
            <Field label="SSL mode" hint="Leave as default unless your database requires TLS.">
              <select
                className={`${input} max-w-xs`}
                value={cfg.options.sslmode ?? ""}
                onChange={(e) => {
                  const { sslmode: _, ...rest } = cfg.options;
                  set({ options: e.target.value ? { ...rest, sslmode: e.target.value } : rest });
                }}
              >
                {SSL_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m || "Default (prefer)"}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {cfg.dialect === "oracle" && (
            <Field
              label="Oracle Instant Client folder (optional)"
              hint="Leave blank to connect without any Oracle software (thin mode). Fill it in to use thick mode, e.g. when the user's password only has Oracle's old 10G verifier (error DPY-3015): download Instant Client Basic from oracle.com, unzip it, and enter the folder, e.g. C:\oracle\instantclient_23_5. Changing it later needs a restart."
            >
              <input className={input} value={cfg.oracle_client ?? ""} onChange={(e) => set({ oracle_client: e.target.value.trim() })} />
            </Field>
          )}
          <p className="text-xs text-subtle">
            The connection is saved in <code>backend/data/database.json</code>, readable only by the account running the backend. Tables are created
            automatically.
          </p>
        </>
      ) : (
        <>
          <Field label="Database file" hint="Leave blank for backend/data/studio.db.">
            <input className={input} value={cfg.database} onChange={(e) => set({ database: e.target.value.trim() })} />
          </Field>
          <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
            SQLite is for trying the studio out on one machine. It allows one writer at a time and lives on this server's disk: use PostgreSQL or Oracle for a
            shared or production installation.
          </p>
        </>
      )}

      {switching && (
        <div className="space-y-2 rounded-md border border-border bg-surface px-3 py-2 text-sm">
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={copyData} onChange={(e) => setCopyData(e.target.checked)} />
            <span>
              Copy all data from the current database
              <span className="block text-xs text-subtle">
                Runs, documents, agents, workflows, domains, contracts and settings. Otherwise the new database starts with the defaults.
              </span>
            </span>
          </label>
          {targetHasData && (
            <label className="flex items-start gap-2 text-bad">
              <input type="checkbox" className="mt-1" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
              <span>Replace the data already in that database</span>
            </label>
          )}
          <p className="text-xs text-subtle">Runs must not be working while the database is switched.</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!complete || test.isPending}
          onClick={() => test.mutate()}
          className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50 disabled:opacity-50"
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
        <button
          type="submit"
          disabled={!complete || save.isPending}
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {save.isPending ? (switching && copyData ? "Copying data…" : "Setting up…") : switching ? "Switch database" : "Save and continue"}
        </button>
        {test.isSuccess && <span className="text-sm text-ok">Connected: {test.data.version}</span>}
      </div>
      {test.error && <p className="text-sm text-bad">{(test.error as Error).message}</p>}
      {save.error && <p className="text-sm text-bad">{(save.error as Error).message}</p>}
    </form>
  );
}
