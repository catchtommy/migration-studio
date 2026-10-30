import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MermaidDiagram } from "./MermaidDiagram";

/** Markdown that stays inside its box: wide tables and code blocks scroll sideways, ```mermaid blocks are drawn as
 * diagrams, and
 * long words (paths, class names, URLs) wrap instead of running past the edge. */
export function Markdown({ children, className = "" }: { children: string; className?: string }) {
  return (
    <article className={`prose prose-sm min-w-0 max-w-none break-words dark:prose-invert [overflow-wrap:anywhere] ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ node: _node, ...props }) => (
            <div className="my-4 max-w-full overflow-x-auto">
              <table {...props} className="my-0 [overflow-wrap:normal]" />
            </div>
          ),
          pre: ({ node, ...props }) => {
            const code = node?.children[0];
            const classes = code?.type === "element" && code.tagName === "code" ? code.properties.className : undefined;
            if (Array.isArray(classes) && classes.includes("language-mermaid")) {
              const text = code?.type === "element" ? code.children.map((c) => (c.type === "text" ? c.value : "")).join("") : "";
              return <MermaidDiagram code={text.replace(/\n$/, "")} />;
            }
            return <pre {...props} className="max-w-full overflow-x-auto [overflow-wrap:normal]" />;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </article>
  );
}
