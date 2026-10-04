# Migration Studio

A web UI that drives the migration of the Java 8 / Spring MVC 3 / iBatis monolith into Spring Boot microservices,
one domain operation at a time. Each operation goes through a workflow of agent stages and human approval gates.
Workflows are editable (Workflows page); the built-in *Standard migration* workflow is:

AS-IS docs → Deep dive → 👤 → Boundary design → 👤 → Module in monolith → Spring Boot microservice → Independent tests → 👤 → Modernisation → Monolith integration → 👤

## Run it

```bash
./dev.sh            # backend on :8000, UI on http://localhost:5173
```

First-time setup:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && PATH=../.tools/node/bin:$PATH npm install
```

Node 20 lives in `.tools/node` (the system Node is too old for Vite).

## AI provider: GitHub Copilot

Agents set to *AI* run with **GitHub Copilot using your own account** (Settings → AI provider; the Claude API is
the alternative). The backend talks to Copilot through the official
[GitHub Copilot SDK](https://github.com/github/copilot-sdk), which runs the Copilot runtime locally.

- **GitHub host** (Settings): `https://github.com`, or your GitHub Enterprise Cloud with data residency address,
  e.g. `https://your-company.ghe.com`. Sign-in and tokens use this host.
- **Sign in** on the Settings page: *Sign in with GitHub* opens the host's browser sign-in (your company's SSO page,
  for ghe.com), *Sign in with Microsoft Entra ID*, or a fine-grained personal access token with the
  *Copilot Requests* permission created on that host (classic `ghp_` tokens are not accepted). An existing
  Copilot CLI or GitHub CLI sign-in on the machine is picked up automatically. The header shows who is signed in.
- **Models**: an agent's model is `auto` (Copilot chooses) or any model id your Copilot plan offers; the Agents page
  suggests them once you are signed in. Copilot's organisation policies (allowed models, premium requests) apply.
- **Tools**: Copilot's built-in shell and file tools are switched off; agents only get this app's sandboxed tools,
  as enabled per agent.

### First-time setup with GitHub Enterprise Cloud (ghe.com)

Our GitHub is GitHub Enterprise Cloud with data residency, so Copilot sign-in happens on the company's `ghe.com`
host, not on github.com.

1. Start the app with `./dev.sh` and open http://localhost:5173.
2. Go to **Settings → AI provider**. Check that *Agents set to AI run with* is **GitHub Copilot (your account)**.
3. In **GitHub host**, enter the company's address, e.g. `https://your-company.ghe.com` (typing
   `your-company.ghe.com` also works), and click **Save host**.
4. Click **Sign in with GitHub (your-company.ghe.com)**. The company's GitHub sign-in page opens in your browser,
   usually followed by single sign-on. When it completes, the header shows *Copilot: your-username*.
   The first connection can take a minute while the Copilot runtime starts.
5. If the browser sign-in doesn't work for you, use **Use a token instead**: create a fine-grained personal access
   token **on the ghe.com site** (not github.com) with the *Copilot Requests* permission, and paste it.
6. Go to **Agents**, switch an agent's mode to **AI**, and start a migration run. Leave the model on `auto`, or
   pick one of the models the page suggests for your account.

The monolith repository URL (**Settings → Monolith repository**) can point at the same `ghe.com` host; runs clone it
with your normal git credentials, as in your terminal.

### Troubleshooting

| What you see | What to do |
| --- | --- |
| Header says *Copilot: sign in* | Sign in on the Settings page (steps 3–4), then press **Refresh**. |
| A run fails with *Not signed in to GitHub Copilot* | Same as above, then **Retry** the run. |
| Settings shows *The GitHub Copilot runtime could not start* / certificate errors | Fill in **Corporate network** below: proxy, extra CA certificates, or an IT-provided Copilot CLI path. |
| *Model … is not available to your Copilot account* | Set the agent's model to `auto`, or one your organisation allows. |
| Sign-in or agent sessions fail although you're signed in | Ask your Copilot admins whether Copilot CLI use is allowed for the organisation. |

