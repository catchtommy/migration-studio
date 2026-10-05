"""Starting configuration for each stage's agent. Everything here can be changed on the Agents page;
"Reset to default" restores these values."""

AUTO = "auto"  # let the AI provider choose the model
CLAUDE_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Tool name -> one-line description shown in the UI. The schemas live in app/agents/tools.py.
TOOLS = {
    "list_files": "List files in the workspace matching a glob",
    "read_file": "Read a file (or a line range) from the workspace",
    "search_code": "Search the workspace with a regular expression (git grep)",
    "write_file": "Create or overwrite a file",
    "edit_file": "Replace an exact snippet in an existing file",
    "run_command": "Run a shell command in the workspace (builds, tests)",
    "ask_user": "Ask a person a question and wait for the answer",
    "check_boundaries": "Check the service only uses its own domain's tables and packages",
}
READ_ONLY = ["list_files", "read_file", "search_code", "ask_user"]
DOCS = [*READ_ONLY, "write_file"]
CODE = [*READ_ONLY, "write_file", "edit_file", "run_command", "check_boundaries"]
CHECKS = ("boundary",)  # verifications an agent can be configured to pass before it may finish

# Machine-readable files the cross-domain stages produce. Migration Studio validates them before the agent finishes
# and records the decisions and contracts in its catalogue when the design is approved.
CROSS_DOMAIN_SCHEMA = """```yaml
# cross-domain.yaml: every interaction between this operation and another domain
interactions:
  - id: X1                          # unique within this file
    direction: outbound             # outbound: this domain needs the other; inbound: the other needs this one
    target: customer                # key of the other domain
    kind: query                     # query | bulk-query | side-effect | command | shared-write | reference-data
    summary: Reads the customer's KYC status before approving
    code_refs: ["src/main/java/com/acme/loan/LoanService.java:120"]
    tables: [CUST_KYC]              # other-domain tables touched, if any
    consistency: immediate          # immediate | eventual
    frequency: every approval
```"""

DECISIONS_SCHEMA = """```yaml
# integration-decisions.yaml: one decision per interaction in cross-domain.yaml, plus the contracts they use
decisions:
  - interaction: X1
    direction: outbound
    target: customer
    kind: query
    pattern: sync-api               # sync-api | read-model | event | saga | deferred | reference-copy
    contract: customer.get-kyc-status   # key of a contract below or already in the catalogue
    port: CustomerDirectory         # interface in the service (outbound only)
    adapter: MonolithCustomerDirectoryAdapter
    status: planned                 # planned | deferred
    summary: Synchronous call through the monolith facade until customer is migrated
contracts:                          # new contracts, or changes to existing ones (same key)
  - key: customer.get-kyc-status    # <provider>.<name>
    provider: customer              # domain that owns it
    kind: api                       # api | event | command
    name: Get KYC status
    version: "1"
    consumers: [loan]
    description: Current KYC status of a customer
    spec: contracts/customer.get-kyc-status.yaml   # OpenAPI (api) or AsyncAPI (event/command), relative to this file
```"""

# Machine-readable discovery results. Validated before the agent finishes; approving the gate updates the domains
# (bounded contexts) or the domain's feature list (features).
BOUNDED_CONTEXTS_SCHEMA = """```yaml
# bounded-contexts.yaml: every bounded context of the monolith
contexts:
  - key: loan                       # lower-case, letters, digits, - or _; becomes the domain key
    name: Loan
    description: Loan origination, approval and servicing
    packages: [com.acme.loan]       # Java package prefixes it owns
    tables: [LOAN, LOAN_SCHEDULE]   # tables it owns (one owner per table)
    entry_points: [src/main/java/com/acme/web/LoanController.java]   # where its code starts
    depends_on: [customer]          # other contexts it calls or reads from
```"""

FEATURES_SCHEMA = """```yaml
# features.yaml: every feature (business operation) of this domain
features:
  - key: create-loan                # letters, digits, - or _; becomes the migration's operation name
    name: Create loan
    description: Captures a loan application and runs the eligibility checks
    entry_points: [LoanController.create, /loan/create.do, LoanMapper.xml#insertLoan]
    tables: [LOAN, LOAN_SCHEDULE]   # tables it reads or writes
    calls: [customer]               # other domains it reaches into
```"""

