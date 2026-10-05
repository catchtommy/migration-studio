# Migration Studio

A web UI that drives the migration of the Java 8 / Spring MVC 3 / iBatis monolith into Spring Boot microservices,
one domain operation at a time. Each operation goes through a workflow of agent stages and human approval gates.
Workflows are editable (Workflows page); the built-in *Standard migration* workflow is:

AS-IS docs → Deep dive → 👤 → Boundary design → Cross-domain integration design → 👤 → Module in monolith → Spring Boot microservice → Independent tests → 👤 → Modernisation → Monolith integration → 👤

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

## Start here: discovery

Before any domain feature is migrated, map the monolith. Both steps are workflow runs with an approval gate, started
from the **Discovery** panel on the Domains page.

1. **Find the bounded contexts** (workflow *Discover bounded contexts*, one run for the whole monolith). The
   *Bounded contexts* agent writes `bounded-contexts.md` and `bounded-contexts.yaml`: each context's key, name,
   description, owned packages and tables, entry points and dependencies. On a large codebase, use *Where to look &
   notes* to point it at existing documentation in the monolith (paths relative to the repository root, e.g.
   `docs/**/*.md` or an index file) and at the modules that matter. Notes can say what to ignore, and that the code
   wins where the docs are out of date. The next run starts with the same paths and notes. Approving it:
   - adds new contexts as domains
   - fills in an existing domain's empty description, merges in its packages and tables, and gives the
     *Domain features* agent the context's entry points
   - deletes nothing; domains the result doesn't include get a *Not found by discovery* chip for you to review
2. **List each domain's features** (workflow *List domain features*, one run per domain). Start it from a domain's
   **Features** tab, or for every domain without a list at once. The *Domain features* agent writes `features.md`
   and `features.yaml`: each feature's key, name, entry points, tables and the domains it calls. Approving it
   replaces the domain's feature list; features that are already being migrated are kept and marked *Not in latest
   list*.

**Migrations start from the feature list.** The New migration form picks a feature, and its entry points go to the
agents unless you give others. A run for anything else is refused. You can also add, edit and remove features by
hand on the Features tab, for example to correct the list.

Both YAML files are validated before the agent may finish, and again when the gate is approved, after any manual
edits. The two discovery agents are ordinary agents: paste your own agent.md into them, or build other discovery
workflows (*New workflow → Each run covers*). The output stays in the domain's *Documentation* tab.

## Cross-domain calls

The monolith mixes all domains in one codebase and one Oracle schema. A domain's microservice must contain **only
its own domain's features**; everything it needs from other domains goes through an agreed contract.

**How each interaction is handled** (the integration design agent applies these rules; reviewers approve them at
*Approve design*):

| Interaction | Example | Pattern |
| --- | --- | --- |
| Query that needs current data | Loan approval reads the customer's KYC status | `sync-api`: REST call through a port + adapter (timeouts, circuit breaker, short cache) |
| Bulk query, joins, reporting | A report joins customer and loan tables | `read-model`: local projection fed by the owner's events |
| Side effect, no answer needed | After approval: notify, audit | `event`: domain event via a transactional outbox to Azure Service Bus |
| Several domains must change together | Approve loan → reserve payment → confirm | `saga`: orchestrated by the initiating service, with compensations |
| Shared write / shared table | One transaction updates loan and customer tables | One owner per table + saga, or `deferred` (stays in the monolith) with a follow-up |
| Reference data | Countries, currencies, branches | `reference-copy`: owner publishes; consumers cache a read-only copy |

Never a direct query or join on another domain's schema. Choreography (events) for one or two hops, orchestration
beyond that.

**What gets built:**
- *Service:* each cross-domain dependency is a **port** (interface in `<domain>.integration.port`). Its
  **adapter** calls the monolith's facade while the other domain is still in the monolith; it is swapped for the
  other service's adapter by configuration once that domain migrates. No classes copied from other domains.
- *Monolith:* **facade endpoints** (`/internal/api/v1/<domain>/…`) for what migrated services still need, an
  **OUTBOX** table + relay publishing to Service Bus, and subscribers for the services' events.
