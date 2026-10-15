import { useEffect, useRef } from "react";
// The standalone bundle carries its own parser and renderer, so it needs no Node polyfills in the browser.
import AsyncApiStandalone from "@asyncapi/react-component/browser/standalone";
import "@asyncapi/react-component/styles/default.min.css";

const CONFIG = { show: { sidebar: false, info: true, servers: true, operations: true, messages: true, schemas: true, errors: true } };

/** An AsyncAPI document (events and commands) in the AsyncAPI viewer. */
export default function AsyncApiView({ spec }: { spec: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // A fresh element per render: the bundle mounts its own React root, which clearing the container wouldn't unmount.
    const host = document.createElement("div");
    el.appendChild(host);
    AsyncApiStandalone.render({ schema: spec, config: CONFIG }, host);
    return () => host.remove();
  }, [spec]);
  return <div ref={ref} />;
}
