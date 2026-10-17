# Migration Studio

A web UI that drives the migration of the Java 8 / Spring MVC 3 / iBatis monolith into Spring Boot microservices,
one domain operation at a time. Each operation goes through a workflow of agent stages and human approval gates.
Workflows are editable (Workflows page); the built-in *Standard migration* workflow is:

AS-IS docs → Deep dive → 👤 → **Cross-domain dependency finder → Modulith design (Linked-domain contracts → Domain → Orchestrator)** → Cross-domain integration design → 👤 → Module in monolith → Spring Boot microservice → Independent tests → 👤 → Modernisation → Monolith integration → 👤

### Modulith design

After the analysis is approved, four focused agents prepare the feature for the monolith. Each reads only the earlier
documents it builds on (the step's *uses*), and one gate (*Approve design*) reviews all of them with the cross-domain
integration design.

| Agent | Does | Writes |
| --- | --- | --- |
| **Cross-domain dependency finder** | For this one feature, finds exactly which other domains it is related to: which service, DAO or table of each, in which direction, how strongly, with code references. Also lists the domains it ruled out. | `domain-dependencies.md`, `domain-dependencies.yaml` |
| **Modulith design: Linked-domain contracts** | Consumer-first: defines what this feature needs from each linked domain as a contract (API, event or command), reusing contracts already in the catalogue. For each, it states what the feature relies on and the **temporary monolith binding** that provides it until the domain is migrated (the existing `Class#method` it delegates to, and when to remove it). | `linked-domain-contracts.md`, `linked-domain-contracts.yaml`, specs under `contracts/` |
| **Modulith design: Domain** | Designs the feature's domain inside the monolith: packages, public API, owned tables, and the ports it needs, each naming the contract it calls. Same technical stack, no database change, no UI change. | `domain-design.md`, `domain-design.yaml`, `openapi.yaml` |
| **Modulith design: Orchestrator** | Looks up the dependencies, the contracts and the domain design, and designs how the feature works as is while other domains are not ready (`local` mode: the existing service) and how it moves to the contracts (`contract` mode: served by the monolith binding now, by the domain's own service later), per dependency, by configuration. | `orchestrator-design.md` |

**Scope decision.** The dependency finder also records a decision in `domain-dependencies.yaml`: `domain-only` (the
feature has no outbound dependency on another domain) or `cross-domain`, with a reason. For a `domain-only` feature
the **Linked-domain contracts** and **Orchestrator** steps are skipped: no agent runs, and each leaves a short "skipped" note that
quotes the decision, so the timeline and later steps still see it. The decision is checked against the dependency
list (`domain-only` with an outbound dependency, or `cross-domain` with none, is refused), and the reviewer sees it
at the design gate. To bring the steps back, change the decision to `cross-domain` and reject the gate so they
re-run.

`domain-dependencies.yaml` is validated before the dependency finder may finish (the decision, relations, directions,
strengths and touchpoints). `linked-domain-contracts.yaml` is validated too: every domain the feature depends on needs
a contract, every contract needs a spec and (unless it reuses a catalogue contract) a temporary binding, and keys are
`<provider>.<capability>`.

**The contracts are the lasting part.** The monolith binding is temporary: when the linked domain gets its own
service, the same contract is served by that service and only the orchestrator's configuration changes. Approving the
design gate records the contracts in the **Contracts** catalogue as *proposed* (the linked domain's team has not
accepted them yet) with this feature's domain as a consumer; contracts the integration design also lists become
*approved*, and a contract already provided is never moved back. The integration design reuses the contract keys and
adds what they don't cover: events, sagas and read models.

The *Module in monolith* agent implements the designs: the module, the orchestrator and its `local` and `contract`
adapters, and the monolith bindings (marked temporary). The earlier *Modular monolith design* (`boundary_design`) and
**Guardrails.** The Modulith design agents act as a DDD architect and share one fixed set of rules: no UI change, no
database schema change (no DDL; contexts are carved by *who writes which tables* and by business capability, one
owning domain per table) and no stack change (Java 8, Spring MVC 3, iBatis). The domain may use **utility modules**
only (stateless helpers with no business rules, database access or domain knowledge) and must never depend on a **God
module**; the dependency finder classifies shared code as `shared_code` (utility) or `god_modules`. Domains depend on
each other only through linked-domain contracts. The domain design also writes `domain-design.yaml`, which is
checked before the agent can finish: `schema_changes`, `ui_changes`, `stack_changes` and `god_module_dependencies`
must be empty unless the user approved an exception, no table may be owned or read directly if another domain owns
it, and `other_domain_dependencies` may only name contracts. The Module in monolith agent treats it as hard limits.

*Modulith design: Wrapper* (`wrapper_design`) agents are no longer part of the Standard migration workflow. They stay
in the code and the database but are hidden from the Agents page and the workflow editor, and reappear while a
workflow uses them.

## Run it

macOS / Linux:

```bash
./dev.sh            # backend on :8000, UI on http://localhost:5173
```

Windows: double-click **`dev.cmd`**, or run `.\dev.cmd` in PowerShell or a command prompt (`./dev.sh` won't run
there: Windows doesn't know `.sh` files). The first run creates `backend\.venv`, installs the backend and the UI's
packages, then starts both; Ctrl+C stops them. It needs **Python 3.12+** (python.org; tick *Add python.exe to PATH*),
**Node.js 20+** (nodejs.org, LTS) and **Git for Windows**. Inside WSL (Linux on Windows), use `./dev.sh` as on
Linux instead.

First-time setup:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && PATH=../.tools/node/bin:$PATH npm install
```

Node 20 lives in `.tools/node` (the system Node is too old for Vite).

## Database

On first start the UI asks which database to use; change it later under **Settings → Database**.

| Choice | Use it for |
|---|---|
| **PostgreSQL** | Production and shared installations. Create an empty database with UTF8 encoding and a user that can create tables. |
| **Oracle** 19c+ | Production, where Oracle is the standard. Give the service name of the pluggable database; the user needs `CREATE TABLE` and quota on its tablespace. Uses python-oracledb in thin mode, so no Oracle Client install is needed (see below for thick mode). |
| **SQLite** | Local development and the test suite only: one writer at a time, on this server's disk. The UI shows a warning while it is in use. |

**Oracle character set**: documents, diffs and other long text are stored as `NCLOB`, which is always Unicode, so
they're kept exactly even when the database's character set is a single-byte one such as `WE8MSWIN1252` (a `CLOB`
there turns arrows, dashes and quotes into `¿`). **Test connection** shows the character set. For a new database,
`AL32UTF8` is best: short texts such as names then hold any character too.

**Oracle error DPY-3015 ("password verifier type 0x939 is not supported … in thin mode")**: the database user's
password only has Oracle's old 10G verifier. Best fix: ask the DBA to reset the password, with
`SQLNET.ALLOWED_LOGON_VERSION_SERVER` of 11 or higher in the server's `sqlnet.ora` (the default since 12c), so a newer
verifier is created. Otherwise, use thick mode: download **Oracle Instant Client Basic** for your OS from oracle.com,
unzip it, and enter its folder in **Oracle Instant Client folder** on the database form (or set
`STUDIO_ORACLE_CLIENT_DIR`). On Linux, also add the folder to `LD_LIBRARY_PATH` before starting the studio. Thick mode
stays on until the studio restarts.

Everything the studio records lives in that database: settings, agents, workflows, domains, features, contracts, runs,
every document agents write (md, yaml, contract specs) with reviewers' edits, each stage's diff, and the LangGraph
checkpoints of paused runs. Backing up the database backs up the studio; nothing under `backend/data` needs a backup
except, for convenience, `database.json`.

- **Where the connection is kept**: `backend/data/database.json`, readable by its owner only (it holds the password).
  The `STUDIO_DATABASE_URL` environment variable overrides it, e.g.
  `postgresql+psycopg://user:pass@host:5432/studio` or `oracle+oracledb://user:pass@host:1521/?service_name=STUDIO`;
  the database can't be changed from the UI then.
- **Switching** (Settings → Database → Change database) can copy all data from the current database into an empty
  new one. No run may be working while it switches.
- **Tables** are created and upgraded automatically at startup (Alembic, `backend/app/db/migrations`). Installs from
  before this choice existed keep their `data/studio.db` (SQLite) and are upgraded in place; their old
  `checkpoints.db` is imported once and kept as `checkpoints.db.imported`.

## Projects

Migration Studio runs several migration projects side by side. Each project has its own monolith repository,
domains, features, runs, documents, contracts, settings and members; the header's project switcher changes the
project you work in (the choice is remembered per browser).

- **Projects page** (switcher → *All projects*): the projects you can open. Users with **Manage projects** create
  projects there (name, key, language, stacks, repository); the creator becomes the project's admin.
- **Project page** (*Project* in the menu): the project's settings and its **Members**.
  - *Language and stack*: Java, C# / .NET, TypeScript / Node.js, Python, Go or other, plus what the monolith is
    and what it migrates to. Every agent gets this, so AI agents work in the project's language even where their
    instructions mention Java.
  - *Source and toolchains*: the monolith repository and its base branch (empty: the default branch), the legacy and target toolchain homes (a JDK, .NET SDK, Node,
    Python or Go install). `run_command` puts the agent's toolchain first on `PATH` and sets `JAVA_HOME` or
    `DOTNET_ROOT` for Java and .NET.
  - The test database connection, self-check attempts and the cross-domain conventions.
- **Workflows** start from the standard ones every project gets, and **changes always apply to the current project
  only**: saving a standard workflow gives the project its own copy (marked *copy*), which other projects don't see,
  and *Reset to standard* drops it. New workflows belong to the project they're made in.
- **Agents** are a shared library used by every project. A project can **customise** one: saving with *This project
  only* gives the project its own copy; *Revert to shared* drops it. Projects can also have agents of their own.
  Changing the shared agents needs *Manage agents* as a global role; a project role only allows project copies.
- The AI provider, Copilot sign-in, corporate network and database are global (**Settings**).
- Data from before projects is in the **Default project**; its existing users became members.

## Users and permissions

Everyone signs in with a username and password; nothing is visible without signing in. The first person to open a
new installation creates the **admin** account, then adds everyone else under **Users** (shown to users who can
manage users).

**Access to a project and its runs is given per project.** Only a project's members can open it, its domains, runs,
documents and contracts; anyone else gets *403* from the API and doesn't see the project in the UI. Each member has a
**role in that project**, and that role alone decides what they can do there. Access is given on the project's
**Members** tab, or per person under **Users → Edit → Project access**.

**Global roles** (Users page) are for work outside projects: global settings, users, creating projects, and changing
the shared agent/workflow library. They give no access to any project. Users with *Manage projects* see every
project and can manage its members, but they open its runs only after adding themselves. The first admin is made a
member of every project that exists when they're created, and whoever creates a project becomes its admin.

A role is a set of permissions, edited as a grid on **Users → Roles & permissions**:

| Permission | Allows |
|---|---|
| Manage agents | Create, edit, reset and delete agents |
| Manage workflows | Create, edit and delete workflows |
| Manage domains & features | Domains, feature lists, contract status, discovery runs |
| Run migrations | Start migration runs, retry failed ones |
| Approve workflow runs | Approve or reject at gates (also re-running or withdrawing a held rejection) |
| Edit documents & answer agents | Edit run documents and inputs, answer agents' questions |
| Manage project | The project's settings and members |
| Manage global settings & database *(global only)* | AI provider, network, Copilot sign-in, switching the database |
| Manage users & roles *(global only)* | Users, passwords, roles |
| Manage projects *(global only)* | Create and delete projects, manage the members of any project |

Default roles: **Admin** (everything, can't be changed), **Architect**, **Developer**, **Reviewer**, **Viewer**
(read-only). The **Agents** and **Workflows** pages are only shown to users who can manage them (agents'
instructions aren't sent to anyone else; other pages still show agent and workflow names). Everything else is
visible to anyone signed in, with *View only* where you can't change things; the backend checks the permission on
every change. Approvals, answers and document edits record the signed-in user.

- Passwords are stored as scrypt hashes; sessions are an HttpOnly cookie valid for 12 hours of inactivity.
  Five wrong passwords lock a username for 15 minutes.
- Forgotten passwords: an admin resets them on the Users page (the user is signed out everywhere).
- The app always keeps at least one active user who can manage users.
- *Global only* permissions come from a user's own roles; a project role never grants them.

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

The monolith repository URL (**Project → Monolith repository**) can point at the same `ghe.com` host; runs clone it
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

**Nothing starts until you've set it up.** *Find bounded contexts…*, *List features…* (a domain's Features tab, where
a new domain opens) and *List features for all domains…* first show a setup step. It shows whether the agent is in
*Stub* mode (sample results only) with a *Switch to AI* button, and the paths and notes to give it. For a domain these
are saved as its *Domain features* inputs, so re-lists use them too. *Start* then begins the run, which pauses at its
approval gate when the agent is done.

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
- The Modulith design steps are added to an existing *Standard migration* workflow the same way: only if it still has
  the previous release's steps. A customised workflow keeps its steps; add the new agents to it on the Workflows page.

## Committing code to git

Agent steps can change code in different repositories: the monolith's changes go into the monolith repository, each
domain's microservice into its own repository, the orchestrator into another. Each migration ends with a reviewed
commit in every repository it changed.

1. **Workflows → a migration workflow → each agent step → Changes code**: choose **In the project's monolith** or
   **In another repository**, with its URL and base branch. The URL can contain `{domain}`, filled in per run, so one
   step serves every domain's own repository, e.g. `https://github.com/acme/{domain}-service.git`. Steps without
   *Changes code* write documents only. Steps sharing a repository must use the same base branch. Save for **This
   project only** when the URLs are specific to a project.
2. **Workflow → Git** sets whether runs end with a review and commit, and the **feature branch name**
   (`migration/{domain}/{feature}` by default; `{run}` and `{date}` also work).
3. **New migration** starts the run on a feature branch named by that pattern, in every repository it will change.
   Names a repository already has, or that another unfinished run uses there, are refused. **Add / show entry
   points** shows the feature's entry points, pre-filled, to change for this run, with notes for the agents.
4. **Before a step changes code in a repository for the first time**, the run pauses as **Confirm branches**: the
   run page shows the repository, its base branch (editable while it isn't checked out yet) and the feature branch
   (renaming it renames it in every repository of the run). **Confirm and start** (needs *Run migrations*) checks the
   names against the repository and starts the step.
   The run checks out the monolith at the workspace root when it starts, and the other repositories inside it
   (`.studio/repos/<name>`, kept out of the monolith's git) when they're confirmed, all on the same feature branch,
   each cut from its own base branch. Agents can read
   every repository and the documents. Each step changes code only in its own repository, and its commands run there.
   Each step's changes are committed per repository and stored in the database, so rejections, restarts and workspace
   rebuilds cover all of them.
5. **Right after a step changes code**, the run waits as **Ready to commit** for that repository: review the step's
   changes, edit the commit message and **Commit & push**, and the workflow goes on. **Continue without committing**
   goes on too; the changes are offered again at the next commit of that repository or at the end.
   When the workflow finishes with code changes still uncommitted, it waits again, with a tab per repository that has
   them. Changes to the studio's documents alone don't need a commit. The review shows:
   - every changed file, with a checkbox: code starts ticked, the studio's documents (`.migration/`, in the monolith's
     tab) unticked;
   - a side-by-side diff, what the base branch changed since the run started, and the merged result;
   - **conflicts** with the latest base branch, to resolve per conflict (this run's side, the base branch's, or both)
     or by editing the result. Files one side deleted, and binary files, are resolved by choosing a version.
6. **Commit & push** (per repository, needs *Approve workflow runs*) makes one commit with the ticked files on top of
   that repository's latest base branch (or of the commit pushed before), as you, and pushes the feature branch.
   GitHub repositories then link to opening a pull request. At the end, the run is complete once nothing is left to
   commit. **Finish without committing** completes it, leaving the rest out.

Restarting a step picks up the workflow's current *Changes code* settings, so runs started before a step was set to
change code (or before git support) ask for branches and end with the review too. Their monolith changes can only be
committed if the run's workspace was cloned from the monolith; otherwise start a new run. Restarting a step after
committing checks the repository out from the feature branch it pushed (with anything others pushed there since), and
the run comes back to *Ready to commit* with the new changes, committed on top. The studio's documents aren't
committed: they stay in its database. Commits never force-push. If someone pushes to the feature branch while the
run is checked out, committing is refused (re-run a step to pick their commits up, or choose another branch name).

**Git access is per user.** Under **My settings** (account menu), everyone sets the **name and email** their commits
use, and an **access token per git host** (e.g. github.com, mycompany.ghe.com). Fetches and pushes over HTTPS use the
acting user's token, given to git through `GIT_ASKPASS`, so it never appears in a URL, command line or log. Without
a token for the host (or for SSH URLs), the server's own git setup is used. Tokens are encrypted with the key in
`STUDIO_SECRET_KEY`, or else `backend/data/secret.key` (made on first use, mode 600). Every server sharing the
database needs the same key, so back it up. If it's lost, users enter their tokens again.

## Publishing AS-IS documentation to the monolith

The AS-IS documentation and Deep dive steps describe the legacy code as it is. Once approved, their documents can be
committed into the monolith repository, so later agents (for this feature, and for features calling into it) read
reviewed documentation before the code, which saves tokens and time.

- **Workflow → agent step → "Publish its documents to the monolith's docs/<domain>/<feature> once approved"**: on by
  default for AS-IS documentation and Deep dive. Design, contract and microservice documents are never published.
- **Project → Settings → Monolith documentation folder** (default `docs`): documents go to
  `<folder>/<domain>/<feature>/`.
- After the gate that reviews those steps (**Approve analysis**) is approved, the run waits as **Ready to publish
  docs**. The run page lists the documents (new, modified or unchanged against the base branch, with a warning if
  someone changed one in git since this run published it), with a side-by-side diff of each and an editable commit
  message. **Publish to <base branch>** (needs *Approve workflow runs* and your git name and email) commits them
  **directly to the monolith's base branch** as you and pushes, never forced. If the branch moved since you looked,
  it asks you to compare again. **Skip for now** goes on without publishing.
- The legacy code stays read-only: the commit is built inside the server's mirror of the repository, so nothing
  touches the shared read-only copy or any checkout. The shared copy gets the documents at the next **Fetch now**.
- Agents are told when the monolith has `<folder>/`: read the feature's documentation first; the AS-IS steps update
  existing documents instead of re-analysing everything.
- Re-running the AS-IS steps and approving again asks to publish only if the documents changed.

## How it works

- **Run** = one `(domain, feature)` through one workflow; one LangGraph thread, checkpointed in the studio database
  (`orchestrator/checkpointer.py`). The run keeps a snapshot of its workflow, so editing or deleting a workflow only
  affects runs started afterwards. The graph is built per workflow snapshot (`orchestrator/graph.py`).
- **Workflows** (Workflows page): create, copy, edit and delete named workflows. Steps are agents from the library and
  approval gates; each gate chooses which earlier agent steps it reviews (and can send back to on rejection).
  Each domain can set a default workflow; the run form preselects it.
- **Hand-off between steps**: all steps share the run's git workspace, so code from earlier steps is simply there.
  Documents are passed explicitly: each agent step *uses the output of* all earlier agent steps (default) or selected
  ones, and gets their latest documents' paths, or their contents with *Include contents in prompt* (up to 200k
  characters; larger documents are listed for the agent to read).
- **Monolith on the server**: each project keeps one copy of its monolith repository, a bare mirror in
  `backend/data/repos/<project>.git`, cloned in the background when the repository is saved (or by the first run if
  it's missing). **Each new run fetches the latest changes first**, so it starts from the newest commit on the base
  branch. If the fetch fails (network, credentials), the run goes on with the copy on the server and says so in its
  log. Runs already started keep the commit they started from, and their page says how many newer commits there are.
  **Fetch latest** on the Project page shows what's new without starting a run (needs *Manage project* or *Run
  migrations*). Like workspaces, the mirror is a cache and is cloned again if it's missing.
- **Legacy code (read-only, shared)**: runs whose steps don't change the monolith don't check it out. Each project
  keeps **one** read-only copy of its base branch at a fixed commit, `backend/data/repos/<project>/legacy/<commit>/`
  (a git worktree of the mirror, so the history isn't duplicated; its files are read-only on disk). Agents read it at
  its usual paths (`src/...`) through their file tools, and commands can read it under `$STUDIO_LEGACY_DIR`; writing
  to it is refused. **Fetch now** (Project page) makes the latest commit the copy new runs read. Runs in progress keep
  the copy they started with, and copies no unfinished run reads are deleted at the next fetch. A run's page shows the
  commit it reads.
- **Workspace**: a run's own folder, `backend/data/workspaces/<run id>`. For a run that only reads the monolith it
  holds just the run's documents (`.migration/`) and the other repositories its steps change, so it stays small. A run
  with a step that changes the monolith gets its own local clone of the mirror (git hard-links the objects, so it costs
  about its checked-out files), on its feature branch. Every stage commits its output, so each attempt has a
  reviewable diff. **Documents are not kept on disk**: they live in the database; a step gets the documents in
  effect written to `.migration/` when it starts, its new or changed ones are stored, and the folder is cleared when it
  ends (reviewers' edits go straight to the database; the Changes tab works out document diffs from the stored
  versions). **A finished run's workspace is deleted**: everything is in the database or pushed. When a step is re-run
  later, or a workspace is missing, it is rebuilt (`app/workspace.py`): a repository whose changes were pushed is
  checked out **from its feature branch** (including anything others pushed there since) and only later changes are
  replayed; other repositories are re-cloned at the run's starting commit (or left blank when the run reads the shared
  copy) with the stages' code changes replayed
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
# Same suite on PostgreSQL or Oracle (an empty database each time):
#   STUDIO_TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/studio_test .venv/bin/pytest
cd frontend && npm test                   # unit tests
cd frontend && npm run e2e                # full run through the UI (needs ./dev.sh running)
# e2e signs in as E2E_USERNAME / E2E_PASSWORD (default e2e-admin / e2e-admin-password); on a stack with no users
# yet it creates that account as the first admin, so point it at a test stack, not a shared one.
# Question flow: start the backend with STUDIO_STUB_QUESTIONS=1 (stub agents then ask one question per stage)
# and run `E2E_STUB_QUESTIONS=1 npm run e2e`. STUDIO_API / E2E_BASE_URL point Vite / Playwright at another stack.
```
