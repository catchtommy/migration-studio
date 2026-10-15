import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useCan, useProjects } from "../lib/hooks";
import { LANGUAGES, useSwitchProject } from "../lib/projectContext";
import { slug } from "../lib/slug";
import { Field, inputClass as input } from "../components/Field";
import { Members } from "./ProjectSettingsPage";
import { PageTitle } from "../components/PageTitle";
import { FolderIcon } from "../components/icons";

function NewProject({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const switchTo = useSwitchProject();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [language, setLanguage] = useState("java");
  const preset = LANGUAGES.find((l) => l.key === language)!;
  const [source, setSource] = useState(preset.source);
  const [target, setTarget] = useState(preset.target);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const projectKey = key || slug(name, "-");
  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        key: projectKey,
        name: name.trim(),
        description,
        settings: { language, source_stack: source, target_stack: target, monolith_repo: repo.trim(), monolith_branch: branch.trim() },
      }),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      onDone();
      switchTo(project.key);
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
      <h2 className="font-semibold">New project</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          <input className={input} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Lending platform" />
        </Field>
        <Field label="Key" hint="Lowercase letters, digits and dashes; can't be changed later">
          <input className={input} value={projectKey} onChange={(e) => setKey(slug(e.target.value, "-"))} />
        </Field>
      </div>
      <Field label="Description">
        <input className={input} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Language">
          <select
            className={input}
            value={language}
            onChange={(e) => {
              const next = LANGUAGES.find((l) => l.key === e.target.value)!;
              setLanguage(next.key);
              setSource(next.source);
              setTarget(next.target);
            }}
          >
            {LANGUAGES.map((l) => (
              <option key={l.key} value={l.key}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Monolith (from)">
          <input className={input} value={source} onChange={(e) => setSource(e.target.value)} />
        </Field>
        <Field label="Target (to)">
          <input className={input} value={target} onChange={(e) => setTarget(e.target.value)} />
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-[1fr_16rem]">
        <Field label="Monolith repository" hint="Git URL or local path; can be set later in the project's settings">
          <input className={input} value={repo} onChange={(e) => setRepo(e.target.value)} />
        </Field>
        <Field label="Base branch" hint="Empty: the repository's default branch">
          <input className={input} placeholder="e.g. main" value={branch} disabled={!repo.trim()} onChange={(e) => setBranch(e.target.value)} />
        </Field>
      </div>
      {create.error && <p className="text-sm text-bad">{(create.error as Error).message}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={!name.trim() || projectKey.length < 2 || create.isPending} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Create project
        </button>
        <button type="button" onClick={onDone} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Every project the user can open; global project admins can create new ones. */
export function ProjectsPage() {
  const { data: projects = [] } = useProjects();
  const canCreate = useCan("projects.manage");
  const switchTo = useSwitchProject();
  const [adding, setAdding] = useState(false);
  const [managing, setManaging] = useState<string | null>(null);
  const mine = projects.filter((p) => p.member);
  const others = projects.filter((p) => !p.member);
  return (
    <div className="space-y-6">
      <PageTitle icon={<FolderIcon />} title="Projects" subtitle="Each project has its own monolith, domains, runs, contracts, settings and members." tone="from-cyan-500 to-blue-600">
        {canCreate && !adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white">
            New project
          </button>
        )}
      </PageTitle>
      {adding && <NewProject onDone={() => setAdding(false)} />}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {mine.map((p) => (
          <button key={p.key} onClick={() => switchTo(p.key)} className="card p-4 text-left transition hover:border-accent/50 hover:shadow-md">
            <span className="block font-semibold">{p.name}</span>
            <code className="block text-xs text-subtle">{p.key}</code>
            {p.description && <span className="mt-2 block text-sm text-subtle">{p.description}</span>}
            <span className="mt-3 flex flex-wrap gap-3 text-xs text-subtle">
              <span>
                {p.runs} run{p.runs === 1 ? "" : "s"}
              </span>
              <span>
                {p.members} member{p.members === 1 ? "" : "s"}
              </span>
              <span className="rounded-full bg-accent/10 px-2 text-accent">{p.my_role}</span>
            </span>
          </button>
        ))}
        {mine.length === 0 && <p className="text-sm text-subtle">You aren't a member of any project yet.</p>}
      </div>
      {others.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="font-semibold">Projects you're not in</h2>
            <p className="text-sm text-subtle">You can manage who has access to these, but only members can open them and their runs. Add yourself to work in one.</p>
          </div>
          <ul className="card divide-y divide-border">
            {others.map((p) => (
              <li key={p.key} className="space-y-3 px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="min-w-48 flex-1">
                    <span className="font-medium">{p.name}</span>
                    <code className="ml-2 text-xs text-subtle">{p.key}</code>
                    <span className="block text-xs text-subtle">
                      {p.runs} run{p.runs === 1 ? "" : "s"} · {p.members} member{p.members === 1 ? "" : "s"}
                    </span>
                  </span>
                  <button onClick={() => setManaging(managing === p.key ? null : p.key)} className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50">
                    {managing === p.key ? "Close" : "Manage access"}
                  </button>
                </div>
                {managing === p.key && <Members project={p.key} manage />}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
