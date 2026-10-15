import { lazy, Suspense, useState } from "react";

const SwaggerView = lazy(() => import("./SwaggerView"));
const AsyncApiView = lazy(() => import("./AsyncApiView"));

export type SpecKind = "openapi" | "asyncapi";

/** OpenAPI (or Swagger 2) and AsyncAPI documents declare themselves with a top-level key. */
export function specKind(text: string): SpecKind | null {
  if (/^(openapi|swagger)\s*:/m.test(text)) return "openapi";
  if (/^asyncapi\s*:/m.test(text)) return "asyncapi";
  return null;
}

const LABELS: Record<SpecKind, string> = { openapi: "Swagger UI", asyncapi: "AsyncAPI view" };

/** An API contract: the rendered view (Swagger UI or AsyncAPI) with the YAML a tab away. Other text shows as YAML. */
export function SpecView({ content, yaml }: { content: string; yaml: React.ReactNode }) {
  const kind = specKind(content);
  const [tab, setTab] = useState<"view" | "yaml">("view");
  if (!kind) return <>{yaml}</>;
  const tabClass = (t: string) => `rounded-full px-3 py-1 text-xs ${tab === t ? "bg-accent/15 font-medium text-accent" : "text-subtle hover:text-fg"}`;
  return (
    <div>
      <div className="flex gap-1 px-3 pt-2" role="tablist">
        <button role="tab" aria-selected={tab === "view"} className={tabClass("view")} onClick={() => setTab("view")}>
          {LABELS[kind]}
        </button>
        <button role="tab" aria-selected={tab === "yaml"} className={tabClass("yaml")} onClick={() => setTab("yaml")}>
          YAML
        </button>
      </div>
      {tab === "yaml" ? (
        yaml
      ) : (
        // Both viewers are designed for a light page; keep them on white in dark mode too.
        <div className="spec-view m-3 overflow-x-auto rounded-lg bg-white text-slate-900">
          <Suspense fallback={<p className="p-4 text-sm text-slate-500">Loading {LABELS[kind]}…</p>}>
            {kind === "openapi" ? <SwaggerView spec={content} /> : <AsyncApiView spec={content} />}
          </Suspense>
        </div>
      )}
    </div>
  );
}
