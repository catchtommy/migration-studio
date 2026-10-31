# Migration Studio

A web UI that drives the migration of the Java 8 / Spring MVC 3 / iBatis monolith into Spring Boot microservices,
one domain operation at a time. Each operation goes through a workflow of agent stages and human approval gates.
Workflows are editable (Workflows page); the built-in *Standard migration* workflow is:

AS-IS docs → Deep dive → 👤 → **Cross-domain dependency finder → Modulith design (Linked-domain contracts → Domain → Orchestrator)** → Cross-domain integration design → 👤 → Module in monolith → Orchestrator in monolith\* → Spring Boot microservice → Orchestrator microservice\* → Independent tests (acceptance suite) → 👤 → Modernisation → Service tests → Monolith integration → 👤

\* Only for a cross-domain feature that has an orchestrator; skipped otherwise.

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

**Guardrails.** The Modulith design agents act as a DDD architect and share one fixed set of rules: no UI change, no
database schema change (no DDL; contexts are carved by *who writes which tables* and by business capability, one
owning domain per table) and no stack change (Java 8, Spring MVC 3, iBatis). The domain may use **utility modules**
only (separate jars the monolith already depends on, named by Maven coordinates, with no business rules, database
access or domain knowledge) and must never depend on a **God module**; the dependency finder classifies shared code as
`shared_code` (utility) or `god_modules`. Domains depend on each other only through linked-domain contracts. The domain
design also writes `domain-design.yaml`, which is checked before the agent can finish: `schema_changes`, `ui_changes`,
`stack_changes` and `god_module_dependencies` must be empty unless the user approved an exception, no table may be
owned or read directly if another domain owns it, and `other_domain_dependencies` may only name contracts.

### Module in monolith

The *Module in monolith* agent is a careful legacy refactorer: it adds one feature to its domain's **domain** module
(one Maven module per domain, shared by its features) exactly as the designs say, in small mechanical steps, and proves
that behaviour is unchanged. The names come from the project's settings alone, the same for every feature, so the
features' branches merge into one module: the folders `domain_module_dir_pattern` and
`orchestrator_module_dir_pattern`, the artifacts `<prefix>:<folder>`, and the packages `<prefix>.<domain>.<feature>`
(domain module, lifted and modern services) and `<prefix>.orchestrator.<domain>.<feature>` (orchestrator module and
service), with `.shared` and `.util` for what several features use. The prefix is the project's **Base package
prefix** (Project → Settings, e.g. `com.acme`), needed before a migration starts. Code one feature uses from another
goes in the other feature's package, on this feature's branch, created when it isn't there yet. It is a
**parallel run**: the as-is code is never changed. It stays the old flow, and the new module is built **beside** it as
a copy. One toggle per feature (`migration.<domain>.<feature>.route`) chooses the flow on every call:

| Value | Flow | Added by |
|---|---|---|
| `legacy` (default) | the as-is code, unchanged | Module in monolith |
| `monolith` | the new domain module (or, with an orchestrator, controller → orchestrator → domain module) | Module in monolith, then Orchestrator in monolith |
| `service` | the orchestrator service when the feature has an orchestrator, else the domain service | Monolith integration |

The switch is a **router** behind the legacy service's bean: the legacy bean gets a new id (`brokerService` →
`brokerServiceLegacy`), and the router, registered under the old id (`primary`), implements the same interface and
hands each call on as the toggle says; an unknown value means `legacy`. Controllers and every other legacy file stay
as they are; only Spring wiring and POM lines change. Mappers between the legacy types and the module's sit in the
bridge package beside the router, which uses them (the module never knows the legacy types).