GitHub's documentation: [Authenticating Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli),
[Authenticating to GHE.com](https://docs.github.com/en/copilot/how-tos/configure-personal-settings/authenticate-to-ghecom).

### Corporate network

- HTTPS from the backend trusts the **operating system's certificate store** (`truststore`), which normally contains
  the company's root CA. If Copilot still reports certificate errors, set *Extra CA certificates* to a PEM file.
- *HTTPS proxy* / *No proxy for* are passed to the Copilot runtime (and used for its first-start download).
- The SDK downloads the Copilot runtime from GitHub releases on first start. If that is blocked, ask IT for the
  Copilot CLI and set *Copilot CLI path* (or `COPILOT_CLI_PATH`).
- *Don't use WebSockets* helps behind proxies that block them.
- The firewall must allow the GitHub host and its Copilot endpoints; GitHub documents the required hosts for
  Copilot on GHE.com, which IT can check against.

## How it works

- **Run** = one `(domain, operation)` through one workflow; one LangGraph thread, checkpointed in
  `backend/data/checkpoints.db`. The run keeps a snapshot of its workflow, so editing or deleting a workflow only
  affects runs started afterwards. The graph is built per workflow snapshot (`orchestrator/graph.py`).
- **Workflows** (Workflows page): create, copy, edit and delete named workflows. Steps are agents from the library and
  approval gates; each gate chooses which earlier agent steps it reviews (and can send back to on rejection).
  Each domain can set a default workflow; the run form preselects it.
- **Hand-off between steps**: all steps share the run's git workspace, so code from earlier steps is simply there.
  Documents are passed explicitly: each agent step *uses the output of* all earlier agent steps (default) or selected
  ones, and gets their latest documents' paths, or their contents with *Include contents in prompt* (up to 200k
  characters; larger documents are listed for the agent to read).
- **Workspace**: each run clones the monolith (Settings → Monolith repository) into `backend/data/workspaces/<run id>`
  on branch `migration/<domain>/<operation>`. Every stage commits its output, so each attempt has a reviewable diff.
- **Gates** pause the graph with `interrupt()`. *Approve* continues. *Reject* requires comments: the workspace is reset
  to where the chosen stage started, and the stage re-runs with the comments as feedback.
  Artifacts can be edited and committed from the UI while a gate is open.
- **Failures** reset the stage's partial changes. *Retry* resumes from the failed stage; completed stages are not repeated.
- **Agents** (Agents page): a library of agents; the eight built-in ones can be reset to their defaults, and custom
  agents can be added (and deleted once no workflow or unfinished run uses them). Each has a title, mode (*Stub* writes a placeholder; *AI* runs with the AI provider),
  model, effort, JDK, instructions (its system prompt), enabled tools, required documents and a turn limit.
  AI agents (`agents/copilot_agent.py`, `agents/llm.py` for Claude) work in the run workspace with the tools in `agents/tools.py`
  and is sent back if a required document is missing. Defaults live in `agents/defaults.py` (*Reset to default*).
  With the Claude API as provider it needs Anthropic credentials in the backend's environment.
- **Domains** (Domains page): add and edit domains; give every agent shared context, and each stage's agent its own
  *where to look* paths and notes. Starting a run can add operation-specific entry points and notes. The domain's
  *Documentation* tab shows the latest documents of every operation.
- **Questions**: agents can ask questions (`ask_user`). The run shows *Needs input* and an answer panel; the stage
  continues once answered (or skipped). A restart while waiting fails the run; *Retry* asks again, reusing answers
  already given to the same question.

## Tests

```bash
cd backend && .venv/bin/pytest            # workflow routing: approve, reject/rollback, retry, manual edits
cd frontend && npm test                   # unit tests
cd frontend && npm run e2e                # full run through the UI (needs ./dev.sh running)
# Question flow: start the backend with STUDIO_STUB_QUESTIONS=1 (stub agents then ask one question per stage)
# and run `E2E_STUB_QUESTIONS=1 npm run e2e`. STUDIO_API / E2E_BASE_URL point Vite / Playwright at another stack.
```
