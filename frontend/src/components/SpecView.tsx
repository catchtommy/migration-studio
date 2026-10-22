import { lazy, Suspense, useState } from "react";
import { parseYaml, YamlSource, YamlView } from "./YamlView";

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

/** An API contract: the rendered view (Swagger UI or AsyncAPI) with the YAML a tab away. Any other YAML document gets
 * a readable view (YamlView) with its source a tab away; text that isn't YAML shows as it is. */
export function SpecView({ content, yaml }: { content: string; yaml: React.ReactNode }) {
  const kind = specKind(content);
  const [tab, setTab] = useState<"view" | "yaml">("view");
  const tabClass = (t: string) => `rounded-full px-3 py-1 text-xs ${tab === t ? "bg-accent/15 font-medium text-accent" : "text-subtle hover:text-fg"}`;
  if (!kind) {
    const parsed = parseYaml(content);
    if (parsed === null) return <>{yaml}</>;
    if ("error" in parsed)
      return (
        <div>
          <p className="px-4 pt-3 text-xs text-warn">Not valid YAML: {parsed.error}</p>
          <YamlSource text={content} />
        </div>
      );
    return (
      <div>
        <div className="flex gap-1 px-3 pt-2" role="tablist">
          <button role="tab" aria-selected={tab === "view"} className={tabClass("view")} onClick={() => setTab("view")}>
            View
          </button>
          <button role="tab" aria-selected={tab === "yaml"} className={tabClass("yaml")} onClick={() => setTab("yaml")}>
            YAML
          </button>
        </div>
        {tab === "yaml" ? <YamlSource text={content} /> : <YamlView value={parsed.value} />}
      </div>
    );
  }
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
