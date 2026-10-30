import { useEffect, useId, useState } from "react";

type MermaidApi = typeof import("mermaid").default;
let loading: Promise<MermaidApi> | null = null;

/** Mermaid is large, so it's only loaded once a document has a diagram; it follows the page's light/dark scheme. */
function loadMermaid(): Promise<MermaidApi> {
  loading ??= import("mermaid").then(({ default: mermaid }) => {
    const dark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default" });
    return mermaid;
  });
  return loading;
}

/** A ```mermaid block drawn as a diagram. While it loads, and when the diagram has a mistake, the source shows instead
 * (with the error), so nothing written is lost. */
export function MermaidDiagram({ code }: { code: string }) {
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setSvg(null);
    setError(null);
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
      .then(({ svg }) => current && setSvg(svg))
      .catch((e: unknown) => {
        document.getElementById(`d${id}`)?.remove(); // mermaid leaves its scratch element behind on a parse error
        if (current) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      current = false;
    };
  }, [id, code]);
  if (svg) {
    return <div role="img" aria-label="Diagram" className="my-4 max-w-full overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />;
  }
  return (
    <div className="my-4">
      {error && <p className="mb-1 text-xs text-bad">This diagram couldn't be drawn: {error.split("\n")[0]}</p>}
      <pre className="max-w-full overflow-x-auto [overflow-wrap:normal]">
        <code>{code}</code>
      </pre>
    </div>
  );
}
