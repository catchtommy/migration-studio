import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type LoginStep } from "../lib/api";
import { useCopilotStatus } from "../lib/hooks";
import { Field, inputClass } from "./Field";

type Flow = { id: string; step: LoginStep };

const button = "rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:border-accent/50 disabled:opacity-50";

/** Drives GitHub's browser sign-in: open the authorize page, wait for it to complete, answer any prompt. */
function SignInFlow({ flow, onStep, onCancel }: { flow: Flow; onStep: (step: LoginStep) => void; onCancel: () => void }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const opened = useRef<string | null>(null);
  const { kind, url, prompt, message } = flow.step;

  const advance = async (value?: string) => {
    try {
      onStep(await api.copilotLoginAdvance(flow.id, value));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Steps that need nothing from the person move on by themselves; the server waits for the browser sign-in.
  useEffect(() => {
    if (kind === "open-url" && url && opened.current !== url) {
      opened.current = url;
      window.open(url, "_blank", "noopener");
    }
    if (kind === "open-url" || kind === "needs-interaction" || kind === "awaiting") {
      const timer = setTimeout(() => void advance(), kind === "awaiting" ? 1000 : 0);
      return () => clearTimeout(timer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.step]);

  return (
    <div className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-3 text-sm">
      {kind === "open-url" && url && (
        <p>
          Finish signing in on GitHub in your browser.{" "}
          <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-accent hover:underline">
            Open the GitHub sign-in page
          </a>{" "}
          if it did not open.
        </p>
      )}
      {kind === "needs-interaction" && <p>Finish signing in in the window that opened.</p>}
      {kind === "awaiting" && <p>Waiting for sign-in to complete…</p>}
      {kind === "input-required" && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void advance(input.trim());
          }}
        >
          <Field label={prompt ?? "Value required"}>
            <input className={`${inputClass} min-w-72`} value={input} onChange={(e) => setInput(e.target.value)} placeholder="e.g. https://mycompany.ghe.com" />
          </Field>
          <button type="submit" disabled={!input.trim()} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Continue
          </button>
        </form>
      )}
      {(kind === "error" || error) && <p className="text-bad">{error ?? message ?? "Sign-in failed."}</p>}
      <button type="button" onClick={onCancel} className="text-xs text-subtle hover:text-fg">
        Cancel sign-in
      </button>
    </div>
  );
}

const hostName = (host: string) => host.replace(/^https?:\/\//, "");

export function CopilotAccount({ githubHost }: { githubHost: string }) {
  const enterprise = hostName(githubHost) !== "github.com";
  const queryClient = useQueryClient();
  const { data: status, isLoading, refetch, isFetching } = useCopilotStatus();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState("");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["copilotStatus"] });
    queryClient.invalidateQueries({ queryKey: ["agentCatalog"] }); // models depend on the account
  };
  const begin = useMutation({
    mutationFn: (kind: "github" | "entra") => api.copilotLogin(kind),
    onSuccess: (f) => {
      setNotice(null);
      setFlow({ id: f.flow_id, step: f.step });
    },
  });
  const onStep = (step: LoginStep) => {
    if (step.kind !== "completed") return setFlow((f) => f && { ...f, step });
    setFlow(null);
    const result = step.result;
    if (result?.status === "completed") setNotice(`Signed in as ${result.login ?? "your account"}.`);
    else if (result?.status === "needs-plaintext-consent")
      setNotice("Signed in, but this machine has no secure credential store, so the sign-in was not saved. Sign in with a token, or set up a keychain.");
    else setNotice("Sign-in was declined.");
    refresh();
  };
  const cancel = () => {
    if (flow) void api.copilotLoginCancel(flow.id).catch(() => undefined);
    setFlow(null);
  };
  const withToken = useMutation({
    mutationFn: () => api.copilotToken(token.trim(), githubHost),
    onSuccess: () => {
      setToken("");
      setShowToken(false);
      refresh();
    },
  });
  const logout = useMutation({ mutationFn: api.copilotLogout, onSuccess: refresh });

  return (
    <div className="space-y-3 card p-4" aria-label="GitHub Copilot account">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`h-2.5 w-2.5 rounded-full ${status?.signed_in ? "bg-ok" : status?.available === false ? "bg-bad" : "bg-warn"}`} />
        <p className="flex-1 text-sm">
          {isLoading ? (
            "Connecting to GitHub Copilot… (the first start can take a minute)"
          ) : status?.signed_in ? (
            <>
              Signed in to GitHub Copilot as <span className="font-medium">{status.login}</span>
              {status.host && status.host !== "https://github.com" && <span className="text-subtle"> on {status.host}</span>}
            </>
          ) : status?.available === false ? (
            <span className="text-bad">{status.message}</span>
          ) : (
            "Not signed in to GitHub Copilot."
          )}
        </p>
        <button type="button" onClick={() => void refetch()} disabled={isFetching} className="text-xs text-subtle hover:text-fg disabled:opacity-50">
          Refresh
        </button>
      </div>

      {notice && <p className="text-sm text-ok">{notice}</p>}

      {status?.signed_in ? (
        <button type="button" className={button} disabled={logout.isPending} onClick={() => logout.mutate()}>
          Sign out
        </button>
      ) : flow ? (
        <SignInFlow flow={flow} onStep={onStep} onCancel={cancel} />
      ) : (
        status?.available !== false &&
        !isLoading && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={begin.isPending} onClick={() => begin.mutate("github")}>
                Sign in with GitHub{enterprise ? ` (${hostName(githubHost)})` : ""}
              </button>
              <button type="button" className={button} disabled={begin.isPending} onClick={() => begin.mutate("entra")}>
                Sign in with Microsoft Entra ID
              </button>
              <button type="button" className="px-2 text-sm text-subtle hover:text-fg" onClick={() => setShowToken(!showToken)}>
                {showToken ? "Hide token sign-in" : "Use a token instead"}
              </button>
            </div>
            <p className="text-xs text-subtle">
              Uses your own Copilot subscription{enterprise ? ` on ${hostName(githubHost)}; your company's single sign-on page opens if it uses one` : ""}. Already signed in
              with the Copilot CLI or GitHub CLI on this machine? That sign-in is picked up automatically; press Refresh.
            </p>
            {showToken && (
              <form
                className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end"
                onSubmit={(e) => {
                  e.preventDefault();
                  withToken.mutate();
                }}
              >
                <Field label={`GitHub token for ${hostName(githubHost)}`} hint='A fine-grained personal access token with the "Copilot Requests" permission. Classic tokens (ghp_…) are not supported.'>
                  <input type="password" autoComplete="off" className={inputClass} value={token} onChange={(e) => setToken(e.target.value)} />
                </Field>
                <button type="submit" disabled={!token.trim() || withToken.isPending} className={`${button} sm:mb-[1.4rem]`}>
                  Sign in
                </button>
              </form>
            )}
          </div>
        )
      )}
      {(begin.error || withToken.error || logout.error) && <p className="text-sm text-bad">{((begin.error || withToken.error || logout.error) as Error).message}</p>}
    </div>
  );
}
