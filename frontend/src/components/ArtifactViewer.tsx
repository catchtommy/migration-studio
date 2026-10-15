import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { SpecView } from "./SpecView";

export function ArtifactViewer({ artifactId, editable }: { artifactId: number; editable: boolean }) {
  const queryClient = useQueryClient();
  const { data: artifact, isLoading } = useQuery({ queryKey: ["artifact", artifactId], queryFn: () => api.artifact(artifactId) });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => setEditing(false), [artifactId]);

  const save = useMutation({
    mutationFn: () => api.updateArtifact(artifactId, draft),
    onSuccess: (updated) => {
      queryClient.setQueryData(["artifact", artifactId], updated);
      setEditing(false);
    },
  });

  if (isLoading || !artifact) return <p className="text-sm text-subtle">Loading artifact…</p>;

  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <code className="truncate text-xs text-subtle">{artifact.path}</code>
        {editable && !editing && (
          <button
            className="text-xs font-medium text-accent hover:underline"
            onClick={() => {
              setDraft(artifact.content);
              setEditing(true);
            }}
          >
            Edit
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2 p-3">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="h-96 w-full rounded-md border border-border bg-bg p-3 font-mono text-xs"
          />
          {save.error && <p className="text-xs text-bad">{(save.error as Error).message}</p>}
          <div className="flex justify-end gap-2">
            <button className="rounded-md px-3 py-1.5 text-sm hover:bg-muted" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={save.isPending}
              onClick={() => save.mutate()}
            >
              Save &amp; commit
            </button>
          </div>
        </div>
      ) : artifact.kind === "markdown" ? (
        <article className="prose prose-sm max-w-none p-4 dark:prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
        </article>
      ) : (
        <SpecView content={artifact.content} yaml={<pre className="overflow-x-auto p-4 font-mono text-xs">{artifact.content}</pre>} />
      )}
    </div>
  );
}
