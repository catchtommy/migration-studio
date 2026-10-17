import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Markdown that stays inside its box: wide tables and code blocks scroll sideways, and long words (paths, class
 * names, URLs) wrap instead of running past the edge. */
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
          pre: ({ node: _node, ...props }) => <pre {...props} className="max-w-full overflow-x-auto [overflow-wrap:normal]" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </article>
  );
}