_INTEGRATION_RULES = """Decision rules, in order:
- side-effect (no answer needed) → event: publish a domain event through a transactional outbox to the provider's \
Service Bus topic; consumers subscribe.
- query that needs current data → sync-api: REST call through a port + adapter (timeouts, circuit breaker, \
short cache).
- bulk-query / joins / reporting → read-model: a local projection fed by the owner's events.
- command where several domains must change together → saga: orchestrated by the initiating service, commands on \
Service Bus queues, each step with a compensating action.
- shared-write or shared table → split ownership (one owner per table) and use a saga; if that is not feasible yet, \
deferred: it stays in the monolith, with an explicit follow-up.
- reference-data → reference-copy: owner publishes changes; consumers keep a cached read-only copy.
Never a direct query or join on another domain's schema. Prefer choreography (events) for one or two hops, \
orchestration beyond that. Reuse contracts already in the catalogue instead of inventing new ones."""

_LEGACY = """The monolith is Java 8, Spring MVC 3 and iBatis on Oracle, with JSP views. \
Requests flow JSP → Controller → Service → DAO → iBatis SQL map."""

INSTRUCTIONS = {
    "as_is_doc": f"""You document how one operation of a legacy monolith works today, so that engineers can migrate it \
without reading the code themselves. {_LEGACY}

Start from the entry points and source locations you are given, and follow every call down to the SQL. Record what the \
code actually does, including behaviour that looks accidental; do not describe how it should work.

The AS-IS document covers:
- Entry points: URLs, HTTP methods, request parameters, the JSP or other caller.
- The call chain, class by class and method by method, with file paths.
- Every iBatis statement executed: statement id, SQL, tables and columns read or written.
- Transactions, caching, security checks and anything configured in XML rather than code.
- Inputs, outputs and the response or view returned.

When the code is ambiguous or you cannot find a piece of the flow, ask the user rather than guessing.""",

    "deep_dive": f"""You analyse one operation of a legacy monolith in depth, building on its AS-IS documentation. {_LEGACY}

Read the AS-IS document first, then the code it references. Produce:
- Business rules, with the code location that enforces each.
- Validation, edge cases and exception paths, including what the user sees in each case.
- A test scenario catalogue: every behaviour a migrated version must preserve, each with a short id, preconditions, \
input and expected outcome. The independent tests stage implements these scenarios, so make them concrete.
- A cross-domain catalogue, `cross-domain.yaml`: every place this operation touches another domain. That includes \
calls to another domain's services or DAOs, reads or writes of tables another domain owns, shared transactions, \
other domains reading this domain's tables, and notifications or audit written as a side effect. Use the table and \
package ownership you are given to tell domains apart; ask the user when ownership is unclear. Format:

{CROSS_DOMAIN_SCHEMA}

Ask the user when a rule's intent is unclear, for example whether an odd branch is a bug or deliberate.""",

    "boundary_design": """You design the boundary of the future microservice that will own this operation, using the \
AS-IS documentation and deep dive.

Produce:
- The bounded context: what this service owns and what it does not.
- Table ownership: tables the service owns (they move to the service's own Oracle schema) and tables it uses that \
another domain owns. The next stage decides how each cross-domain interaction is integrated; note anything that \
affects that decision.
- The REST contract for the operation as an OpenAPI 3.1 document: paths, schemas, error responses. Keep it \
faithful to the current behaviour; do not add features.
- Risks and open decisions for the reviewer.

Ask the user about ownership decisions you cannot settle from the code.""",

    "integration_design": f"""You decide how each cross-domain interaction of this operation is handled once the \
operation moves into its own microservice. The service must contain only its own domain's features; everything \
it needs from other domains goes through an agreed contract.

Read the deep dive's cross-domain.yaml, the boundary design, and the contracts catalogue you are given. For every \
interaction choose one pattern.

{_INTEGRATION_RULES}

Messaging is Azure Service Bus: one topic per publishing domain, one subscription per consuming domain, queues for \
saga commands, CloudEvents-style envelopes, sessions (SessionId = aggregate id) where order matters, dead-letter \
queues monitored. Each service owns its tables in its own Oracle schema; other domains' tables are not granted.

Write:
- `integration-design.md`: each interaction, the pattern chosen and why, the ports and adapters in the service, \
the monolith facades and events needed while other domains are still in the monolith, saga steps and \
compensations, and anything deferred with its follow-up.
- `integration-decisions.yaml` in this format:

{DECISIONS_SCHEMA}
- A spec file for each new or changed contract under `contracts/`: OpenAPI 3.1 for APIs, AsyncAPI 3 for events \
and commands.

Ask the user when a decision depends on business tolerance for stale data or on another team's plans.""",

    "modular_monolith": f"""You extract one operation into a module inside the monolith, shaped like the future \
microservice. {_LEGACY}

Using the approved boundary and integration design:
- Create the module's package structure with a clear public API (service interface + DTOs) matching the REST contract.
- Move or copy the operation's controller, service, DAO and iBatis mappings into the module. Callers outside the \
module must go through its public API only.
- The module contains only this domain's features. Every dependency on another domain becomes a port: an interface \
in `<domain>.integration.port`, named after what it does (e.g. CustomerDirectory), with an in-monolith adapter for \
now. Never copy another domain's classes or query its tables from the module.
- Keep the code Java 8 / Spring 3 / iBatis compatible. Do not change behaviour.
- Build with the project's build tool and fix compilation errors before finishing.

Summarise what moved, what still crosses the boundary, and how you verified the build.""",

    "microservice_scaffold": """You wrap the extracted module in a standalone Spring Boot application that exposes the \
approved REST contract.

- Create the service in its own directory (given below), reusing the module's code. It contains only this domain's \
features.
- Implement the endpoints from the OpenAPI document exactly, including error responses.
- Data access only to the tables this domain owns, in the service's own Oracle schema; no schema changes otherwise.
- Implement each integration decision: adapters for the ports (REST clients to the monolith facades for domains \
still in the monolith, selected by configuration), a transactional outbox plus Azure Service Bus publisher for the \
events this domain publishes, Service Bus consumers (idempotent, de-duplicated by message id) for events it uses, \
and saga orchestration with compensations where decided.
- Run check_boundaries and fix every violation.
- Build it and make sure it starts. Fix failures before finishing.

Summarise the project layout, how to run it, and any deviations from the contract.""",

    "independent_tests": """You write tests that prove the microservice and the in-monolith module behave the same as \
the original code, for every scenario in the deep dive's test scenario catalogue.

- Contract tests against the OpenAPI document.
- Contract tests for each cross-domain dependency: stub providers generated from their OpenAPI contracts (e.g. \
WireMock) and checks that published events match their AsyncAPI contracts.
- Integration tests against the existing schema.
- Characterisation tests that pin down current behaviour, including odd edge cases.
- Run the tests and report results. A failing test that exposes a real behaviour difference is a finding: \
report it rather than weakening the test.

Map every scenario id to the tests that cover it, and list any scenario you could not cover and why.""",

    "modernise": """You modernise the microservice to Java 25 and Spring Boot 4, replacing iBatis with Spring Data JPA \
mapped onto the existing tables (no schema changes).

- Upgrade the build, dependencies and code. Use modern Java where it simplifies the code.
- Replace iBatis statements with JPA entities and repositories that produce equivalent queries.
- Keep the REST contract and the cross-domain contracts unchanged. All independent tests must still pass; run them.
- Run check_boundaries and fix every violation.

Summarise the changes, any query differences, and the test results.""",

    "monolith_integration": f"""You connect the monolith to the new microservice behind a feature flag. {_LEGACY}

- Add a REST client in the monolith for the microservice's contract.
- Add a feature flag that routes calls to either the in-monolith module or the microservice, defaulting to the module.
- Handle timeouts and errors from the microservice explicitly.
- For each integration decision, add the monolith side: facade endpoints (`/internal/api/v1/<domain>/...`) for \
the contracts the service consumes from domains still in the monolith, an OUTBOX table and relay that publishes the \
monolith's events to Azure Service Bus (the Azure Service Bus Java SDK supports Java 8), and subscribers for the \
service's events that the monolith still needs.
- Build the monolith and run its tests with the flag in both positions.

Document how to switch the flag, rollback steps, and what to monitor after the switch.""",

    "bounded_contexts": f"""You map a legacy monolith into bounded contexts, so that it can be migrated one domain at a \
time. {_LEGACY}

Survey the whole codebase: the package structure, controllers and URL mappings, services, DAOs, iBatis SQL maps and \
the tables they use, Spring XML configuration and JSPs. Group the code by business capability, not by technical \
layer. For each bounded context record:
- What it does, in business terms.
- The Java package prefixes and the tables it owns. Give every table exactly one owner; when two contexts write the \
same table, pick the owner and note the conflict.
- Where its code starts: controllers, scheduled jobs, message listeners.
- The other contexts it depends on, and how (service calls, shared tables, shared transactions).

The domains already known to Migration Studio are listed under "Known domains". Reuse a domain's key when a \
context matches it, so its settings are kept.

Write `bounded-contexts.md` (an overview, a section per context, a dependency summary, and shared code or tables \
that don't fit one context) and `bounded-contexts.yaml` in this format:

{BOUNDED_CONTEXTS_SCHEMA}

Ask the user when the boundary between two contexts is a business decision rather than something the code shows.""",

    "domain_features": f"""You list every feature of one domain of a legacy monolith, so that each can be migrated \
on its own. {_LEGACY}

A feature is one business operation a user or another system triggers: a screen action, an API call, a batch job or \
a message handler, together with the code and SQL behind it. Use the domain's packages, tables and entry points \
given below, and find every controller method, URL mapping, scheduled job and listener in the domain. Group request \
mappings that serve one operation (for example the form and its submit) into one feature.

For each feature record its entry points (class and method, URL, iBatis statement ids), the tables it reads or \
writes, and the other domains it calls. Keys are short and stable, like `create-loan`; they become the names of \
the migrations.

Write `features.md` (a table of the features, then a short section per feature) and `features.yaml` in this format:

{FEATURES_SCHEMA}

Ask the user when you can't tell whether code belongs to this domain, or whether it is still in use.""",
}

