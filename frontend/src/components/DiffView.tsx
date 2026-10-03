import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

function lineClass(line: string) {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-subtle";
  if (line.startsWith("+")) return "bg-ok/10 text-ok";
  if (line.startsWith("-")) return "bg-bad/10 text-bad";
  if (line.startsWith("@@")) return "text-accent";
  if (line.startsWith("diff ")) return "mt-3 font-semibold";
  return "";
}

export function DiffView({ runId, executionId }: { runId: string; executionId: number }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["diff", runId, executionId],
    queryFn: () => api.diff(runId, executionId),
    retry: false,
  });
  if (isLoading) return <p className="text-sm text-subtle">Loading diff…</p>;
  if (error) return <p className="text-sm text-subtle">{(error as Error).message}</p>;
  if (!data?.diff) return <p className="text-sm text-subtle">No file changes in this attempt.</p>;
  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-surface p-3 font-mono text-xs leading-5">
      {data.diff.split("\n").map((line, i) => (
        <div key={i} className={lineClass(line)}>
          {line || " "}
        </div>
      ))}
    </pre>
  );
}
