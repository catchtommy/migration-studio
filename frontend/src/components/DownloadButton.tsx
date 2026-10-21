import { useState } from "react";

/** A small button that downloads a file, showing progress and any error. */
export function DownloadButton({ label, title, onDownload }: { label: string; title?: string; onDownload: () => void | Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await onDownload();
    } catch (e) {
      setError((e as Error).message || "Download failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" title={title} className="text-xs font-medium text-accent hover:underline disabled:opacity-50" disabled={busy} onClick={run}>
        {busy ? "Downloading…" : label}
      </button>
      {error && <span className="text-xs text-bad">{error}</span>}
    </span>
  );
}
