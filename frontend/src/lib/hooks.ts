import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuditEvent, type Permission } from "./api";

export function useCopilotStatus() {
  return useQuery({ queryKey: ["copilotStatus"], queryFn: api.copilotStatus, staleTime: 60_000 });
}

export function useAgents() {
  return useQuery({ queryKey: ["agents"], queryFn: api.agents });
}

export function useWorkflows() {
  return useQuery({ queryKey: ["workflows"], queryFn: api.workflows });
}

export function useDomains() {
  return useQuery({ queryKey: ["domains"], queryFn: api.domains });
}

/** The signed-in user (null while signed out). */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
}

/** Whether the signed-in user has a permission. The backend enforces it too; this only shapes the UI. */
export function useCan(permission: Permission): boolean {
  const { data } = useMe();
  return !!data?.user?.permissions.includes(permission);
}

/** Streams the run's audit log over SSE and refreshes run data whenever something meaningful happens. */
export function useRunEvents(runId: string) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    setEvents([]);
    const source = new EventSource(`/api/runs/${runId}/events`);
    source.addEventListener("audit", (e) => {
      const event: AuditEvent = JSON.parse((e as MessageEvent).data);
      setEvents((prev) => (prev.some((p) => p.id === event.id) ? prev : [...prev, event]));
      if (event.kind !== "log") {
        queryClient.invalidateQueries({ queryKey: ["run", runId] });
        queryClient.invalidateQueries({ queryKey: ["artifacts", runId] });
        queryClient.invalidateQueries({ queryKey: ["runs"] });
        queryClient.invalidateQueries({ queryKey: ["questions", runId] });
      }
    });
    return () => source.close();
  }, [runId, queryClient]);

  return events;
}
