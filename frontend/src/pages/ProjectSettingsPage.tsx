import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, getCurrentProject, type ProjectSettings } from "../lib/api";
import { useCan, useDomains, useIsAdmin, useWorkflows } from "../lib/hooks";
import { DangerDialog } from "../components/DangerDialog";
import { LANGUAGES, useSwitchProject } from "../lib/projectContext";
import { Field, inputClass as input } from "../components/Field";
import { Guarded } from "../components/Guarded";
import { PageTitle } from "../components/PageTitle";
import { FolderIcon } from "../components/icons";

type Tab = "general" | "members";

/** Who can open a project and its runs, and their role in it. `manage` overrides the current project's permission,
 * for global project admins managing a project they aren't in. */
export function Members({ project, manage }: { project: string; manage?: boolean }) {
  const queryClient = useQueryClient();
  const canManageCurrent = useCan("project.manage");
  const canManage = manage ?? canManageCurrent;
  const { data: members = [] } = useQuery({ queryKey: ["members", project], queryFn: () => api.members(project) });
  const { data: roles = [] } = useQuery({ queryKey: ["projectRoles"], queryFn: api.roles, enabled: canManage, retry: false });
  const { data: candidates = [] } = useQuery({ queryKey: ["candidates", project], queryFn: () => api.candidates(project), enabled: canManage });
  const [userId, setUserId] = useState<number | "">("");
  const [role, setRole] = useState("developer");
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["members", project] });
    queryClient.invalidateQueries({ queryKey: ["candidates", project] });
    queryClient.invalidateQueries({ queryKey: ["projects"] });
    queryClient.invalidateQueries({ queryKey: ["users"] });
  };
  const add = useMutation({ mutationFn: () => api.setMember(project, Number(userId), role), onSuccess: () => (refresh(), setUserId("")) });
  const change = useMutation({ mutationFn: ({ id, r }: { id: number; r: string }) => api.setMember(project, id, r), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: number) => api.removeMember(project, id), onSuccess: refresh });
  // Project admins may not manage users globally, so the role list may be unavailable: fall back to the defaults.
  const roleOptions = roles.length ? roles.map((r) => ({ key: r.key, name: r.name })) : ["admin", "architect", "developer", "reviewer", "viewer"].map((k) => ({ key: k, name: k[0].toUpperCase() + k.slice(1) }));
  const error = add.error || change.error || remove.error;

  return (
    <div className="space-y-4">
      <p className="text-sm text-subtle">
        Only members can open this project and its runs. Their role here alone decides what they can do in it; roles they have elsewhere don't apply.
      </p>
      {canManage && (
        <form
          className="card flex flex-wrap items-end gap-3 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <div className="min-w-64 flex-1">
            <Field label="Add a person">
              <select className={input} value={userId} onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Choose…</option>
                {candidates.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.display_name} ({c.username})
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="w-44">
            <Field label="Role in this project">
              <select className={input} value={role} onChange={(e) => setRole(e.target.value)}>
                {roleOptions.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <button type="submit" disabled={!userId || add.isPending} className="mb-[1.4rem] rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Add
          </button>
          {candidates.length === 0 && <p className="w-full text-xs text-subtle">Everyone is a member already. New people are added on the Users page first.</p>}
        </form>
      )}
      <ul className="card divide-y divide-border">
        {members.map((m) => (
          <li key={m.user_id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
            <span className="min-w-48 flex-1">
              <span className="font-medium">{m.display_name}</span>
              <span className="block text-xs text-subtle">
                {m.username}
                {!m.active && " · deactivated"}
              </span>
            </span>
            {canManage ? (
              <>
                <select
                  aria-label={`Role of ${m.display_name}`}
                  className="rounded-md border border-border bg-surface px-2 py-1 text-sm"
                  value={m.role}
                  onChange={(e) => change.mutate({ id: m.user_id, r: e.target.value })}
                >
                  {roleOptions.map((r) => (
                    <option key={r.key} value={r.key}>
                      {r.name}
                    </option>
                  ))}
                </select>
                <button
                  className="rounded-md px-2 py-1 text-sm text-bad hover:bg-bad/10"
                  onClick={() => confirm(`Remove ${m.display_name} from this project?`) && remove.mutate(m.user_id)}
                >
                  Remove
                </button>
              </>
            ) : (
              <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs text-accent">{m.role}</span>
            )}
          </li>
        ))}
        {members.length === 0 && <li className="px-4 py-3 text-sm text-subtle">No members yet.</li>}
      </ul>
      {error && <p className="text-sm text-bad">{(error as Error).message}</p>}
    </div>
  );
}

/** A one-line note under the repository setting: the server's copy, which each new run fetches first. */
function RepositoryNote({ project }: { project: string }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["repository", project],
    queryFn: () => api.repository(project),
    refetchInterval: (q) => (q.state.data?.fetching ? 1500 : false),
  });
  const fetchLatest = useMutation({
    mutationFn: () => api.fetchRepository(project),
    onSuccess: (status) => queryClient.setQueryData(["repository", project], status),
  });
  if (!data?.url) return null;
  const error = data.error ?? (fetchLatest.error as Error | null)?.message;
  return (
    <p className="-mt-2 text-xs text-subtle">
      {data.fetching ? (
        data.cloned ? "Fetching the latest changes…" : "Cloning to the server…"
      ) : data.cloned ? (
        <>
          On the server{data.fetched_at && <>, fetched {new Date(data.fetched_at).toLocaleString()}</>}
          {data.legacy ? (
            <>
              {" "}
              · agents read one shared, read-only copy at <code>{data.legacy.sha.slice(0, 8)}</code> {data.legacy.subject}
            </>
          ) : (
            data.head && (
              <>
                {" "}
                · latest <code>{data.head.sha.slice(0, 8)}</code> {data.head.subject}
              </>
            )
          )}
          . Fetch to give new runs the latest; runs in progress keep theirs.{" "}
          <button type="button" onClick={() => fetchLatest.mutate()} className="text-accent hover:underline">
            Fetch now
          </button>
        </>
      ) : (
        "Not on the server yet; it's cloned when saved or by the first run."
      )}
      {error && !data.fetching && (
        <span className="block text-bad">
          {error}{" "}
          <button type="button" onClick={() => fetchLatest.mutate()} className="text-accent hover:underline">
            Try again
          </button>
        </span>
      )}
    </p>
  );
}