- *Azure Service Bus:* one topic per publishing domain (`{domain}-events`), one subscription per consumer, queues
  for saga commands (`{domain}-commands`), idempotent consumers, sessions where order matters (Settings →
  Cross-domain integration).
- *Data:* each service owns its tables in its **own Oracle schema**; other domains' tables are not granted.

**In Migration Studio:**
- **Deep dive** writes `cross-domain.yaml` (every interaction, with code references). **Cross-domain integration
  design** writes `integration-design.md`, `integration-decisions.yaml` and OpenAPI/AsyncAPI specs under
  `contracts/`. Both YAML files are validated before the agent may finish.
- Approving the design gate records the decisions in the **Contracts** catalogue (APIs, events and commands with
  provider, consumers and status) and the domain's **Dependencies**. Later migrations get the catalogue in their
  instructions and its specs in `.migration/contracts/`, so they reuse contracts instead of inventing new ones.
  An invalid decisions file blocks approval with the reason.
- Each domain's **Boundary** tab lists the tables and Java packages it owns. Agents with the **Boundary check**
  (on by default for *Spring Boot microservice* and *Modernisation*) are sent back until their service uses no
  other domain's packages or tables; they can also run `check_boundaries` themselves.
- The **Domains** page shows the dependency matrix and a **suggested migration order**: domains others depend on
  synchronously the least go first (typically event consumers such as notification and auditing).
- Upgrading adds the new agent and step to the built-in *Standard migration* workflow, unless you customised it.

## How it works

- **Run** = one `(domain, feature)` through one workflow; one LangGraph thread, checkpointed in
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
- **Gates** pause the graph with `interrupt()`. *Approve* continues. Rejecting requires comments, and has two forms:
  - *Reject & re-run now*: the workspace is reset to where the chosen stage started, and the stage re-runs straight
    away with the comments as feedback.
  - *Reject, amend first*: the run shows *Changes requested* and nothing runs. Meanwhile you can change the agent
    (for example paste a new agent.md), the run's or the domain's paths and notes, and the reviewed documents.
    *Re-run* then runs the stage with your (editable) comments. *Withdraw rejection* reopens the gate instead.
    Agents and inputs are read when a stage starts, so the re-run uses the amended versions.
  Artifacts can be edited and committed from the UI while a gate is open.
- **Failures** reset the stage's partial changes. *Retry* resumes from the failed stage; completed stages are not repeated.
- **Agents** (Agents page): a library of agents; the built-in ones can be reset to their defaults, and custom
  agents can be added (and deleted once no workflow or unfinished run uses them). Each has a title, mode (*Stub* writes a placeholder; *AI* runs with the AI provider),
  model, effort, JDK, instructions (its system prompt), enabled tools, required documents and a turn limit.
  AI agents (`agents/copilot_agent.py`, `agents/llm.py` for Claude) work in the run workspace with the tools in `agents/tools.py`
  and is sent back if a required document is missing. Defaults live in `agents/defaults.py` (*Reset to default*).
  With the Claude API as provider it needs Anthropic credentials in the backend's environment.
- **agent.md text**: each agent is edited as one plain text in GitHub Copilot's agent.md format, so you can
  **paste your agent.md from GitHub** into it (the editor opens on this view; *Form* shows the same agent field by
  field). *New agent* starts with a paste box. The header's `name`, `description`, `model` and `tools` set the
  agent (Copilot tool aliases `read`, `search`, `edit`, `execute` map to this app's tools; web, sub-agent and to-do
  tools are reported and skipped); the text below it is the instructions. A GitHub agent.md has no Migration Studio
  settings, so the agent **keeps** its mode (Stub/AI), effort, JDK, max turns, required documents and checks; the
  editor says so. To set them in the text too, add `metadata` with `mode`, `effort`, `jdk`, `max-turns`,
  `required-documents` and `checks`. Required documents are added to the agent's brief automatically, so your
  instructions don't need to repeat them. *Import agent.md* and *Download agent.md* use the same format.
- **Upgrades**: built-in agents still holding an earlier release's default instructions (never edited) are brought
  up to the current defaults once, keeping their mode and model.
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