The one exception is a legacy caller (controller, job or service) with no bean seam: one that uses the concrete
class, `new` or a static call, or one that sequences the domains' calls itself (so a router behind one bean would
leave the other calls in place). It gets a **toggle branch** asking the router
(`if (brokerServiceRouter.routed()) { return brokerServiceRouter.create(name); }`), and it may be refactored as the
branch needs: lines moved into a method of their own, wrapped in an `else`, rewritten. The rule is about behaviour,
not text: a characterization test of that caller, written and committed before it changes, passes unchanged at
`legacy` and at `monolith` (and `service`, once it exists). The router stays the one place that reads the toggle; each
such caller is listed with its reason in `toggle_branches`, and the pull request lists, per caller, the lines added,
moved and rewritten, so a reviewer knows exactly where to look. Deploying changes nothing until someone switches, and
setting the toggle back to `legacy` is the rollback. The as-is code is removed later, outside these steps, once the
feature runs on the new flow.

1. **Baseline** build of the affected modules (stop and ask if it is red), then **characterization tests** first: new
   Mockito tests for every public method of the feature, green on the as-is code; they run again at `monolith`.
2. **One step at a time**: module skeleton, copy the classes (package line only), context ports in the copy, copy the
   SQL statements into the module's own SQL map (text identical), local adapters, events, then the Spring wiring and the
   router. Each step is compiled, tested, checked and committed locally; a failed step is reverted, not "fixed".
3. **Events** are only those the approved designs name, published with Spring's own event publishing after commit.
   There is no outbox (that would need a table), so delivery is not guaranteed; the pull request description says so.
4. **Cross-domain**: every call the copy makes to another domain goes through a port of the domain module; its `local`
   adapter sits in the bridge package (`bridge_dir`, beside the router), one class per contract binding, resolving the
   legacy bean lazily.

The step writes `module-extraction.yaml` (the domain module, `bridge_dir`, `routing`: the toggle, its `legacy`
default, the router and the beans it takes over; `toggle_branches`; every file changed outside the module and the
bridge; `events`) and
`modular-monolith.md`, which is the pull request description (what was copied, how to switch, how behaviour was proven
the same at both values, guardrails, known limitations, rollback = set the toggle to `legacy`).

