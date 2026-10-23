import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Markdown } from "./Markdown";
import { api, saveFile } from "../lib/api";
import { DownloadButton } from "./DownloadButton";
import { SpecView } from "./SpecView";
import { ErrorBoundary } from "./ErrorBoundary";
import { parseYaml } from "./YamlView";

const isYaml = (name: string) => /\.ya?ml$/i.test(name);

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
      queryClient.invalidateQueries({ queryKey: ["artifacts"] });
      setEditing(false);
    },
  });

  if (isLoading || !artifact) return <p className="text-sm text-subtle">Loading artifact…</p>;

  return (
    <div className="card min-w-0 max-w-full">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <code className="truncate text-xs text-subtle">{artifact.path}</code>
        <div className="flex shrink-0 items-center gap-3">
          <DownloadButton
            label="Download"
            title={`Download ${artifact.name}`}
            onDownload={() => saveFile(new Blob([artifact.content], { type: artifact.kind === "markdown" ? "text/markdown" : "text/plain" }), artifact.name)}
          />
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
      </div>
      {editing ? (
        <div className="space-y-2 p-3">
          <textarea
            aria-label={`Edit ${artifact.name}`}
            value={draft}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            className="h-[32rem] w-full resize-y rounded-md border border-border bg-bg p-3 font-mono text-xs leading-5"
          />
          {isYaml(artifact.name) && <YamlCheck text={draft} />}
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
      ) : (
        // A document a viewer can't render (a malformed spec, say) shows as text instead of breaking the page; Edit stays.
        <ErrorBoundary key={artifact.content} fallback={(error) => <Unrendered text={artifact.content} error={error} editable={editable} />}>
          {artifact.kind === "markdown" ? (
            <Markdown className="p-4">{artifact.content}</Markdown>
          ) : (
            <div className="max-w-full overflow-x-auto">
              <SpecView content={artifact.content} yaml={<pre className="max-w-full overflow-x-auto p-4 font-mono text-xs">{artifact.content}</pre>} />
            </div>
          )}
        </ErrorBoundary>
      )}
    </div>
  );
}

/** The document as text, when its formatted view failed: what went wrong, and that Edit can fix it. */
function Unrendered({ text, error, editable }: { text: string; error: Error; editable: boolean }) {
  return (
    <div>
      <p className="border-b border-warn/40 bg-warn/10 px-4 py-2 text-xs text-warn">
        This document can't be shown formatted ({error.message.split("\n")[0]}). Its text is below{editable ? "; use Edit above to fix it" : ""}.
      </p>
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap p-4 font-mono text-xs">{text}</pre>
    </div>
  );
}

/** While a YAML document is edited: whether it parses, and where it doesn't. Saving is allowed either way. */
function YamlCheck({ text }: { text: string }) {
  const parsed = parseYaml(text);
  if (parsed && "error" in parsed)
    return (
      <p role="status" className="rounded-md bg-warn/10 px-3 py-1.5 text-xs text-warn">
        Not valid YAML yet: {parsed.error}. You can still save it, but steps and checks that read this file will fail on it.
      </p>
    );
  return (
    <p role="status" className="text-xs text-ok">
      Valid YAML
    </p>
  );
}
