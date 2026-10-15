import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAgents, useCan } from "../lib/hooks";

/** Which agent a run will use and whether it really reads the code: Stub agents only write sample results. */
export function AgentModeNote({ agentKey }: { agentKey: string }) {
  const queryClient = useQueryClient();
  const { data: agents } = useAgents();
  const agent = agents?.find((a) => a.key === agentKey);
  const canManage = useCan("agents.manage");
  const toAi = useMutation({
    mutationFn: () => api.updateAgent(agentKey, { ...agent!, mode: "llm" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
  if (!agent) return null;

  if (agent.mode === "llm")
    return (
      <p className="text-xs text-subtle">
        Agent:{" "}
        {canManage ? (
          <Link to={`/agents/${agent.key}`} className="text-accent hover:underline">
            {agent.title}
          </Link>
        ) : (
          agent.title
        )}{" "}
        (AI, {agent.model === "auto" ? "model chosen by the provider" : agent.model}).
      </p>
    );
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm" role="note">
      <span className="flex-1">
        The <strong>{agent.title}</strong> agent is in <strong>Stub</strong> mode: it writes sample results without reading your code.
      </span>
      {canManage && (
        <>
          <button onClick={() => toAi.mutate()} disabled={toAi.isPending} className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white disabled:opacity-50">
            Switch to AI
          </button>
          <Link to={`/agents/${agent.key}`} className="text-xs text-accent hover:underline">
            Edit agent
          </Link>
        </>
      )}
      {toAi.error && <span className="w-full text-xs text-bad">{(toAi.error as Error).message}</span>}
    </div>
  );
}
