import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getCurrentProject, GLOBAL_PERMISSIONS, withProject, type AuditEvent, type Permission, type Project, type RunDetail } from "./api";

/** Agents, workflows, domains and the project change when someone edits them (their pages refresh them then): read
 * again at most every 30 s otherwise, not on every page they're used on. */
const LIBRARY_STALE_MS = 30_000;

export function useCopilotStatus(enabled = true) {
  return useQuery({ queryKey: ["copilotStatus"], queryFn: api.copilotStatus, staleTime: 60_000, enabled });
}

export function useAgents() {
  return useQuery({ queryKey: ["agents"], queryFn: api.agents, staleTime: LIBRARY_STALE_MS });
}

export function useWorkflows() {
  return useQuery({ queryKey: ["workflows"], queryFn: api.workflows, staleTime: LIBRARY_STALE_MS });
}

export function useDomains() {
  return useQuery({ queryKey: ["domains"], queryFn: api.domains, staleTime: LIBRARY_STALE_MS });
}

/** The signed-in user (null while signed out). */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: api.projects, staleTime: 60_000 });
}

/** The project the app is working in (see the project switcher). */
export function useCurrentProject(): Project | undefined {
  const { data } = useProjects();
  const key = getCurrentProject();
  return data?.find((p) => p.key === key);
}

/** Whether the signed-in user has a permission: in the current project (their roles plus their role there), or for
 * global ones (users, global settings, projects) from their own roles. The backend enforces it too; this only shapes
 * the UI. */
export function useCan(permission: Permission): boolean {
  const { data } = useMe();
  const project = useCurrentProject();
  if (GLOBAL_PERMISSIONS.includes(permission)) return !!data?.user?.permissions.includes(permission);
  return !!project?.permissions.includes(permission);
}

/** Whether the signed-in user has the Admin role itself: what can't be undone (deleting a project with its runs,
 * starting a feature over with another workflow) is the admin's alone. */
export function useIsAdmin(): boolean {
  const { data } = useMe();
  return !!data?.user?.roles.includes("admin");
}

/** A global permission from the user's own roles, e.g. to change the shared agent library. */
export function useCanGlobally(permission: Permission): boolean {
  const { data } = useMe();
  return !!data?.user?.permissions.includes(permission);
}

/** How often streamed events are shown and checked for a refresh: one render and at most one refetch per tick. */
export const RUN_EVENTS_FLUSH_MS = 250;

/** Streams the run's audit log over SSE and refreshes run data when something meaningful happened since the run was
 * loaded. The stored log is replayed first (for the activity log): those events never cause refetches by themselves.
 * A refresh happens only when a non-log event is newer than the newest event the loaded run includes
 * (RunDetail.last_event_id), at most once per tick and never while the run is being fetched, so a long history or a
 * burst of events costs one request instead of one per event. */
export function useRunEvents(runId: string) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    setEvents([]);
    const seen = new Set<number>();
    let pending: AuditEvent[] = [];
    let newestChange = 0; // id of the newest non-log event received

    const flush = () => {
      if (pending.length) {
        const batch = pending;
        pending = [];
        setEvents((prev) => [...prev, ...batch]);
      }
      const state = queryClient.getQueryState<RunDetail>(["run", runId]);
      if (!state?.data || state.fetchStatus === "fetching") return;
      if (newestChange > (state.data.last_event_id ?? 0)) {
        const opts = { cancelRefetch: false } as const;
        queryClient.invalidateQueries({ queryKey: ["run", runId] }, opts);
        queryClient.invalidateQueries({ queryKey: ["artifacts", runId] }, opts);
        queryClient.invalidateQueries({ queryKey: ["questions", runId] }, opts);
        queryClient.invalidateQueries({ queryKey: ["runs"] }, opts);
      }
    };
    const timer = window.setInterval(flush, RUN_EVENTS_FLUSH_MS);

    const source = new EventSource(withProject(`/api/runs/${runId}/events`));
    source.addEventListener("audit", (e) => {
      const event: AuditEvent = JSON.parse((e as MessageEvent).data);
      if (seen.has(event.id)) return;
      seen.add(event.id);
      pending.push(event);
      if (event.kind !== "log") newestChange = Math.max(newestChange, event.id);
    });
    return () => {
      window.clearInterval(timer);
      source.close();
    };
  }, [runId, queryClient]);

  return events;
}

/** Re-fetches a run (and the given related queries) after an action on it, resolving once the new state is in. Return
 * it from a mutation's onSuccess so the button stays busy until the page shows the result, and can't be pressed twice
 * in between. */
export function useRefreshRun(runId: string) {
  const queryClient = useQueryClient();
  return (...also: string[]) =>
    Promise.all([["run", runId], ...also.map((k) => [k, runId])].map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

/** The project's default migration workflow (Project settings), as the server falls back to it: its key when it is a
 * migration workflow of this project, else the Standard migration. A domain's own default comes before it. */
export function useProjectDefaultWorkflow(): string | undefined {
  const project = getCurrentProject();
  const { data } = useQuery({ queryKey: ["project", project], queryFn: () => api.project(project!), enabled: !!project, staleTime: LIBRARY_STALE_MS });
  const { data: workflows = [] } = useWorkflows();
  const migration = workflows.filter((w) => w.scope === "operation");
  const chosen = data?.settings.default_workflow as string | undefined;
  return [chosen, "standard"].find((k) => k && migration.some((w) => w.key === k)) ?? migration[0]?.key;
}

/** The workflow a new migration of a domain preselects: the domain's default, else the project's. */
export function defaultLabel(key: string, domainDefault: string | null | undefined, projectDefault: string | undefined): string {
  if (key === domainDefault) return " (domain default)";
  return key === projectDefault ? " (project default)" : "";
}
