import { Component, type ReactNode } from "react";

/** Keeps a part of the page that fails to render (a viewer given a malformed document, say) from taking the page with
 * it: `fallback` shows instead, with the error. Give it a `key` that changes with its input to try again. */
export class ErrorBoundary extends Component<{ fallback: (error: Error) => ReactNode; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    return this.state.error ? this.props.fallback(this.state.error) : this.props.children;
  }
}
