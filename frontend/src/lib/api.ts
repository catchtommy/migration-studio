export type RunStatus = "pending" | "running" | "awaiting_input" | "awaiting_approval" | "changes_requested" | "completed" | "failed";

export interface StageDef {
  key: string;
  title: string;
  kind: "agent" | "gate";
  description: string;
  reviews: string[];
  uses?: string[] | null;
  include_content?: boolean;
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
  branch: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
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
  pending_rerun: { gate: string; target_stage: string; comments: string | null } | null; // rejection held for amendments
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
}

export interface Agent extends AgentInput {
  key: string;
  builtin: boolean;
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
}

export interface Workflow {
  key: string;
  name: string;
  description: string;
  scope: Scope;
  steps: WorkflowStep[];
  stages: StageDef[];
  runs: number;
  created_at: string;
  updated_at: string;
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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const api = {
  runs: () => request<Run[]>("/runs"),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  createRun: (body: { domain: string; operation: string; workflow?: string; inputs?: AgentInputs }) => request<Run>("/runs", json("POST", body)),
  decide: (id: string, body: { decision: "approve" | "reject"; comments?: string; target_stage?: string; reviewer: string; rerun?: "now" | "later" }) =>
    request<Approval>(`/runs/${id}/decision`, json("POST", body)),
  rerun: (id: string, body: { comments?: string; target_stage?: string }) => request<Run>(`/runs/${id}/rerun`, json("POST", body)),
  backToReview: (id: string) => request<Run>(`/runs/${id}/back-to-review`, { method: "POST" }),
  updateRunInputs: (id: string, inputs: AgentInputs) => request<RunDetail>(`/runs/${id}/inputs`, json("PUT", inputs)),
  retry: (id: string) => request<Run>(`/runs/${id}/retry`, { method: "POST" }),
  artifacts: (runId: string) => request<ArtifactSummary[]>(`/runs/${runId}/artifacts`),
  artifact: (id: number) => request<Artifact>(`/artifacts/${id}`),
  updateArtifact: (id: number, content: string) => request<Artifact>(`/artifacts/${id}`, json("PUT", { content })),
  diff: (runId: string, executionId: number) => request<{ diff: string }>(`/runs/${runId}/stages/${executionId}/diff`),
  questions: (runId: string) => request<Question[]>(`/runs/${runId}/questions`),
  answer: (questionId: number, answer: string, answered_by: string) =>
    request<Question>(`/questions/${questionId}/answer`, json("POST", { answer, answered_by })),
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
  createAgent: (body: { key: string; title: string; description: string }) => request<Agent>("/agents", json("POST", body)),
  updateAgent: (key: string, config: AgentInput) => request<Agent>(`/agents/${key}`, json("PUT", config)),
  resetAgent: (key: string) => request<Agent>(`/agents/${key}/reset`, { method: "POST" }),
  deleteAgent: (key: string) => request<void>(`/agents/${key}`, { method: "DELETE" }),
  parseAgentMd: (content: string) => request<ParsedAgentFile>("/agents/parse-agent-md", json("POST", { content })),
  agentMdUrl: (key: string) => `/api/agents/${key}/agent.md`,
  renderAgentMd: (fields: AgentInput) => request<{ content: string }>("/agents/render-agent-md", json("POST", fields)),
  saveAgentMd: (key: string, content: string) =>
    request<{ agent: Agent; warnings: string[] }>(`/agents/${key}/agent-md`, json("PUT", { content })),
  workflows: () => request<Workflow[]>("/workflows"),
  createWorkflow: (body: { key: string; name: string; description: string; scope?: Scope; copy_from?: string; steps?: WorkflowStep[] }) =>
    request<Workflow>("/workflows", json("POST", body)),
  updateWorkflow: (key: string, body: { name?: string; description?: string; steps?: WorkflowStep[] }) =>
    request<Workflow>(`/workflows/${key}`, json("PUT", body)),
  deleteWorkflow: (key: string) => request<void>(`/workflows/${key}`, { method: "DELETE" }),
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
};
