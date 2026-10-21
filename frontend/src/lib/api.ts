export type RunStatus = "pending" | "running" | "awaiting_input" | "awaiting_approval" | "changes_requested" | "awaiting_branches" | "awaiting_commit" | "awaiting_publish" | "completed" | "failed";

export interface StageDef {
  key: string;
  title: string;
  kind: "agent" | "gate";
  description: string;
  reviews: string[];
  uses?: string[] | null;
  include_content?: boolean;
  code?: StepCode | null;
  publish_docs?: boolean;
}

export type Scope = "operation" | "domain" | "monolith";
export const SCOPE_LABELS: Record<Scope, string> = {
  operation: "Migration of one feature",
  domain: "Feature listing of one domain",
  monolith: "Bounded contexts of the whole monolith",
};

/** Reserved names of discovery runs (see backend app/discovery_store.py). */
export const MONOLITH = "_monolith";
export const FEATURES_OP = "_features";

export interface Run {
  id: string;
  domain: string;
  operation: string;
  scope: Scope; // operation: a migration of one feature; domain / monolith: discovery
  workflow: string | null;
  workflow_stages: StageDef[];
  status: RunStatus;
  current_stage: string | null;
  branch: string | null; // the feature branch
  base_branch: string | null; // the branch it was cut from (null: the repository's default branch)
  repository: string | null; // the monolith's, at the workspace root
  repositories: { key: string; name: string; url: string; base_branch: string | null; path: string }[] | null; // the others
  legacy: { url: string; branch: string; sha: string; subject?: string } | null; // the shared read-only monolith it reads
  delivery: Delivery | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** Committing a run's code: whether it will be reviewed and committed, then what was committed to each repository. */
export interface Delivery {
  commit?: boolean;
  repos?: Record<string, RepoDelivery>;
  docs?: { sha: string; branch: string; repository: string; published: string[]; by: string; at: string }; // AS-IS docs published to the monolith
}

export interface RepoDelivery {
  name?: string;
  repository?: string;
  status?: "pushed" | "skipped";
  sha?: string;
  branch?: string;
  base_branch?: string;
  files?: string[];
  message?: string;
  by?: string;
  at?: string;
  compare_url?: string | null;
  history?: { sha: string; branch: string; at: string; by: string }[];
}

/** A step about to change code in a repository for the first time in the run: its branches to confirm. */
export interface PendingBranches {
  stage: string;
  stage_title: string;
  feature_branch: string;
  repository: { key: string; name: string; url: string; base_branch: string | null; checked_out: boolean; default_branch: string | null };
  existing_branch?: { in_run: boolean; message: string } | null; // the feature branch already exists there
  parallel_runs?: { run_id: string; subject: string; branch: string; by: string | null; stage: string | null }[]; // others working in this repository
}

/** Approved AS-IS documents and how they compare with the monolith's base branch. */
export interface DocsPreview {
  repository: string;
  branch: string;
  latest_sha: string;
  folder: string;
  message: string;
  files: { path: string; status: "new" | "modified" | "unchanged"; changed_upstream: boolean; old: string | null; new: string | null }[];
}

/** Where a new migration works, for the New migration form: the feature branch, cut in each repository. */
export interface GitPlan {
  commit: boolean;
  feature_branch: string;
  repositories: PlanRepository[];
}

export interface PlanRepository {
  key: string; // "monolith" for the project's monolith
  name: string;
  url: string;
  root: boolean;
  base_branch: string;
  steps: string[]; // the steps that change code in it
  default_branch: string | null;
  feature_branch_exists: boolean | null;
  base_branch_exists: boolean | null;
}

/** One of a finished run's repositories, for the review. */
export interface RunRepository {
  key: string;
  name: string;
  url: string;
  base_branch: string | null;
  root: boolean;
  changed: boolean;
  pending: boolean; // code changes not committed yet
  delivery: RepoDelivery | null;
}

export type ConflictKind = "content" | "deleted_here" | "deleted_there" | "binary";

export interface ChangedFile {
  path: string;
  status: "A" | "M" | "D" | string;
  document: boolean;
  binary: boolean;
  upstream_changed: boolean;
  conflict: ConflictKind | null;
  additions: number | null;
  deletions: number | null;
  same_in_git?: boolean; // already on the feature branch in git exactly so: nothing new to commit
}

export interface RunChanges {
  key: string;
  name: string;
  repository: string;
  base_branch: string;
  feature_branch: string;
  base_sha: string;
  head_sha: string;
  latest_sha: string;
  behind: number;
  remote_branch: string | null;
  previous: RepoDelivery | null;
  warning: string | null;
  message: string;
  files: ChangedFile[]; // only files that differ from the feature branch in git
  unchanged_in_git?: number; // files the run changed that are already on the branch in git as they are: not listed
  conflicts: number;
}

export interface ChangedFileDetail extends ChangedFile {
  too_large: boolean;
  original: string | null; // where the run started
  ours: string | null; // the run's version
  theirs: string | null; // the latest base branch's, when it changed the file too
  merged: string | null; // the merge of the two, with conflict markers when they conflict
  in_git: boolean; // the run has pushed its feature branch: what this commit changes is relative to it
  pushed: string | null; // the file on the feature branch in git (null: not there yet)
}

export type Resolution = { content: string } | { use: "ours" | "theirs" } | { delete: true };

export interface AccountGit {
  name: string;
  email: string;
  default_name: string;
  credentials: { id: number; host: string; username: string; updated_at: string }[];
}

export interface StageExecution {
  id: number;
  stage: string;
  attempt: number;
  status: "running" | "completed" | "failed" | "awaiting_approval" | "approved" | "rejected";
  start_sha: string | null;
  end_sha: string | null;
  feedback: string | null;
  error: string | null;
  rerun?: boolean | null; // re-ran a completed step in place ("Re-run from here")
  started_at: string;
  finished_at: string | null;
}

export interface Approval {
  id: number;
  gate: string;
  decision: "approve" | "reject";
  target_stage: string | null;
  comments: string | null;
  reviewer: string;
  created_at: string;
}

export interface AgentInputs {
  paths: string[];
  notes: string;
}

export interface RunDetail extends Run {
  workspace: string | null;
  inputs: AgentInputs | null;
  stages: StageExecution[];
  approvals: Approval[];
  pending_gate: { gate: string; reviews: string[] } | null;
  pending_branches: PendingBranches | null; // awaiting_branches: confirm before a step changes code
  pending_publish: { stage: string; steps: string[]; files: string[]; repository: string } | null; // awaiting_publish
  pending_commit: { stage: string; stage_title: string; repository: { key: string; name: string; url: string } } | null; // awaiting_commit right after a step
  pending_rerun: { gate: string; target_stage: string; comments: string | null } | null; // rejection held for amendments
  base_sha: string | null; // the monolith commit the run started from
  monolith_behind: number | null; // commits the project's base branch has gained since (as last fetched)
  workflow_update: WorkflowUpdate | null; // the run's workflow changed since it started: what updating it would do
}

/** What bringing a waiting run up to date with its workflow would change (step titles), or why it can't. */
export interface WorkflowUpdate {
  added?: string[];
  removed?: string[];
  changed?: string[];
  missed?: string[]; // new steps before the point the run has reached: this run won't run them
  reordered?: boolean;
  blocked?: string; // the upcoming steps can't follow the workflow (a re-run still can)
  agents_changed?: string[]; // completed steps whose agent (or a shared text it uses) changed since they ran
  rerun?: { from: string; title: string; reasons: string[] }; // a re-run that brings the run up to date
}

/** The server's copy of a project's monolith repository, shared by its runs. */
export interface RepositoryStatus {
  url: string;
  branch: string;
  cloned: boolean;
  fetching: boolean;
  fetched_at: string | null;
  default_branch: string | null;
  head: { sha: string; subject: string; author: string; date: string } | null;
  error: string | null;
  legacy: { sha: string; branch: string; subject: string; at: string } | null; // the shared read-only copy new runs read
}

export interface ArtifactSummary {
  id: number;
  stage: string;
  attempt: number;
  name: string;
  path: string;
  kind: string;
  created_at: string;
  updated_at: string;
}

/** A text several agents' instructions include as a placeholder, `{{key}}`, filled in when a step starts. */
export interface SharedText {
  key: string;
  title: string;
  description: string;
  text: string;
  builtin: boolean; // shipped with the studio: it has a default to reset to
  customised: boolean; // differs from that default
  used_by: string[]; // titles of the agents whose instructions include it
  updated_by: string | null;
  updated_at: string | null;
}

/** Placeholders of shared texts, as the backend reads them (app/shared_texts.py PLACEHOLDER). */
const PLACEHOLDER = /\{\{\s*([a-z][a-z0-9_]*)\s*\}\}/g;

/** The shared-text keys the instructions include, each once, in order of first use. */
export function placeholders(instructions: string): string[] {
  return [...new Set([...instructions.matchAll(PLACEHOLDER)].map((m) => m[1]))];
}

/** The instructions with every known placeholder filled in (unknown ones are left as they are), as the agent gets them. */
export function expandPlaceholders(instructions: string, texts: Record<string, string>): string {
  return instructions.replace(PLACEHOLDER, (whole, key: string) => texts[key] ?? whole);
}

export interface Artifact extends ArtifactSummary {
  content: string;
}

export interface AuditEvent {
  id: number;
  stage: string | null;
  kind: "log" | "status" | "artifact" | "approval" | "error" | "question" | "answer";
  message: string;
  created_at: string;
}

export interface Question {
  id: number;
  stage: string;
  attempt: number;
  question: string;
  options: string[] | null;
  status: "open" | "answered" | "cancelled";
  answer: string | null;
  answered_by: string | null;
  created_at: string;
  answered_at: string | null;
}

export interface AgentInput {
  title: string;
  description: string;
  mode: "stub" | "llm";
  model: string;
  effort: string;
  jdk: "legacy" | "modern";
  instructions: string;
  tools: string[];
  outputs: string[];
  max_turns: number;
  checks: string[]; // e.g. "boundary"
  guardrails: string; // this agent's own, in plain words
}

export type Library = "shared" | "project"; // the library every project uses, or this project's own/copy

export interface Agent extends AgentInput {
  key: string;
  builtin: boolean;
  scope: Library;
  customised: boolean; // this project's copy of a shared agent
  created_at: string;
  updated_at: string;
}

/** Fields read from agent.md text; only those the text sets are present. */
export type ParsedAgentFile = Partial<AgentInput> & { instructions: string; warnings: string[] };

/** True when text starts with agent.md frontmatter (a pasted agent.md file). */
export const looksLikeAgentMd = (text: string) => /^\s*---[ \t]*\n[\s\S]*?\n---[ \t]*(\n|$)/.test(text);

export interface WorkflowStep {
  kind: "agent" | "gate";
  key: string;
  title?: string;
  description?: string;
  reviews?: string[];
  uses?: string[] | null; // agents: earlier agent steps whose output they get; null = all of them
  include_content?: boolean;
  code?: StepCode | null; // agents: the step changes code, in this repository; null = documents only
  publish_docs?: boolean; // agents: once approved, its documents are published to the monolith's docs/<domain>/<feature>
}

/** Where an agent step changes code: the project's monolith (repository ""), or another repository whose URL can
 * contain {domain} (e.g. each domain's microservice), from its base branch. */
export interface StepCode {
  repository: string;
  base_branch: string;
}

export interface Workflow {
  key: string;
  name: string;
  description: string;
  scope: Scope;
  steps: WorkflowStep[];
  stages: StageDef[];
  runs: number;
  library: Library;
  customised: boolean; // this project's copy of a shared workflow
  git: WorkflowGit;
  created_at: string;
  updated_at: string;
}

/** A workflow's code changes: review and commit them at the end; where; the feature branch's name. */
export interface WorkflowGit {
  commit: boolean;
  branch_pattern: string; // {domain} {feature} {run} {date}
}

export interface AgentCatalog {
  provider: "copilot" | "claude";
  models: string[];
  efforts: string[];
  tools: { name: string; description: string }[];
}

export interface Domain {
  key: string;
  name: string;
  description: string;
  context: string;
  agent_inputs: Record<string, AgentInputs>;
  default_workflow: string | null;
  owned_tables: string[] | null;
  owned_packages: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface DomainSummary extends Domain {
  runs: number; // migration runs
  completed: number;
  features: number;
}

export interface RunBrief {
  id: string;
  status: RunStatus;
  current_stage: string | null;
  created_at: string;
}

export interface FeatureInput {
  name?: string;
  description?: string;
  entry_points?: string[];
  tables?: string[];
  calls?: string[];
}

export interface Feature {
  key: string;
  domain: string;
  name: string;
  description: string;
  entry_points: string[];
  tables: string[];
  calls: string[]; // other domains it reaches into
  run_id: string | null; // discovery run that listed it; null when added by hand
  missing_from_latest: boolean; // kept because it is being migrated, though the latest list dropped it
  migration: RunBrief | null; // latest migration run
  created_at: string;
  updated_at: string;
}

export interface DiscoveryOverview {
  bounded_contexts: RunBrief | null;
  contexts_inputs: AgentInputs | null; // paths and notes the latest bounded-contexts run was given
  contexts_approved: boolean;
  domains: { key: string; name: string; features: number; run: RunBrief | null }[];
  not_found: string[]; // domains the latest approved bounded contexts did not include
}

/** What a run covers, for headings: the whole monolith, a domain's feature list, or one feature. */
export function runSubject(run: Pick<Run, "domain" | "operation">): string {
  if (run.domain === MONOLITH) return "Whole monolith · bounded contexts";
  if (run.operation === FEATURES_OP) return `${run.domain} · feature list`;
  return `${run.domain} / ${run.operation}`;
}

export interface DomainUpdate {
  name?: string;
  description?: string;
  context?: string;
  agent_inputs?: Record<string, AgentInputs>;
  default_workflow?: string;
  owned_tables?: string[];
  owned_packages?: string[];
}

export type ContractStatus = "proposed" | "approved" | "provided-by-monolith" | "provided-by-service" | "deprecated";
export const CONTRACT_STATUSES: ContractStatus[] = ["proposed", "approved", "provided-by-monolith", "provided-by-service", "deprecated"];

export interface Contract {
  key: string;
  provider: string;
  kind: "api" | "event" | "command";
  name: string;
  version: string;
  description: string;
  spec: string | null;
  spec_path: string | null;
  consumers: string[];
  status: ContractStatus;
  run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Dependency {
  id: number;
  run_id: string;
  interaction: string;
  source: string; // calls / consumes
  target: string; // provides
  kind: string;
  pattern: string;
  contract: string | null;
  status: string;
  summary: string;
  code_refs: string[];
  created_at: string;
  operation: string | null;
}

export interface DependencyMap {
  domains: string[];
  edges: { source: string; target: string; count: number; patterns: Record<string, number> }[];
  order: { domain: string; reason: string }[];
}

export interface OperationDocs {
  run: Run;
  artifacts: ArtifactSummary[];
}

export type Settings = Record<string, unknown>;

export interface CopilotStatus {
  available: boolean; // the Copilot runtime started
  signed_in: boolean;
  login: string | null;
  host: string | null;
  auth_type: string | null;
  message: string | null;
}

export interface LoginStep {
  kind: "awaiting" | "completed" | "error" | "input-required" | "needs-interaction" | "open-url";
  url: string | null;
  prompt: string | null;
  message: string | null;
  result: { status: "completed" | "declined" | "needs-plaintext-consent"; host: string | null; login: string | null } | null;
}

export type DatabaseDialect = "postgresql" | "oracle" | "sqlite";

/** The studio's own database connection. `database` is the PostgreSQL database name, the Oracle service name or the
 * SQLite file. The password is never sent back; `has_password` says whether one is saved. */
export interface DatabaseConfig {
  dialect: DatabaseDialect;
  host: string;
  port: number | null;
  database: string;
  username: string;
  password?: string;
  options: Record<string, string>;
  oracle_client?: string; // Oracle Instant Client folder: thick mode; empty: thin mode
  has_password?: boolean;
}

export interface DatabaseStatus {
  configured: boolean;
  source: "env" | "file" | "default" | null; // env: STUDIO_DATABASE_URL; default: the existing data/studio.db
  dialect: DatabaseDialect | null;
  config: DatabaseConfig | null;
  version: string | null;
  copied_rows?: number | null;
}

export interface Project {
  key: string;
  name: string;
  description: string;
  runs: number;
  members: number;
  my_role: string | null; // null: not a member (global project admins see every project)
  member: boolean; // only members can open a project and its runs
  permissions: Permission[]; // what the signed-in user may do in it
  created_at: string;
  updated_at: string;
}

export type ProjectSettings = Record<string, string | number | boolean>;

/** A built-in guardrail check and what it enforces (read-only: it lives in code). */
export type BuiltinGuardrail = { key: string; title: string; description: string; checks: string[] }; // checks: the agent checks it serves
export type Guardrails = { builtin: BuiltinGuardrail[] };

export interface ProjectDetail extends Project {
  settings: ProjectSettings;
  languages: Record<string, string>;
}

export interface Member {
  user_id: number;
  username: string;
  display_name: string;
  active: boolean;
  role: string;
  created_at: string;
}

export type Permission =
  | "agents.manage"
  | "workflows.manage"
  | "domains.manage"
  | "runs.start"
  | "runs.approve"
  | "runs.edit"
  | "project.manage"
  | "settings.manage"
  | "users.manage"
  | "projects.manage";

/** Permissions that only come from a user's own roles (not a project role): checked globally. */
export const GLOBAL_PERMISSIONS: Permission[] = ["settings.manage", "users.manage", "projects.manage"];

export interface Me {
  id: number;
  username: string;
  display_name: string;
  roles: string[];
  permissions: Permission[];
}

export interface AuthState {
  user: Me | null;
  needs_bootstrap: boolean; // no users yet: the first visitor creates the admin
}

export interface User {
  id: number;
  username: string;
  display_name: string;
  active: boolean;
  roles: string[];
  created_at: string;
  last_login_at: string | null;
  projects: { project: string; name: string; role: string }[]; // the projects they can open, with their role there
}

export interface Role {
  key: string;
  name: string;
  description: string;
  permissions: Permission[];
  locked: boolean; // Admin: always every permission
  users: number;
}

export interface PermissionInfo {
  key: Permission;
  label: string;
  description: string;
}

/** Fired when the backend says the session is gone, so the app shows the sign-in page. */
export const SIGNED_OUT_EVENT = "studio:signed-out";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let currentProject: string | null = null;

/** The project every request is about (sent as X-Project); set by the project switcher. */
export function setCurrentProject(key: string | null) {
  currentProject = key;
}

export function getCurrentProject(): string | null {
  return currentProject;
}

/** For URLs the browser fetches itself (EventSource, downloads), which can't carry the header. */
export function withProject(url: string): string {
  return currentProject ? `${url}${url.includes("?") ? "&" : "?"}project=${encodeURIComponent(currentProject)}` : url;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(currentProject ? { "X-Project": currentProject } : {}), ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

/** Save a blob as a file in the browser's downloads. */
export function saveFile(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Download a file the API serves (with the project header, so a plain link won't do), under the name it gives. */
async function download(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`/api${path}`, { headers: currentProject ? { "X-Project": currentProject } : {} });
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    throw new ApiError(res.status, res.statusText);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  saveFile(await res.blob(), name);
}

export const api = {
  runs: () => request<Run[]>("/runs"),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  createRun: (body: { domain: string; operation: string; workflow?: string; inputs?: AgentInputs; base_branches?: Record<string, string>; feature_branch?: string }) =>
    request<Run>("/runs", json("POST", body)),
  gitPlan: (domain: string, feature: string, workflow?: string) =>
    request<GitPlan>(`/runs-git-plan?${new URLSearchParams({ domain, feature, ...(workflow ? { workflow } : {}) })}`),
  runRepositories: (runId: string) => request<RunRepository[]>(`/runs/${runId}/repositories`),
  runChanges: (runId: string, repo: string) => request<RunChanges>(`/runs/${runId}/changes?${new URLSearchParams({ repo })}`),
  runChangeFile: (runId: string, repo: string, path: string, latest: string) =>
    request<ChangedFileDetail>(`/runs/${runId}/changes/file?${new URLSearchParams({ repo, path, latest })}`),
  commitRun: (runId: string, body: { repo: string; message: string; branch: string; files: string[]; resolutions: Record<string, Resolution>; latest_sha: string; head_sha: string }) =>
    request<RepoDelivery>(`/runs/${runId}/commit`, json("POST", body)),
  docsPreview: (runId: string) => request<DocsPreview>(`/runs/${runId}/docs`),
  publishDocs: (runId: string, body: { message: string; paths: string[]; latest_sha: string }) =>
    request<{ sha: string; branch: string }>(`/runs/${runId}/docs/publish`, json("POST", body)),
  skipDocs: (runId: string) => request<void>(`/runs/${runId}/docs/skip`, { method: "POST" }),
  confirmBranches: (runId: string, body: { feature_branch: string; base_branch?: string }) => request<Run>(`/runs/${runId}/branches`, json("POST", body)),
  finishRun: (runId: string) => request<void>(`/runs/${runId}/finish`, { method: "POST" }),
  accountGit: () => request<AccountGit>("/account/git"),
  setGitIdentity: (body: { name: string; email: string }) => request<AccountGit>("/account/git", json("PUT", body)),
  setGitCredential: (body: { host: string; username: string; token: string }) => request<AccountGit>("/account/git/credentials", json("PUT", body)),
  removeGitCredential: (id: number) => request<AccountGit>(`/account/git/credentials/${id}`, { method: "DELETE" }),
  testGit: (project: string) => request<{ ok: boolean; repository: string; with_token: boolean; error: string | null }>("/account/git/test", json("POST", { project })),
  decide: (id: string, body: { decision: "approve" | "reject"; comments?: string; target_stage?: string; rerun?: "now" | "later" }) =>
    request<Approval>(`/runs/${id}/decision`, json("POST", body)),
  rerun: (id: string, body: { comments?: string; target_stage?: string }) => request<Run>(`/runs/${id}/rerun`, json("POST", body)),
  backToReview: (id: string) => request<Run>(`/runs/${id}/back-to-review`, { method: "POST" }),
  updateRunInputs: (id: string, inputs: AgentInputs) => request<RunDetail>(`/runs/${id}/inputs`, json("PUT", inputs)),
  retry: (id: string, fresh = false) => request<Run>(`/runs/${id}/retry`, json("POST", { fresh })),
  updateRunWorkflow: (id: string, rerun = false) => request<Run>(`/runs/${id}/update-workflow`, json("POST", { rerun })),
  restart: (id: string, stage: string, notes?: string, fresh = false) => request<Run>(`/runs/${id}/restart`, json("POST", { stage, notes, fresh })),
  artifacts: (runId: string) => request<ArtifactSummary[]>(`/runs/${runId}/artifacts`),
  artifact: (id: number) => request<Artifact>(`/artifacts/${id}`),
  sharedTexts: () => request<SharedText[]>("/shared-texts"),
  updateSharedText: (key: string, text: string) => request<SharedText>(`/shared-texts/${key}`, json("PUT", { text })),
  resetSharedText: (key: string) => request<SharedText>(`/shared-texts/${key}/reset`, { method: "POST" }),
  downloadRunDocuments: (runId: string) => download(`/runs/${runId}/documents.zip`, "documents.zip"),
  downloadDomainDocuments: (key: string) => download(`/domains/${key}/documents.zip`, `${key}-documents.zip`),
  updateArtifact: (id: number, content: string) => request<Artifact>(`/artifacts/${id}`, json("PUT", { content })),
  diff: (runId: string, executionId: number) => request<{ diff: string }>(`/runs/${runId}/stages/${executionId}/diff`),
  questions: (runId: string) => request<Question[]>(`/runs/${runId}/questions`),
  answer: (questionId: number, answer: string) => request<Question>(`/questions/${questionId}/answer`, json("POST", { answer })),
  copilotStatus: () => request<CopilotStatus>("/copilot/status"),
  copilotLogin: (kind: "github" | "entra") => request<{ flow_id: string; step: LoginStep }>("/copilot/login", json("POST", { kind })),
  copilotLoginAdvance: (flowId: string, input?: string) => request<LoginStep>(`/copilot/login/${flowId}/advance`, json("POST", { input })),
  copilotLoginCancel: (flowId: string) => request<void>(`/copilot/login/${flowId}`, { method: "DELETE" }),
  copilotToken: (token: string, host: string) => request<CopilotStatus>("/copilot/token", json("POST", { token, host })),
  copilotLogout: () => request<CopilotStatus>("/copilot/logout", { method: "POST" }),
  settings: () => request<Settings>("/settings"),
  updateSettings: (values: Settings) => request<Settings>("/settings", json("PUT", values)),
  agents: () => request<Agent[]>("/agents"),
  agentCatalog: () => request<AgentCatalog>("/agents/catalog"),
  createAgent: (body: { key: string; title: string; description: string }, scope: Library = "shared") =>
    request<Agent>(`/agents?scope=${scope}`, json("POST", body)),
  updateAgent: (key: string, config: AgentInput, scope: Library = "shared") => request<Agent>(`/agents/${key}?scope=${scope}`, json("PUT", config)),
  resetAgent: (key: string) => request<Agent>(`/agents/${key}/reset`, { method: "POST" }),
  revertAgent: (key: string) => request<Agent>(`/agents/${key}/revert`, { method: "POST" }),
  deleteAgent: (key: string, scope: Library = "shared") => request<void>(`/agents/${key}?scope=${scope}`, { method: "DELETE" }),
  parseAgentMd: (content: string) => request<ParsedAgentFile>("/agents/parse-agent-md", json("POST", { content })),
  agentMdUrl: (key: string) => withProject(`/api/agents/${key}/agent.md`),
  renderAgentMd: (fields: AgentInput) => request<{ content: string }>("/agents/render-agent-md", json("POST", fields)),
  saveAgentMd: (key: string, content: string, scope: Library = "shared") =>
    request<{ agent: Agent; warnings: string[] }>(`/agents/${key}/agent-md?scope=${scope}`, json("PUT", { content })),
  workflows: () => request<Workflow[]>("/workflows"),
  createWorkflow: (body: { key: string; name: string; description: string; scope?: Scope; copy_from?: string; steps?: WorkflowStep[] }, library: Library = "project") =>
    request<Workflow>(`/workflows?library=${library}`, json("POST", body)),
  updateWorkflow: (key: string, body: { name?: string; description?: string; steps?: WorkflowStep[]; git?: WorkflowGit }, library: Library = "project") =>
    request<Workflow>(`/workflows/${key}?library=${library}`, json("PUT", body)),
  revertWorkflow: (key: string) => request<Workflow>(`/workflows/${key}/revert`, { method: "POST" }),
  deleteWorkflow: (key: string, library: Library = "project") => request<void>(`/workflows/${key}?library=${library}`, { method: "DELETE" }),
  domains: () => request<DomainSummary[]>("/domains"),
  domain: (key: string) => request<Domain>(`/domains/${key}`),
  createDomain: (body: { key: string; name: string; description: string }) => request<Domain>("/domains", json("POST", body)),
  updateDomain: (key: string, body: DomainUpdate) => request<Domain>(`/domains/${key}`, json("PUT", body)),
  deleteDomain: (key: string, withRuns = false) => request<void>(`/domains/${key}${withRuns ? "?with_runs=true" : ""}`, { method: "DELETE" }),
  domainDocs: (key: string) => request<OperationDocs[]>(`/domains/${key}/docs`),
  domainDependencies: (key: string) => request<Dependency[]>(`/domains/${key}/dependencies`),
  dependencyMap: () => request<DependencyMap>("/domains/dependency-map"),
  features: (domain: string) => request<Feature[]>(`/domains/${domain}/features`),
  addFeature: (domain: string, body: FeatureInput & { key: string; name: string }) =>
    request<Feature>(`/domains/${domain}/features`, json("POST", body)),
  updateFeature: (domain: string, key: string, body: FeatureInput) =>
    request<Feature>(`/domains/${domain}/features/${encodeURIComponent(key)}`, json("PUT", body)),
  deleteFeature: (domain: string, key: string) =>
    request<void>(`/domains/${domain}/features/${encodeURIComponent(key)}`, { method: "DELETE" }),
  discovery: () => request<DiscoveryOverview>("/discovery"),
  discoverBoundedContexts: (inputs?: AgentInputs) => request<Run>("/discovery/bounded-contexts", json("POST", { inputs })),
  discoverFeatures: (domain: string) => request<Run>(`/domains/${domain}/discover-features`, { method: "POST" }),
  discoverAllFeatures: () => request<Run[]>("/discovery/features", { method: "POST" }),
  contracts: (domain?: string) => request<Contract[]>(`/contracts${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`),
  updateContract: (key: string, status: ContractStatus) => request<Contract>(`/contracts/${encodeURIComponent(key)}`, json("PUT", { status })),
  me: () => request<AuthState>("/auth/me"),
  projects: () => request<Project[]>("/projects"),
  project: (key: string) => request<ProjectDetail>(`/projects/${key}`),
  createProject: (body: { key: string; name: string; description: string; settings: ProjectSettings }) =>
    request<ProjectDetail>("/projects", json("POST", body)),
  updateProject: (key: string, body: { name?: string; description?: string }) => request<ProjectDetail>(`/projects/${key}`, json("PUT", body)),
  deleteProject: (key: string) => request<void>(`/projects/${key}`, { method: "DELETE" }),
  updateProjectSettings: (key: string, values: ProjectSettings) => request<ProjectSettings>(`/projects/${key}/settings`, json("PUT", values)),
  guardrails: () => request<Guardrails>("/guardrails"),
  repository: (key: string) => request<RepositoryStatus>(`/projects/${key}/repository`),
  fetchRepository: (key: string) => request<RepositoryStatus>(`/projects/${key}/repository/fetch`, { method: "POST" }),
  members: (key: string) => request<Member[]>(`/projects/${key}/members`),
  candidates: (key: string) => request<{ id: number; username: string; display_name: string }[]>(`/projects/${key}/candidates`),
  setMember: (key: string, userId: number, role: string) => request<Member[]>(`/projects/${key}/members/${userId}`, json("PUT", { role })),
  removeMember: (key: string, userId: number) => request<void>(`/projects/${key}/members/${userId}`, { method: "DELETE" }),
  login: (username: string, password: string) => request<{ user: Me }>("/auth/login", json("POST", { username, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  bootstrap: (body: { username: string; display_name: string; password: string }) => request<{ user: Me }>("/auth/bootstrap", json("POST", body)),
  changePassword: (current_password: string, new_password: string) => request<void>("/auth/password", json("PUT", { current_password, new_password })),
  users: () => request<User[]>("/users"),
  createUser: (body: { username: string; display_name: string; password: string; roles: string[] }) => request<User>("/users", json("POST", body)),
  updateUser: (id: number, body: { display_name?: string; active?: boolean; roles?: string[] }) => request<User>(`/users/${id}`, json("PUT", body)),
  resetPassword: (id: number, password: string) => request<void>(`/users/${id}/password`, json("PUT", { password })),
  deleteUser: (id: number) => request<void>(`/users/${id}`, { method: "DELETE" }),
  roles: () => request<Role[]>("/roles"),
  createRole: (body: { key: string; name: string; description: string; permissions: Permission[] }) => request<Role>("/roles", json("POST", body)),
  updateRole: (key: string, body: { name: string; description: string; permissions: Permission[] }) => request<Role>(`/roles/${key}`, json("PUT", body)),
  deleteRole: (key: string) => request<void>(`/roles/${key}`, { method: "DELETE" }),
  permissions: () => request<PermissionInfo[]>("/permissions"),
  databaseStatus: () => request<DatabaseStatus>("/database"),
  testDatabase: (config: DatabaseConfig) => request<{ ok: boolean; version: string }>("/database/test", json("POST", config)),
  configureDatabase: (body: { config: DatabaseConfig; copy_data: boolean; replace: boolean }) =>
    request<DatabaseStatus>("/database", json("POST", body)),
};
