import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** One colour-coded part of the warning: red what is deleted, amber what is stopped first, green what is kept, grey
 * what is out of the studio's reach. */
export interface DangerSection {
  tone: "bad" | "warn" | "ok" | "neutral";
  title: string;
  items?: ReactNode[];
  text?: ReactNode;
}

const TONES: Record<DangerSection["tone"], { box: string; title: string }> = {
  bad: { box: "border-bad/50 bg-bad/10", title: "text-bad" },
  warn: { box: "border-warn/50 bg-warn/10", title: "text-warn" },
  ok: { box: "border-ok/50 bg-ok/10", title: "text-ok" },
  neutral: { box: "border-border bg-muted", title: "" },
};

/** A sharp warning before something that can't be undone: what happens, part by part in colour, and `confirmText`
 * to be typed before the red button unlocks. Drawn at the page's top level, so nothing around the button that opened
 * it (a table cell's no-wrap, say) bleeds into it. */
export function DangerDialog({ title, subtitle = "Permanent. This can't be undone.", sections, confirmText, actionLabel, pendingLabel, pending, error, onConfirm, onClose, blocked, checking }: {
  title: string;
  subtitle?: string;
  sections: DangerSection[];
  /** Why it can't be done yet: shown instead of the warning, with only a Close button. */
  blocked?: ReactNode;
  /** What it affects is still being looked up: nothing can be confirmed yet. */
  checking?: boolean;
  confirmText: string;
  actionLabel: string;
  pendingLabel: string;
  pending: boolean;
  error?: unknown;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !pending && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pending]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center whitespace-normal bg-black/50 p-4 text-left font-sans"
      role="dialog"
      aria-modal="true"
      aria-labelledby="danger-title"
    >
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto break-words rounded-xl border-2 border-bad bg-surface text-fg shadow-2xl">
        <header className="flex items-start gap-3 rounded-t-[10px] bg-bad px-5 py-4 text-white">
          <span aria-hidden className="text-2xl leading-none">
            ⚠
          </span>
          <div>
            <h2 id="danger-title" className="text-base font-semibold">
              {title}
            </h2>
            <p className="text-sm text-white/90">{subtitle}</p>
          </div>
        </header>

        <div className="space-y-3 p-5 text-sm">
          {checking && <p className="text-subtle">Checking what uses it…</p>}
          {blocked && (
            <section className="rounded-lg border border-bad/50 bg-bad/10 p-3">
              <h3 className="mb-1 font-semibold text-bad">Can't be deleted yet</h3>
              <p>{blocked}</p>
            </section>
          )}
          {!checking && !blocked && sections.map((s) => (
            <section key={s.title} className={`rounded-lg border p-3 ${TONES[s.tone].box}`}>
              <h3 className={`mb-1 font-semibold ${TONES[s.tone].title}`}>{s.title}</h3>
              {s.items && (
                <ul className="list-disc space-y-0.5 pl-5">
                  {s.items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              )}
              {s.text && <p className={s.tone === "neutral" ? "text-subtle" : ""}>{s.text}</p>}
            </section>
          ))}

          {!checking && !blocked && <label className="block">
            <span className="mb-1 block text-xs font-medium">
              Type <code className="rounded bg-bad/10 px-1 font-mono text-bad">{confirmText}</code> to confirm
            </span>
            <input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className="w-full rounded-md border-2 border-bad/40 bg-surface px-2 py-1.5 font-mono focus:border-bad focus:outline-none"
            />
          </label>}
          {!!error && <p className="font-medium text-bad">{(error as Error).message}</p>}
        </div>

        <footer className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <button type="button" disabled={pending} onClick={onClose} className="rounded-md px-3 py-1.5 text-sm hover:bg-muted">
            {blocked ? "Close" : "Cancel"}
          </button>
          {!blocked && <button
            type="button"
            disabled={typed !== confirmText || pending || checking}
            onClick={onConfirm}
            className="rounded-md bg-bad px-3 py-1.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {pending ? pendingLabel : actionLabel}
          </button>}
        </footer>
      </div>
    </div>,
    document.body,
  );
}
