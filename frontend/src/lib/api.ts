export type RunStatus = "pending" | "running" | "awaiting_input" | "awaiting_approval" | "completed" | "failed";

export interface StageDef {
  key: string;
  title: string;
  kind: "agent" | "gate";
  description: string;
  reviews: string[];
  uses?: string[] | null;
  include_content?: boolean;
}

export interface Run {
  id: string;
  domain: string;
  operation: string;
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
}

export interface Agent extends AgentInput {
  key: string;
  builtin: boolean;
  created_at: string;
  updated_at: string;
}

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
  steps: WorkflowStep[];
  stages: StageDef[];
  runs: number;
  created_at: string;
  updated_at: string;
}

export interface AgentCatalog {
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
  created_at: string;
  updated_at: string;
}

export interface DomainSummary extends Domain {
  runs: number;
  completed: number;
}

export interface DomainUpdate {
  name?: string;
  description?: string;
  context?: string;
  agent_inputs?: Record<string, AgentInputs>;
  default_workflow?: string;
}

export interface OperationDocs {
  run: Run;
  artifacts: ArtifactSummary[];
}

export type Settings = Record<string, unknown>;

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
  decide: (id: string, body: { decision: "approve" | "reject"; comments?: string; target_stage?: string; reviewer: string }) =>
    request<Approval>(`/runs/${id}/decision`, json("POST", body)),
  retry: (id: string) => request<Run>(`/runs/${id}/retry`, { method: "POST" }),
  artifacts: (runId: string) => request<ArtifactSummary[]>(`/runs/${runId}/artifacts`),
  artifact: (id: number) => request<Artifact>(`/artifacts/${id}`),
  updateArtifact: (id: number, content: string) => request<Artifact>(`/artifacts/${id}`, json("PUT", { content })),
  diff: (runId: string, executionId: number) => request<{ diff: string }>(`/runs/${runId}/stages/${executionId}/diff`),
  questions: (runId: string) => request<Question[]>(`/runs/${runId}/questions`),
  answer: (questionId: number, answer: string, answered_by: string) =>
    request<Question>(`/questions/${questionId}/answer`, json("POST", { answer, answered_by })),
  settings: () => request<Settings>("/settings"),
  updateSettings: (values: Settings) => request<Settings>("/settings", json("PUT", values)),
  agents: () => request<Agent[]>("/agents"),
  agentCatalog: () => request<AgentCatalog>("/agents/catalog"),
  createAgent: (body: { key: string; title: string; description: string }) => request<Agent>("/agents", json("POST", body)),
  updateAgent: (key: string, config: AgentInput) => request<Agent>(`/agents/${key}`, json("PUT", config)),
  resetAgent: (key: string) => request<Agent>(`/agents/${key}/reset`, { method: "POST" }),
  deleteAgent: (key: string) => request<void>(`/agents/${key}`, { method: "DELETE" }),
  workflows: () => request<Workflow[]>("/workflows"),
  createWorkflow: (body: { key: string; name: string; description: string; copy_from?: string; steps?: WorkflowStep[] }) =>
    request<Workflow>("/workflows", json("POST", body)),
  updateWorkflow: (key: string, body: { name?: string; description?: string; steps?: WorkflowStep[] }) =>
    request<Workflow>(`/workflows/${key}`, json("PUT", body)),
  deleteWorkflow: (key: string) => request<void>(`/workflows/${key}`, { method: "DELETE" }),
  domains: () => request<DomainSummary[]>("/domains"),
  domain: (key: string) => request<Domain>(`/domains/${key}`),
  createDomain: (body: { key: string; name: string; description: string }) => request<Domain>("/domains", json("POST", body)),
  updateDomain: (key: string, body: DomainUpdate) => request<Domain>(`/domains/${key}`, json("PUT", body)),
  deleteDomain: (key: string) => request<void>(`/domains/${key}`, { method: "DELETE" }),
  domainDocs: (key: string) => request<OperationDocs[]>(`/domains/${key}/docs`),
};