**`check_module_guardrails`** compares the code with the commit the step started from, and runs again when the agent
tries to finish; the step cannot finish while it fails. It refuses: a legacy file (Java, JSP, SQL map, test) that is
changed, moved or deleted, or a new file outside the module and the bridge (new tests, and lines added to existing
ones, are fine anywhere); edits outside them other than Spring wiring lines for this step's beans (the legacy bean's
new id, the router, aliases, imports) and POM lines; for a caller listed in `toggle_branches`, no test of it in the
change, a branch that doesn't ask the router, new database access or transactions, or a DAO listed; any UI file; DDL or
new SQL scripts; SQL in the module that is not a copy, text for text, of the monolith's; build changes beyond a
`<module>` line and a dependency on the new module; a new module that declares plugins, a Java level, a dependency on a
module of the monolith itself, a library the monolith does not already use, or a version it does not use (the
exceptions are the utility jars listed in `domain-design.yaml`); a module or bridge folder that already existed (unless
it is the domain's own on the base branch); a class in the bridge that is neither the router, an adapter of a
port, nor a helper (mapper) the router or an adapter uses, or a helper with database access; a missing router, or a toggle configured to anything but `legacy`; ignored tests; any use of another domain's
tables or packages or of a God module; and more than 10 changed files outside the module (stop and ask). The built-in
agent always runs it; tick the *Module guardrails* check on the Agents page to give it to a custom agent.

### Orchestrator in monolith

Only a cross-domain feature has an orchestrator (a domain-only feature skips the step). The *Orchestrator in monolith*
agent builds it as its own Maven module beside the domain module, from `orchestrator-design.md`, and points the
router's `monolith` value at it, so that a request flows **controller → router → orchestrator → domain module**
(`legacy` still runs the as-is code). The orchestrator sequences the calls to the domain's public API and to the other
domains (through ports, each with a `local`/`contract` mode switch, `local` by default), and holds **no database access
and no transaction**: data and transactions stay in the domain module, which this step doesn't change. Controllers
don't change, except for a toggle branch in a caller that sequences the domains' calls itself (listed in
`toggle_branches`, same rules as above): the router gets a method that calls the orchestrator operation making the
whole sequence. It writes `orchestrator-extraction.yaml` (or `orchestrator: none` with the reason) and
`orchestrator-module.md`. **`check_orchestrator_guardrails`** refuses DAO, iBatis, JDBC, JPA, SQL maps or transactions
in the orchestrator, changes to the domain module, an orchestrator folder that already existed, and everything the
module check refuses outside the module and the bridge.

### Microservice

The *Spring Boot microservice* step is a **lift**, not a rewrite: it turns the module into a service in its own
repository (the step's *Changes code* setting) and changes nothing about how the code behaves. Modernisation stays a
later step. The stack stays the module's (Java 8, Spring 3.2, iBatis 2): an executable jar that starts an embedded
server, with a health endpoint, not Spring Boot, because Spring's own iBatis 2 support is gone from Spring 4 onward.

- The module's code, SQL maps and tests are **copied** with package and import lines changed and nothing else. A file
  that cannot be copied unchanged is listed under `replaced` with a reason.
- The service has the **same schema**, owned tables only. There is no outbox (that would need a table): designed events
  are published after commit to Azure Service Bus, at-most-once, and the pull request description says so.
- A monolith has no endpoints, only method calls, so the REST contract is new: each public method of the module's API
  maps to exactly one OpenAPI operation, and the step writes that table in `microservice-extraction.yaml`.
- What the session and thread-locals gave the monolith comes through the **context ports** of the domain design: the
  module reads them through a port, the monolith fills it from the session, and the service fills it from request
  headers. The step also lists, without solving them, the callers that use the module inside a larger transaction and
  the calls inside loops: over HTTP the first loses atomicity and the second becomes many calls.
- There is no authentication yet (internal network only); the pull request description records that limitation.
- Only the domain module is lifted: the orchestrator, when there is one, becomes a service of its own (next section).

**`check_service_guardrails`** compares the service repository with its starting commit, and runs again when the agent
tries to finish. It refuses: any change to the monolith repository; copied code or tests that differ from the module's
beyond package and import lines (or that were not copied); SQL that is not the module's, text for text; libraries other
than the module's (same versions) plus an embedded server, JSON, Azure Service Bus, an HTTP client, logging and the
module's test tools; a dependency on a module of the monolith; a Java level other than 8; secrets, DDL, new SQL scripts
or UI files; God modules and other domains' packages; operations that differ from `openapi.yaml`; events the designs do
not name; and a context port without an adapter. The built-in agent always runs it; tick the *Service guardrails*
check to give it to a custom agent.

### Orchestrator microservice

When the feature has an orchestrator, the *Orchestrator microservice* step lifts it into its own **Java 25 / Spring
Boot 4** service (it has no data layer to rewrite, so it goes straight to the target stack). Afterwards the monolith
calls the feature through it: **monolith → orchestrator service → domain service**, and the other domains through their
contracts. The orchestrator's classes and tests are copied unchanged apart from package and import lines; the domain
API becomes a REST client of the domain service, each port is wired straight to its contract adapter (outside the
monolith there is no `local` mode), and the Spring XML becomes Boot configuration. It writes `orchestrator-openapi.yaml`
(under `/internal/api/v1/<domain>/orchestrator/`), `orchestrator-service-extraction.yaml` and `orchestrator-service.md`.
**`check_orchestrator_service_guardrails`** refuses any database (data-access code, transactions, SQL, JDBC/JPA/iBatis
libraries, datasource or migration-tool settings), a stack other than Java 25 on Spring Boot 4, changes to the monolith,
secrets, other domains' code, and operations other than the declared ones. Set the step's *Changes code* to the
orchestrator service's own repository.

### Tests

- **Independent tests** writes a black-box **acceptance suite** in `acceptance-tests/` of the domain service's
  repository: a Maven project of its own (Java 8 language level, JUnit 5, REST Assured, WireMock 2) that calls the
  running service over HTTP and checks the database over JDBC, for every scenario of `test-scenarios.md`. It never
  imports the service's classes, so the same suite runs unchanged against the lifted service (the baseline in
  `test-report.md`) and after modernisation. The orchestrator service's tests are its own step's.
- **Modernisation** must run that suite unchanged; everything that passed before must still pass.
- **Service tests** (after Modernisation) adds JUnit 5 tests on the final code: `@WebMvcTest` controllers with the
  REST rules, `@DataJpaTest` repositories on the existing schema (Testcontainers when Docker is available), Mockito
  services, and the adapters to other domains. It adds tests only.

`check_test_guardrails` enforces this: Independent tests changes only `acceptance-tests/` (black-box, its own build);
Modernisation leaves `acceptance-tests/` unchanged; Service tests changes only `src/test/` and adds only test-scoped
dependencies. **`check_release_guardrails`** keeps Modernisation on the same schema (no DDL, schema scripts, Flyway or
Liquibase; `ddl-auto` `none` or `validate`), on Java 25 / Spring Boot 4, without secrets; and Monolith integration to
the switch only: the `service` value added to the feature's own toggle and router (still defaulting to `legacy`),
calling the orchestrator service when the feature has one and the domain service otherwise; no UI, DDL or SQL change;
only an HTTP client and the Azure Service Bus SDK added; the domain and orchestrator modules unchanged.

### What keeps agents within their step

- **Every built-in agent's guardrail checks run before it may finish**, even if they are unticked on the Agents page;
  when they keep failing, the step fails and its changes are reset.
- **Agents don't loop on what they can't fix.** A step that can't finish as it is set up (a code agent on a step
  without *Changes code*, the wrong kind of repository, an earlier step's declaration missing) fails before the agent
  starts, with the setting to change, and costs no tokens. A problem that needs the workflow, an earlier step, the
  environment or a person ends the step at once instead of sending the agent back; so do the same problems after a
  fix attempt. An agent is judged on its own documents, not on earlier steps' ones, and the check tools tell it when
  to stop calling them.
- **File tools** write only in the step's own repository and the documents folder. Documents a gate before the step
  approved are read-only to it (a change made some other way isn't kept). Steps without *Changes code* write
  documents only and can't run commands.
- **Commands** run with the server's environment reduced to what builds need (no API keys, database URLs or tokens),
  and refuse git commands that publish or rewrite (push, remote, config, amend, rebase, branch switching, tags,
  `sudo`, forcing ignored files in with `git add -f`). Agents commit locally; the run commits and pushes through the
  review, as you.
- **After a step**, changes it made to the run's other repositories are undone, and the step fails if its repository
  is no longer on the run's branch with the step's starting commit in its history.
- `run_command` is still a shell on the server: see *Deployment* below.

The earlier *Modular monolith design* (`boundary_design`) and *Modulith design: Wrapper* (`wrapper_design`) agents are
no longer part of the Standard migration workflow. They stay in the code and the database but are hidden from the
Agents page and the workflow editor, and reappear while a workflow uses them.

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
   (`migration/{domain}/{feature}` by default; `{run}` and `{date}` also work). **Each feature runs on its own
   branch**, cut from the base branch when the run starts (the latest of it is fetched then, so features merged into
   it since are included), and is merged into the base branch by hand when done. Features never share a branch, and a
   run never reads what another feature's run is doing: it knows the base branch and the domain's feature list only.
   The pattern must name `{feature}` (or `{run}`).
3. **New migration** starts the run on a feature branch named by that pattern, in every repository it will change.
   A name another unfinished run uses there is refused; a branch that already exists is continued on (next point). **Add / show entry
   points** shows the feature's entry points, pre-filled, to change for this run, with notes for the agents.
4. **Before every step that changes code** (each time it runs), the run pauses as **Confirm branches**: the run page
   shows the repository, its base branch (editable while it isn't checked out yet) and the feature branch (renaming it
   renames it in every repository of the run), with the workflow's current settings for the step. It says when the
   branch **already exists** (the step then continues on it). **Confirm and start** (needs *Run migrations*) checks the
   names, then **brings in the latest of the feature branch from git** (fast-forward, or a merge: where only this run
   pushed, git's version wins; work not pushed yet is kept, and a conflict with it stops the step) and starts it.
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
   - every file with a change, with a checkbox: code starts ticked, the studio's documents (`.migration/`, in the
     monolith's tab) unticked. Files already on the feature branch in git as they are aren't listed (the header says
     how many), and **files you leave out stay as they are on the branch in git**;
   - a side-by-side diff against the branch in git once it's pushed (or against where the run started), what the base
     branch changed since, and the merged result;
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
committed: they stay in its database. Commits never force-push. If someone pushes to the feature branch meanwhile,
the commit goes on top of their work, unless they changed a file you're committing (then leave it out, or re-run the
step: the latest is brought in before it starts).

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
  (`orchestrator/checkpointer.py`). The run keeps a snapshot of its workflow, so editing or deleting a workflow doesn't
  reshape a run behind its back; a waiting run whose workflow changed offers **Update this run**, which changes only
  the steps it hasn't reached. Each step's *Changes code* settings are picked up whenever the run goes on. The graph
  is built per workflow snapshot (`orchestrator/graph.py`).
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
- **Shared texts** (Agents page): the preamble every agent gets and the blocks several agents' instructions include
  as `{{key}}` (legacy stack, Modulith guardrails, REST rules, the machine-readable formats). A change reaches every
  agent that includes the block from its next step; *Reset* puts a default back. Exported agent.md files have them
  written out, and importing turns them back into placeholders.
- **Upgrades**: built-in agents still holding an earlier release's default instructions (never edited), and shared
  texts still holding their default, are brought up to the current defaults once, keeping their mode and model. An
  unchanged *Standard migration* workflow (shared, or a project's copy with only its step settings changed) gains new
  steps, keeping each step's settings.
- **Domains** (Domains page): add and edit domains; give every agent shared context, and each stage's agent its own
  *where to look* paths and notes. Starting a run can add operation-specific entry points and notes. The domain's
  *Documentation* tab shows the latest documents of every operation.
- **Questions**: agents can ask questions (`ask_user`). The run shows *Needs input* and an answer panel; the stage
  continues once answered (or skipped). A restart while waiting fails the run; *Retry* asks again, reusing answers
  already given to the same question.

## Deployment

- **One backend process.** Runs (up to 16 at once), reviews and catalogue updates are coordinated with in-process
  locks, so run one backend process (as `dev.sh` does). Several processes or instances would need database or file
  locks instead.
- **Toolchains**: the project's legacy and target toolchain homes (Project → Settings), e.g. a JDK 8 and a JDK 25:
  the lift and acceptance steps build with the legacy one, Modernisation, the orchestrator service and Service tests
  with the target one. Service tests uses Docker for Testcontainers when available (`DOCKER_HOST` and
  `TESTCONTAINERS_*` reach the agents' commands, as do proxy and certificate settings).
- **Sandbox the agents' commands.** `run_command` runs a shell on the server, limited as described above but not
  contained by the OS. Before giving agents real repositories and credentials, run the backend in a container (or VM)
  that mounts only its data folder and reaches only the git hosts and package registries it needs.
- **Back up** the database, and `STUDIO_SECRET_KEY` (or `backend/data/secret.key`), which encrypts users' git tokens.
- **Upgrading** runs the database migrations at startup, including for databases from before migrations.

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
