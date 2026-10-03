import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuditEvent } from "./api";

export function useAgents() {
  return useQuery({ queryKey: ["agents"], queryFn: api.agents });
}

export function useWorkflows() {
  return useQuery({ queryKey: ["workflows"], queryFn: api.workflows });
}

export function useDomains() {
  return useQuery({ queryKey: ["domains"], queryFn: api.domains });
}

const REVIEWER_KEY = "migration-studio.reviewer";

/** The reviewer name recorded on approvals; remembered per browser. */
export function useReviewer(): [string, (name: string) => void] {
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem(REVIEWER_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const update = (value: string) => {
    setName(value);
    try {
      localStorage.setItem(REVIEWER_KEY, value);
    } catch {
      /* storage unavailable: keep in memory only */
    }
  };
  return [name, update];
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