function General({ project }: { project: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const switchTo = useSwitchProject();
  const canDelete = useIsAdmin();
  const { data } = useQuery({ queryKey: ["project", project], queryFn: () => api.project(project) });
  const { data: workflows = [] } = useWorkflows();
  const migrationWorkflows = workflows.filter((w) => w.scope === "operation");
  const [details, setDetails] = useState({ name: "", description: "" });
  const [form, setForm] = useState<ProjectSettings | null>(null);
  useEffect(() => {
    if (data) {
      setDetails({ name: data.name, description: data.description });
      setForm(data.settings);
    }
  }, [data]);
  const save = useMutation({
    mutationFn: async () => {
      await api.updateProject(project, details);
      return api.updateProjectSettings(project, form!);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", project] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["repository", project] }); // the repository or branch may have changed
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProject(project),
    onSuccess: async () => {
      const rest = (await queryClient.fetchQuery({ queryKey: ["projects"], queryFn: api.projects })).filter((p) => p.key !== project);
      if (rest[0]) switchTo(rest[0].key);
      navigate("/projects");
    },
  });
  if (!data || !form) return <p className="text-subtle">Loading…</p>;
  const set = (key: string, value: string | number) => setForm({ ...form, [key]: value });
  const lang = LANGUAGES.find((l) => l.key === form.language) ?? LANGUAGES[LANGUAGES.length - 1];

  return (
    <Guarded permission="project.manage">
      <form
        className="max-w-3xl space-y-8"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <section className="space-y-4">
          <h2 className="font-semibold">Project</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <input className={input} value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} />
            </Field>
            <Field label="Key">
              <input className={`${input} text-subtle`} value={data.key} readOnly />
            </Field>
          </div>
          <Field label="Description">
            <input className={input} value={details.description} onChange={(e) => setDetails({ ...details, description: e.target.value })} />
          </Field>
        </section>

        <section className="space-y-4">
          <h2 className="font-semibold">Language and stack</h2>
          <p className="text-sm text-subtle">Every agent is told what it migrates from and to, so AI agents work in this project's language even where their instructions mention Java.</p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Language">
              <select className={input} value={form.language as string} onChange={(e) => set("language", e.target.value)}>
                {LANGUAGES.map((l) => (
                  <option key={l.key} value={l.key}>
                    {l.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Monolith (from)">
              <input className={input} value={form.source_stack as string} placeholder={lang.source} onChange={(e) => set("source_stack", e.target.value)} />
            </Field>
            <Field label="Target (to)">
              <input className={input} value={form.target_stack as string} placeholder={lang.target} onChange={(e) => set("target_stack", e.target.value)} />
            </Field>
          </div>
        </section>

        <section className="space-y-4">
          <h2 className="font-semibold">Source and toolchains</h2>
          <div className="grid gap-4 sm:grid-cols-[1fr_16rem]">
            <Field label="Monolith repository" hint="Git URL or local path, kept once on the server for all runs. Empty: a blank workspace.">
              <input className={input} value={form.monolith_repo as string} onChange={(e) => set("monolith_repo", e.target.value)} />
            </Field>
            <Field label="Base branch" hint="Runs start from this branch or tag. Empty: the repository's default branch.">
              <input
                className={input}
                placeholder="e.g. main or release/2.4"
                value={(form.monolith_branch as string) ?? ""}
                disabled={!form.monolith_repo}
                onChange={(e) => set("monolith_branch", e.target.value.trim())}
              />
            </Field>
          </div>
          <RepositoryNote project={project} />
          <Field
            label="Monolith documentation folder"
            hint="Approved AS-IS documents are published here, as <folder>/<domain>/<feature>/, on the base branch; later agents read them before the code."
          >
            <input
              className={`${input} max-w-xs font-mono`}
              value={(form.monolith_docs_dir as string) ?? "docs"}
              disabled={!form.monolith_repo}
              onChange={(e) => set("monolith_docs_dir", e.target.value.trim())}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={`Legacy ${lang.toolchain} home`} hint="The monolith's toolchain, for agents set to Legacy">
              <input className={input} value={form.jdk_legacy_home as string} onChange={(e) => set("jdk_legacy_home", e.target.value)} />
            </Field>
            <Field label={`Target ${lang.toolchain} home`} hint="The new services' toolchain, for agents set to Target">
              <input className={input} value={form.jdk_modern_home as string} onChange={(e) => set("jdk_modern_home", e.target.value)} />
            </Field>
          </div>
          <Field label="Test database connection" hint="Read-only connection to the monolith's database (e.g. a JDBC URL), for schema metadata and integration tests.">
            <input className={input} value={form.oracle_test_url as string} onChange={(e) => set("oracle_test_url", e.target.value)} />
          </Field>
          <Field label="Self-check attempts" hint="How many times an agent is sent back when a required document is missing.">
            <input type="number" min={1} max={10} className={`${input} max-w-32`} value={form.max_attempts as number} onChange={(e) => set("max_attempts", Number(e.target.value))} />
          </Field>
        </section>

        <section className="space-y-4">
          <h2 className="font-semibold">Migrations</h2>
          <Field
            label="Default migration workflow"
            hint="Selected when starting a migration in this project. A domain can have a default of its own (its Features tab), which comes first."
          >
            <select className={`${input} max-w-md`} value={(form.default_workflow as string) ?? ""} onChange={(e) => set("default_workflow", e.target.value)}>
              <option value="">Standard migration (built-in default)</option>
              {migrationWorkflows
                .filter((w) => w.key !== "standard")
                .map((w) => (
                  <option key={w.key} value={w.key}>
                    {w.name}
                  </option>
                ))}
            </select>
          </Field>
        </section>

        <section className="space-y-4">
          <h2 className="font-semibold">Cross-domain integration</h2>
          <p className="text-sm text-subtle">Conventions given to the agents when they design and build cross-domain calls. No secrets are stored here.</p>
          <Field label="Azure Service Bus namespace" hint="e.g. mycompany-migration.servicebus.windows.net">
            <input className={input} value={form.event_bus_namespace as string} onChange={(e) => set("event_bus_namespace", e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Event topic per domain" hint="{domain} is replaced">
              <input className={input} value={form.event_topic_pattern as string} onChange={(e) => set("event_topic_pattern", e.target.value)} />
            </Field>
            <Field label="Command queue per domain" hint="For saga steps">
              <input className={input} value={form.command_queue_pattern as string} onChange={(e) => set("command_queue_pattern", e.target.value)} />
            </Field>
            <Field label="Service directory" hint="Where each microservice lives in the repository">
              <input className={input} value={form.service_dir_pattern as string} onChange={(e) => set("service_dir_pattern", e.target.value)} />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Domain module folder" hint="In the monolith; {domain} is replaced. One per domain: every feature of the domain extends it.">
              <input className={input} value={form.domain_module_dir_pattern as string} onChange={(e) => set("domain_module_dir_pattern", e.target.value)} />
            </Field>
            <Field label="Orchestrator module folder" hint="In the monolith; {domain} is replaced. One per domain, for its cross-domain features.">
              <input className={input} value={form.orchestrator_module_dir_pattern as string} onChange={(e) => set("orchestrator_module_dir_pattern", e.target.value)} />
            </Field>
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="font-semibold">Guardrails</h2>
          <p className="text-sm text-subtle">
            Guardrails are kept per agent: each agent's page on the{" "}
            <Link to="/agents" className="text-accent hover:underline">
              Agents page
            </Link>{" "}
            has its rules in plain words; the built-in checks are always on for the agents they belong to. To give this project different rules,
            customise the agent for this project.
          </p>
        </section>

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending || !details.name.trim()} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Save project settings
          </button>
          {save.isSuccess && <span className="text-sm text-ok">Saved</span>}
          {save.error && <span className="text-sm text-bad">{(save.error as Error).message}</span>}
        </div>
      </form>
      {canDelete && <DeleteProject name={data.name} projectKey={project} remove={remove} />}
    </Guarded>
  );
}

/** An admin deletes the project with everything in it: a sharp, colour-coded warning with what it holds, and its
 * key to type before the red button unlocks. */
function DeleteProject({ name, projectKey, remove }: { name: string; projectKey: string; remove: { isPending: boolean; error: unknown; mutate: () => void } }) {
  const [open, setOpen] = useState(false);
  const { data: runs = [] } = useQuery({ queryKey: ["runs"], queryFn: api.runs, enabled: open });
  const { data: domains = [] } = useDomains();
  const migrations = runs.filter((r) => r.scope === "operation").length;
  const working = runs.filter((r) => r.status === "running" || r.status === "awaiting_input").length;
  const features = domains.reduce((n, d) => n + (d.features ?? 0), 0);
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

  return (
    <section className="mt-10 max-w-3xl space-y-2 rounded-lg border border-bad/40 bg-bad/5 p-4">
      <h2 className="font-semibold text-bad">Delete project</h2>
      <p className="text-sm text-subtle">Deletes {name} with every run and everything recorded in it. Only an admin can do this, and it can't be undone.</p>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border border-bad/60 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/10"
      >
        Delete project…
      </button>
      {open && (
        <DangerDialog
          title={`Delete the project ${name}`}
          sections={[
            {
              tone: "bad",
              title: "Deleted for good",
              items: [
                <>
                  <strong>{plural(runs.length, "run")}</strong> ({plural(migrations, "feature migration")}, {plural(runs.length - migrations, "discovery run")}), with all
                  their steps, documents, approvals, questions, activity logs, saved progress and workspaces
                </>,
                <>
                  <strong>{plural(domains.length, "domain")}</strong> and their <strong>{plural(features, "feature")}</strong>, with the inputs and ownership given to them
                </>,
                "The project's contracts catalogue and the cross-domain dependencies recorded in it",
                "This project's own agents and workflows, and its copies of shared ones",
                "The project's settings, its members and their access, and the server's copy of its monolith repository",
              ],
            },
            ...(working
              ? [{ tone: "warn" as const, title: "Stopped first", text: `${plural(working, "run")} ${working === 1 ? "is" : "are"} working right now: stopped at the next step before anything is deleted.` }]
              : []),
            {
              tone: "ok",
              title: "Kept",
              items: [
                "Every other project, with its runs and data",
                "The shared agent library, shared workflows and shared texts, which other projects use",
                "User accounts (people lose access to this project only)",
              ],
            },
            {
              tone: "neutral",
              title: "Not undone in git",
              text: "The monolith and service repositories themselves, code already pushed to feature branches and AS-IS documents published to the monolith stay there.",
            },
          ]}
          confirmText={projectKey}
          actionLabel={`Delete ${name} and all its data`}
          pendingLabel={working ? "Stopping and deleting…" : "Deleting…"}
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate()}
          onClose={() => setOpen(false)}
        />
      )}
    </section>
  );
}

/** The current project's settings and members. */
export function ProjectSettingsPage() {
  const project = getCurrentProject()!;
  const [tab, setTab] = useState<Tab>("general");
  const { data } = useQuery({ queryKey: ["project", project], queryFn: () => api.project(project) });
  const tabClass = (t: Tab) => `rounded-full px-3 py-1.5 text-sm ${tab === t ? "bg-accent/15 font-medium text-accent" : "text-subtle hover:text-fg"}`;
  return (
    <div className="space-y-6">
      <PageTitle icon={<FolderIcon />} title={data?.name ?? "Project"} subtitle="This project's monolith, stack, toolchains, conventions and members." tone="from-cyan-500 to-blue-600" />
      <div className="flex gap-1">
        <button className={tabClass("general")} onClick={() => setTab("general")}>
          Settings
        </button>
        <button className={tabClass("members")} onClick={() => setTab("members")}>
          Members{data ? ` (${data.members})` : ""}
        </button>
      </div>
      {tab === "general" ? (
        <General key={project} project={project} />
      ) : (
        <Members key={project} project={project} />
      )}
    </div>
  );
}