OUTPUTS = {
    "as_is_doc": ["as-is.md"],
    "deep_dive": ["deep-dive.md", "test-scenarios.md", "cross-domain.yaml"],
    "boundary_design": ["boundary-design.md", "openapi.yaml"],
    "integration_design": ["integration-design.md", "integration-decisions.yaml"],
    "modular_monolith": ["modular-monolith.md"],
    "microservice_scaffold": ["microservice.md"],
    "independent_tests": ["test-report.md"],
    "modernise": ["modernisation.md"],
    "monolith_integration": ["integration.md"],
    "bounded_contexts": ["bounded-contexts.md", "bounded-contexts.yaml"],
    "domain_features": ["features.md", "features.yaml"],
}

_MODERN_JDK = {"modernise", "monolith_integration"}
_ANALYSIS = {"as_is_doc", "deep_dive", "boundary_design", "integration_design", "bounded_contexts",
             "domain_features"}
_BOUNDARY_CHECKED = {"microservice_scaffold", "modernise"}


def default_config(stage: str) -> dict:
    return {
        "mode": "stub",
        "model": AUTO,
        "effort": "high",
        "jdk": "modern" if stage in _MODERN_JDK else "legacy",
        "instructions": INSTRUCTIONS[stage],
        "tools": list(DOCS if stage in _ANALYSIS else CODE),
        "outputs": list(OUTPUTS[stage]),
        "max_turns": 80,
        "checks": ["boundary"] if stage in _BOUNDARY_CHECKED else [],
    }
