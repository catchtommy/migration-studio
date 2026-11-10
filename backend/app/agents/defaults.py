"""Starting configuration for each stage's agent. Everything here can be changed on the Agents page;
"Reset to default" restores these values."""

import re

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
    "check_service_guardrails": "Check the service against its limits: monolith untouched, code and SQL equal to the "
                                "module's, allowed libraries only, exactly the approved operations",
    "check_module_guardrails": "Check the module extraction against its limits: no UI, DDL or version changes, only "
                               "call-site and wiring edits outside the new modules",
    "check_orchestrator_guardrails": "Check the orchestrator module against its limits: no database access or "
                                     "transactions, domain module unchanged, only call-site and wiring edits outside it",
    "check_release_guardrails": "Check the modernised service (same schema, Java 25 on Spring Boot 4, no secrets) or "
                                "the monolith integration (no UI, DDL or SQL change, only an HTTP client and Service "
                                "Bus added, modules unchanged)",
    "check_test_guardrails": "Check that a test step only adds tests where it may: the acceptance suite's folder, or "
                             "test sources and test-scoped dependencies; and that the acceptance suite is unchanged",
    "check_orchestrator_service_guardrails": "Check the orchestrator service against its limits: no database of any "
                                             "kind, monolith untouched, orchestrator code copied unchanged, Java 25 "
                                             "and Spring Boot, exactly the declared operations",
}
READ_ONLY = ["list_files", "read_file", "search_code", "ask_user"]
# edit_file too: an amendment or a re-run changes a document in place, line by line (common.amend_brief)
DOCS = [*READ_ONLY, "write_file", "edit_file"]
CODE = [*READ_ONLY, "write_file", "edit_file", "run_command", "check_boundaries"]
CHECKS = ("boundary", "module_guardrails", "orchestrator_guardrails", "service_guardrails",
          "orchestrator_service_guardrails", "acceptance_test_guardrails", "acceptance_suite_unchanged",
          "service_test_guardrails", "modernise_guardrails", "integration_guardrails")
TEST_CHECKS = ("acceptance_test_guardrails", "acceptance_suite_unchanged", "service_test_guardrails")  # verifications an agent can be configured to pass before it may finish

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
    requirements: [FR-003, BR-002, MSG-04]   # ids from functional-spec.md this interaction serves, if any
    scenarios: [TS-004]             # ids from test-scenarios.md that exercise it, if any
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
  - interaction: X3
    direction: outbound
    target: customer
    kind: side-effect
    pattern: event
    contract: loan.loan-approved      # the event this domain publishes; the handler's domain consumes it
    status: planned
    summary: customer records the approval on the customer's history
    event_handler:                  # event decisions whose reaction still runs in the monolith (the allocation's event
                                    #   names delegates_to): its handler in the event-handler module, named from the
                                    #   domain and the event's key (CustomerLoanApprovedHandler)
      domain: customer              # the handling domain: the handler's package (<prefix>.eventhandler.customer)
      calls: [customer.record-loan-approval]   # the contracts whose entry points it calls, one or more per domain it
                                    #   works in: each a linked-domain contract with a binding and an entry_point
      remove_when: The customer service subscribes to loan.loan-approved
contracts:                          # new contracts, or changes to existing ones (same key)
  - key: customer.get-kyc-status    # <provider>.<name>
    provider: customer              # domain that owns it
    kind: api                       # api | event | command
    name: Get KYC status
    version: "1"
    consumers: [loan]
    description: Current KYC status of a customer
    spec: contracts/customer.get-kyc-status.yaml   # OpenAPI (api) or AsyncAPI (event/command), relative to this file
  - key: loan.loan-approved
    provider: loan
    kind: event
    name: Loan approved
    version: "1"
    consumers: [customer]
    spec: contracts/loan.loan-approved.yaml
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
    used_by:                        # entry points OUTSIDE this domain that use the feature, if any
      - domain: broker              # another domain's key, or external (a partner system, a batch feed)
        entry_point: BrokerOnboardingService.register -> LoanService.create
```"""

# Machine-readable result of the dependency finder: for one feature, exactly which other domains it is related to.
# Validated before the agent finishes; the Modulith design agents (contracts, domain, orchestrator) build on it.
DOMAIN_DEPENDENCIES_SCHEMA = """```yaml
# domain-dependencies.yaml: the other domains this one feature is related to, one entry per domain
feature: create-loan                # the feature (operation) analysed
domain: loan                        # the domain it belongs to
decision:                           # the Modulith scope decision; it decides which design steps run
  scope: cross-domain               # domain-only | cross-domain
  reason: Calls customer for the KYC status and reads CUST_KYC
dependencies:                       # empty when scope is domain-only
  - domain: customer                # key of the OTHER domain
    relation: calls                 # calls | reads-data | writes-data | called-by | shares-transaction
    direction: outbound             # outbound: this feature needs the other domain; inbound: the other needs this feature
    strength: hard                  # hard: the feature fails without it | soft: degrades gracefully | side-effect
    interactions: [X1]              # ids from cross-domain.yaml, if any
    touchpoints:                    # the exact places the two domains meet
      - kind: service               # service | dao | table | view | config | event
        ref: com.acme.customer.CustomerService#getKycStatus
        owner: customer             # domain that owns the referenced code or table
        used_for: Reads the KYC status before approval
        code_refs: ["src/main/java/com/acme/loan/LoanService.java:120"]
        tables: [CUST_KYC]          # tables behind it, if any
    notes: Same transaction as the loan insert
considered_unrelated:               # domains you checked and ruled out, with the reason
  - domain: payment
    reason: Only appears in a comment
considered_internal:                # requirements naming another party that need no other domain, with the reason
  - requirement: FR-006
    reason: The confirmation letter is produced by this domain's own templates
shared_code: [com.acme.common.DateUtils]   # utility modules only: stateless helpers, no business rules
god_modules: [com.acme.common.MasterService]   # mix several domains' logic: no domain may depend on them
```"""

# Where each functionality of one feature goes once migrated, decided before the design. A reviewer approves it at the
# analysis gate and may amend it there (the studio's table view writes `review`); the design builds on it as approved.
FUNCTIONAL_ALLOCATION_SCHEMA = """```yaml
# functional-allocation.yaml: every functionality of this one feature, and where it goes once migrated
feature: update-bank-account
domain: client
functionalities:
  - id: F1                          # F1, F2, ...: unique in this file
    name: Update the client's bank account
    legacy: ClientService.updateBankAccount validates and writes CLIENT_BANK   # what the code does today
    code_refs: ["src/main/java/com/acme/client/ClientService.java:88"]
    requirements: [FR-001, BR-002, MSG-01]   # ids of functional-spec.md this functionality implements
    placement: domain               # domain | domain-call | orchestrator | event-handler | other-domain | ui-unchanged
    reason: Writes only CLIENT_BANK, which the client domain owns
  - id: F2
    name: Check the bank code against the bank directory
    legacy: ClientService.updateBankAccount calls BankDirectoryService#findBank
    requirements: [BR-003]
    placement: domain-call          # the domain module calls another domain's API itself, without an orchestrator
    calls: [bank-directory]         # domain-call and orchestrator: the other domains it asks, each through a contract
    interactions: [X1]              # ids from cross-domain.yaml, if any
    reason: Needed inside the update's own validation; client may call bank-directory directly (Direct calls)
  - id: F3
    name: Refuse the change while the client has an open fraud case
    requirements: [BR-004, MSG-02]
    placement: other-domain         # another domain decides it; this feature only asks for the answer
    owner: fraud                    # event-handler and other-domain: the OTHER domain that does or decides it
    via: orchestrator               # other-domain only: how it is asked, orchestrator | domain-call
    interactions: [X2]
    reason: The fraud domain decides what an open case is; client may not call fraud directly
  - id: F4
    name: Add a note to every loan of the client
    legacy: Same method, then LoanDao.findByClient and an insert into LOAN_NOTE per loan, in the same transaction
    requirements: [BR-005]
    placement: event-handler
    owner: loan
    event:                          # event-handler only
      name: client.bank-account-changed
      triggered_from: domain        # domain | orchestrator: who publishes the event
      publisher: client             # the publishing domain
      when: After the bank account update commits
      handler: Fetches every loan of the client and adds a loan note to each
      delegates_to: [com.acme.loan.LoanNoteService#addNoteForClient]   # the handling domain's existing service
                                    #   method(s) that do the work, for the temporary handler while it is in the monolith
    depends_on: [F1]                # functionalities that must happen first, if any
    interactions: [X3]
    reason: A side effect in loan nobody waits for; today in the same transaction, after migration not atomic
```"""

# Machine-readable result of the linked-domain contracts step: what this feature needs from each domain it depends
# on. Validated before the agent finishes; approving the design gate records the contracts in the catalogue.
LINKED_DOMAIN_CONTRACTS_SCHEMA = """```yaml
# linked-domain-contracts.yaml: the contracts between this feature and the domains it depends on
feature: create-loan
domain: loan
contracts:                          # at least one per outbound dependency in domain-dependencies.yaml
  - key: customer.get-kyc-status    # <provider>.<capability>
    provider: customer              # the linked domain that provides it
    kind: api                       # api | event | command
    name: Get KYC status
    version: "1"
    description: Current KYC status of a customer
    reuse: false                    # true: already in the catalogue under this key; no spec or binding needed
    interactions: [X1]              # ids from cross-domain.yaml
    relies_on: Answer is current (no cache); fails closed when the customer is unknown
    spec: contracts/customer.get-kyc-status.yaml    # OpenAPI 3.1 (api) or AsyncAPI 3 (event/command)
    binding:                        # TEMPORARY: how the monolith provides it while customer is not migrated
      delegates_to: ["com.acme.customer.CustomerService#getKycStatus"]
      remove_when: The customer service provides this contract
      entry_point:                  # api and command contracts: its entry point in the integration module. Classes are
                                    #   named from the key: CustomerGetKycStatusApi (interfaces module, the domain and
                                    #   the orchestrator call it), CustomerGetKycStatusEntryPoint, its gateway
                                    #   CustomerGetKycStatusGateway and the legacy adapter CustomerGetKycStatusGatewayLegacy
        operations:                 # the interface's methods; together they reach every delegates_to above
          - method: getKycStatus
            operation_id: getKycStatus             # the spec's operation (OpenAPI), when it has one
            delegates_to: ["com.acme.customer.CustomerService#getKycStatus"]
```"""

# Guardrails for the Modulith design agents (and the implementation that follows). Plain text, included as is.
_MODULITH_GUARDRAILS = """Modulith guardrails. The target is a modular monolith that the team can reach WITHOUT \
changing the UI, the database schema or the technology stack, designed as a DDD architect would. These guardrails are \
fixed: a design or a code change that breaks one is rejected, not negotiated, and you may not grant yourself an \
exception.
1. The schema is fixed. No DDL: never propose a new or changed table, column, index, view, sequence, trigger, \
constraint or data type, and never propose moving data. Carve domains by WHO WRITES WHICH TABLES and by business \
capability. Every table has exactly one owning domain, the one whose code writes it. Other domains read or change it \
only through the owner's contract: no SQL, iBatis statement, join or view of this domain may touch a table another \
domain owns. Where two domains write the same table, name the owner (the domain whose business rules decide the \
table's state), treat the other writer as a consumer of the owner's contract, and raise it as an open decision. \
Never settle it with a schema change.
2. The UI is fixed. Same URLs, parameters, JSPs, forms, redirects and response shapes. Controllers keep their \
behaviour: they reach the domain's public API through the feature's router, which a toggle switches between the as-is \
code and the new domain. A controller with no bean seam for the router (it uses the concrete class, `new` or a static \
call) gets only a toggle branch that asks it.
3. The stack is fixed. Java 8, Spring MVC 3 and iBatis. No new framework or library and no language feature beyond \
Java 8. New Maven modules for the domain (and its orchestrator) are allowed: they are how the domain is carved out, \
and they use only the libraries the monolith already has.
4. Utility modules only. The domain may use shared code only if it is a utility module: stateless helpers with no \
business rules, no database access and no knowledge of any domain (date, string and number helpers, formatting, \
constants, generic validation, logging). List each one you use by its Maven coordinates (groupId:artifactId) and say why it qualifies. A utility module is \
a separate jar the monolith already depends on; helper classes inside the monolith's own sources are not one. Shared code that is not a \
utility is not available to the domain.
5. No dependency on God modules. A God module is a class or package that mixes several domains' business logic or \
that most of the code reaches (a giant service, manager, facade, helper or DAO). The domain never imports, injects, \
extends or configures one, directly or through a utility that wraps one. If the feature uses one today, split it: \
the logic that belongs to this domain moves into the domain, with behaviour unchanged; the logic that belongs to \
another domain is reached through a linked-domain contract. Name every God module you found and say how the \
dependency is removed.
6. Dependencies run one way and only through contracts. A domain not migrated yet is reached only through its \
integration entry-point interface (the interfaces module), never its classes. Between domains, only ports and adapters that call \
linked-domain contracts: no direct class, bean, mapper or table reference to another domain, and no cycles.
7. Public API only. The domain's internals are package-private; entities and iBatis result objects never leave the \
domain.
8. Behaviour is preserved and the scope is this feature. The as-is code stays as it is (the old flow, behind the \
toggle); the new domain is built from copies of it, wrapped, not refactored beyond that, except splitting a method \
at the steps the approved design gives to the orchestrator (the copy exposes those steps and the orchestrator calls \
them in order). No new features, and no redesign of other features.
9. Evidence over guesses. Every statement about the current code names its file and line; mark an assumption as an \
assumption. When table ownership, a table's writer or a God module is unclear, ask the user.
10. No ambient inputs. The feature's code never reads the session, the request, the current user, a thread-local or a static holder \
directly. Each one is reached through a context port: an interface in the domain module, named for what it gives (CurrentUser, \
CallContext), whose adapter reads the legacy source in the monolith. The port is what a separate service later fills from the \
request headers of the REST rules, so the code itself moves unchanged.
11. Exceptions only with the user. If the feature cannot meet a rule, stop and ask the user. Record the answer under \
`exceptions` with the rule and the reason, so that the reviewer sees it at the design gate."""

# REST design rules: the norm for every API a design step specifies (contracts, wrappers, public API, boundary).
_REST_RULES = """REST API design rules. They apply to every REST operation you specify, in the OpenAPI document and in the \
prose, and they override the legacy shapes: the monolith's URLs, JSPs and responses stay as they are, but the new \
REST contract follows these rules.
0. Paths: every REST API is under `/internal/api/v1/<domain>/...`, where <domain> is the key of the domain that \
provides it (the domain's own API and the contracts it provides alike). Services run on the internal network only.
1. Success codes: POST returns 201. Every other method returns 200.
2. An operation with nothing to return answers with exactly {"status": "Success"}.
3. An error is HTTP 400 for validation, with this body (an RFC 7807 style problem):
   {"type": "https://api.example.com/problems/validation-error", "title": "Validation Failed", \
"detail": "one or more field validation errors", "errorCode": "VAL-CUST-001", "instance": "/domain/123", \
"errors": [{"field": "cust.test", "message": "invalid customer first name"}, \
{"field": "cust.test", "message": "invalid customer last name"}]}
   Every HTTP error other than 400 uses the same body WITHOUT the `errors` field. Document each error response in \
the OpenAPI document with this schema.
   `errorCode` is <KIND>-<DOMAIN>-<number>. <KIND> and the status go together, and no other kinds or error statuses \
are used: VAL 400 (validation of the request), AUTH 401 or 403 (not signed in, or not allowed), NF 404 (the resource \
does not exist), CONF 409 (conflicts with the current state, such as a duplicate), BUS 422 (a business rule refuses \
a valid request), SYS 500 (unexpected failure). <DOMAIN> is an upper-case code of up to four letters for the providing \
domain (CUST for customer), the same in all its APIs; <number> is three digits, unique per kind within the domain. \
`type` is a URI per kind, `title` the kind's short name, `detail` a sentence for this occurrence, and `instance` the \
request path.
4. Request headers, all of type string, declared on every operation: `x-context-agency-id` (the unique logged-in \
agency broker id, for example PL123456789), `x-context-user-id` (the unique logged-in user id), `x-correlation-id`, \
`x-trace-id` and `x-ms-client-id` (the unique id of the caller). They carry what the monolith reads today from the \
session or thread-locals, so they are what the context ports are filled from.
5. Response headers, all of type string, declared on every response: `x-correlation-id`, `x-trace-id` and \
`x-ms-client-id`, echoing the request's values."""

# Machine-readable guardrail declaration of the domain design. Validated before the agent finishes: the lists that
# must stay empty are checked, and tables and dependencies are checked against domain ownership and the contracts.
DOMAIN_DESIGN_SCHEMA = """```yaml
# domain-design.yaml: the domain's declaration against the guardrails
domain: loan
feature: create-loan
module: loan-domain                   # the domain's ONE Maven module ("This domain's modules"), named for the domain, never the feature
artifact: com.acme:loan-domain        # its groupId:artifactId, as "This domain's modules" gives it (the project's settings)
base_package: com.acme.loan           # the module's package (<prefix>.<domain>), the same for every feature of the domain
feature_package: com.acme.loan.createloan   # this feature's classes: <base_package>.<feature package>
orchestrator_module:                  # cross-domain features: the domain's ONE orchestrator module, a separate Maven module
  module: loan-orchestrator           #   from the domain module, also named for the domain; write `orchestrator_module: none`
  artifact: com.acme:loan-orchestrator   # for a domain-only feature, or for a cross-domain one whose functional allocation
                                      #   places nothing on the orchestrator (then also no_orchestrator_reason)
  base_package: com.acme.orchestrator.loan   # <prefix>.orchestrator.<domain>
  feature_package: com.acme.orchestrator.loan.createloan
owned_tables: [LOAN, LOAN_SCHEDULE]   # tables this domain WRITES: one owner per table, none owned by another domain
read_only_tables: [LOAN_TYPE]         # this domain's own tables that its code only reads
utility_modules: [com.acme:common-utils]   # Maven coordinates (groupId:artifactId) of the utility jars used
other_domain_dependencies: [customer.get-kyc-status]   # only contract keys from linked-domain-contracts.yaml
schema_changes: []                    # must stay empty: no new or changed table, column, index, view, trigger
ui_changes: []                        # must stay empty: URLs, parameters, JSPs, response shapes
stack_changes: []                     # must stay empty: no new framework or library
god_module_dependencies: []           # must stay empty: no use of a God module
context_ports: [CurrentUser]          # ports for ambient inputs (session, current user, thread-locals); [] when there are none
exceptions: []                        # only what the user approved: {rule: schema_changes, reason: ...}
```"""

MODULE_EXTRACTION_SCHEMA = """```yaml
# module-extraction.yaml: what this step created and changed. A tool compares it with git before you may finish.
feature: create-broker
domain: broker
scope: domain-only            # copy of decision.scope in domain-dependencies.yaml
modules:
  - path: broker-domain        # the domain's ONE module (see "This domain's modules"): new for its first feature
    kind: domain              # the domain module, the only one this step creates or extends (the next step adds the orchestrator)
    artifact: com.acme:broker-domain
base_package: com.acme.broker   # the module's package, as "This domain's modules" gives it (<prefix>.<domain>)
#   this feature's classes: com.acme.broker.createbroker (its key without dashes), sub-packages allowed;
#   the domain's types several features use and none owns (entities, DAOs, DTOs): com.acme.broker.shared
#   stateless helpers several features use (formatters, mappers, validators): com.acme.broker.util
uses_features: [view-broker]  # the domain's other features whose packages this feature's code uses; [] when none
bridge_dir: web/src/main/java/com/acme/broker/legacybridge   # new: the router, and adapters for context ports and local bindings
routing:                      # the parallel run: legacy (as-is) or monolith (the new module), per call
  property: migration.broker.create-broker.route
  default: legacy             # the as-is code until someone switches
  router: web/src/main/java/com/acme/broker/legacybridge/BrokerServiceRouter.java
  routes:                     # each legacy bean the router takes over
    - bean: brokerService                     # the id callers use: now the router's
      legacy_bean: brokerServiceLegacy        # the legacy bean's new id
      interface: com.acme.broker.BrokerService   # the legacy interface the router implements
      module: com.acme.broker.createbroker.BrokerService   # what the monolith value calls (in the feature's package)
toggle_branches: []           # only callers with no bean seam: [{file: <path of the .java>, reason: ...}]
outside_changes:              # every existing file outside the module and the bridge you changed: wiring and POMs only
  - web/src/main/webapp/WEB-INF/applicationContext.xml
  - pom.xml
events: [BrokerCreated]       # only events named in the approved designs
legacy_modules:               # what this step built for the domains not migrated yet (when the design has entry points
#                               or event handlers): the integration module's entry points (every one the design gives)
#                               and the handlers of the events the domain module publishes
  adapter_dir: web/src/main/java/com/acme/integration/legacy   # new: the legacy adapters, one package per domain
  entry_points: [customer.get-kyc-status]                      # contract keys
  handlers:
    - {event: broker.broker-created, domain: customer}           # the event's contract key and the handling domain
added: [CloseBrokerCommand]   # optional: classes this feature added to the module
reused: [BrokerDao, Broker]   # optional: classes already in the module (merged features copied them), reused as they were
```"""

SERVICE_EXTRACTION_SCHEMA = """```yaml
# microservice-extraction.yaml: what the service exposes and what it did not copy. A tool compares it with git.
feature: create-broker
domain: broker
module_commit: 3f2a9c1             # the monolith commit the code was copied from
base_package: com.acme.broker   # the service's package (<prefix>.<domain>), as "This domain's services" gives it:
#   this feature's classes in com.acme.broker.createbroker, shared ones in .shared, helpers in .util
uses_features: []                  # the domain's other features whose packages this feature's code uses
operations:                        # this feature's operations (its openapi.yaml), none more (besides health)
  - method: BrokerService#create   # Class#method in the module
    operation_id: createBroker     # the operationId in openapi.yaml
replaced:                          # module files not copied unchanged, each with its reason
  - file: SpringEventPublisher.java
    reason: events go to Azure Service Bus after commit
open_decisions:                    # found, not solved here
  - topic: transactions
    detail: OrderService#place calls BrokerService#create and #update in one transaction
events: [BrokerCreated]            # only events named in the approved designs
```"""

ORCHESTRATOR_EXTRACTION_SCHEMA = """```yaml
# orchestrator-extraction.yaml: what this step created and changed. A tool compares it with git before you may finish.
feature: create-loan
domain: loan
orchestrator: module          # module | none (no orchestrator: give the reason and nothing else)
# reason: The feature is domain-only   # only with orchestrator: none
module:
  path: loan-orchestrator    # the domain's ONE orchestrator module (see "This domain's modules"): new for its first feature
  artifact: com.acme:loan-orchestrator
base_package: com.acme.orchestrator.loan   # <prefix>.orchestrator.<domain>: this feature's classes in com.acme.orchestrator.loan.createloan; shared ones in .shared, helpers in .util
uses_features: []             # the domain's other features whose packages this feature's code uses
operations:                   # what the router's monolith value now calls
  - method: LoanOrchestrator#create            # same name and signature as the domain API method it takes over
    replaces: LoanService#create               # the domain API method the router called before
ports:                        # ports of other domains, each with its mode switch
  - port: CustomerGetKycStatusApi   # for a domain not migrated yet: the contract's entry point interface
    contract: customer.get-kyc-status          # key in linked-domain-contracts.yaml
    property: loan.customer-directory.mode     # local | contract
    default: local
router: web/src/main/java/com/acme/loan/legacybridge/LoanServiceRouter.java   # its monolith value now calls the orchestrator
legacy_modules:               # as in module-extraction.yaml, the handlers of the events the orchestrator publishes
  handlers: []                #   (triggered_from: orchestrator) whose handling domain is still in the monolith
toggle_branches:              # only callers that sequence the domains' calls themselves; [] when there are none
  - file: web/src/main/java/com/acme/web/LoanController.java
    reason: calls LoanService#create and CustomerService#link itself; the orchestrator takes over both
outside_changes:              # every existing file you changed outside the module and the bridge: wiring and POMs only
  - web/src/main/webapp/WEB-INF/applicationContext.xml
```"""

_TOGGLE_BRANCH = """\
**Toggle branches: behaviour, not text, must stay the same.** A caller listed in `toggle_branches` may change as \
much as its branch needs: add the branch, move the as-is lines into a method of their own, wrap them in an `else`, \
rename a local variable, split a statement. What must not change is behaviour, at every value of the toggle: \
`legacy` gives exactly today's results, side effects and errors, and `monolith` (and `service`, once it exists) gives \
the same. That is proven by tests, so:
- before you touch the caller, write a characterization test of it (Mockito, or Spring's MockMvc where it exists): \
the normal path, each error branch, what goes into the model, session or response, the calls it makes and their \
order. It must pass on the as-is code; commit it first;
- after the change the same test passes, unchanged, with the toggle at `legacy` and at `monolith`;
- the branch only asks the router; no new database access or transaction in the caller, and never a branch in a DAO;
- keep the as-is lines as they are wherever you can, and in the pull request description list, per caller, the lines \
you added, moved and rewritten, so the reviewer reads the rewritten ones closely. Tests may be added or extended \
anywhere; existing assertions never change.

"""

ORCHESTRATOR_SERVICE_SCHEMA = """```yaml
# orchestrator-service-extraction.yaml: what the orchestrator service exposes and calls. A tool compares it with git.
feature: create-loan
domain: loan
orchestrator: service              # service | none (no orchestrator: give the reason and nothing else)
# reason: The feature has no orchestrator   # only with orchestrator: none
module_commit: 3f2a9c1             # the monolith commit the orchestrator module was copied from
base_package: com.acme.orchestrator.loan   # <prefix>.orchestrator.<domain>, as "This domain's services" gives it;
#   this feature's classes in com.acme.orchestrator.loan.createloan, shared ones in .shared, helpers in .util
uses_features: []                  # the domain's other features whose packages this feature's code uses
openapi: orchestrator-openapi.yaml # the API the monolith calls, next to this file
operations:                        # one per operation of orchestrator-extraction.yaml, none more (besides health)
  - method: LoanOrchestrator#create          # Class#method in the orchestrator module
    operation_id: createLoan                 # the operationId in orchestrator-openapi.yaml
domain_api:                        # the domain service operations the orchestrator calls
  - method: LoanService#create
    operation_id: createLoan                 # the operationId in the domain's openapi.yaml
contracts: [customer.get-kyc-status]   # linked-domain contracts it calls (keys in linked-domain-contracts.yaml)
replaced:                          # orchestrator module files not copied unchanged, each with its reason
  - file: CustomerDirectorySwitch.java
    reason: only contract mode exists in the service
```"""

INTEGRATION_ROUTING_SCHEMA = """```yaml
# integration-routing.yaml: how the monolith reaches the feature, old flow or new. Checked against the designs and git.
feature: create-loan
domain: loan
target: orchestrator-service       # orchestrator-service when the feature has an orchestrator, else domain-service
api: orchestrator-openapi.yaml     # the target's API: orchestrator-openapi.yaml, or the domain's openapi.yaml
toggle:
  property: migration.loan.create-loan.route   # the feature's toggle (routing in module-extraction.yaml)
  default: legacy                  # legacy (as-is) | monolith (the new code in the monolith) | service (this step)
router: web/src/main/java/com/acme/loan/legacybridge/LoanServiceRouter.java   # the router that reads it
operations:                        # every operation of the target's API, none more
  - operation_id: createLoan
    monolith: LoanOrchestrator#create          # the monolith value: the orchestrator module, else the domain module
    client: LoanOrchestratorClient#create      # the service value: the REST client of the target
temporary_subscribers:             # the monolith's Service Bus subscriber for each event handler of
#                                    integration-decisions.yaml; [] when none
  - handler: CustomerLoanApprovedHandler           # the event-handler module's handler interface it calls
    event: loan.loan-approved                      # the event contract it subscribes to
    enabled_property: migration.loan.loan-approved.customer-history.enabled   # default true; false once customer's
#                                                    own subscription to the event is live
```"""

_INTEGRATION_RULES = """The schema is fixed: no pattern may need a new table, column or data move. That rules out a \
transactional outbox and a stored projection; propose either only as an open decision for the user, who may record \
it as an exception.
Decision rules, in order:
- side-effect (no answer needed) → event: publish a domain event to the provider's Service Bus topic after the \
transaction commits (no outbox, so delivery is at-most-once; say what a lost event means for the business); \
consumers subscribe.
- query that needs current data → sync-api: REST call through a port + adapter (a timeout on every call, a circuit \
breaker, and a short in-memory cache only where the current code tolerates stale data).
- bulk-query / joins / reporting → sync-api through a bulk operation of the owner's contract. A read-model (a local \
projection fed by the owner's events) needs storage the schema does not have: propose it as an open decision.
- command where several domains must change together → saga: orchestrated by the initiating service, commands on \
Service Bus queues, each step with a compensating action.
- shared-write or shared table → split ownership (one owner per table) and use a saga; if that is not feasible yet, \
deferred: it stays in the monolith, with an explicit follow-up.
- reference-data → reference-copy: owner publishes changes; consumers keep a read-only in-memory copy, loaded \
through the owner's contract and refreshed from its events (no table).
Never a direct query or join on another domain's tables; every service uses only the tables it owns, in the \
existing schema. Prefer choreography (events) for one or two hops, \
orchestration beyond that. Reuse contracts already in the catalogue instead of inventing new ones."""

_LEGACY = """The monolith is Java 8, Spring MVC 3 and iBatis on Oracle, with JSP views. \
Requests flow JSP → Controller → Service → DAO → iBatis SQL map."""

# The migration's fixed rules, stated once for the lean workflow's agents (each older agent states its own).
_FIXED_RULES = """Fixed rules of this migration. They hold for every step and are not negotiated:
- The UI is frozen: URLs, parameters, JSPs, forms, redirects and response shapes stay exactly as they are; so are \
the external interfaces (web service, message and file formats) other systems use: their shape stays the same, while \
the code behind them is migrated and routed to the new API like any other entry point.
- The database is frozen: no DDL, no new or changed table, column, index, view, sequence or trigger, no data move, so \
no outbox and no stored projection. Each domain reads and writes only the tables it owns, in the existing schema.
- The migration is step by step: one feature at a time, each step small, reviewed at its gate, and reversible.
- The as-is flow keeps running. The feature's toggle router chooses per call between the as-is code (`legacy`, the \
default, also for an unknown value) and the new flow (`monolith`, later `service`). Controllers keep their \
behaviour: they reach the new flow through the router. Only a caller with no bean seam for the router (a controller, \
job or service that uses the concrete class, `new` or a static call) gets a toggle branch that asks it, its behaviour \
pinned by a test; never a DAO.
- Tests are never deleted, ignored or weakened, and an assertion never changes to make a test pass."""

# What the dependency analysis and the design never treat as a dependency on another domain.
_NOT_CONTRACTS = """Never a dependency on another domain, and never a contract, wherever the code sits:
- Generating a reference or sequence number (application, loan, case or any business reference) for a record this \
feature creates. It stays in this domain, with the same format, sequence or counter and iBatis statements as today. \
If the sequence or table is also written by another domain, name the owner and raise it as an open decision; never \
settle it with a schema change or a contract.
- Validating a format such as a mobile number, email address, postcode, PAN or IBAN. That is helper code: if it is in \
a utility jar the monolith depends on, list the jar's Maven coordinates (`shared_code`); if it sits in the monolith's \
own sources, name it as a helper the module extraction step copies, logic unchanged.
A helper that reads the database, the session or another domain's data is not a helper: it is a normal dependency."""

# The functional specification: business users' and BAs' document, written by Analysis next to the AS-IS one. Its
# ids (FR, BR, UC, MSG) are what the later steps prove they cover (app/functional_spec.py checks them).
FUNCTIONAL_SPEC_FORMAT = """Format of `functional-spec.md` (for business users and business analysts: business \
language only):
```
# Functional specification: <feature name>
Audience: business users and business analysts

| Domain | Feature | Version | Business owner | Approval |
|---|---|---|---|---|
| <domain> | <feature> | 1 | to be confirmed | recorded in Migration Studio at Approve analysis |

## 1. Purpose and business context
Why the business needs this feature and what it achieves.
## 2. Scope
In scope; out of scope (other features named, not described).
## 3. Users and roles
| Role | What they can do | What they can't |
## 4. Business process
The main flow as numbered steps, in business words. Then each way the function is started (its entry points: a
screen, another business area's process, a partner system), who starts it and why, and what differs for it (steps,
rules, information, messages).
## 5. Functional requirements
| ID | The system shall … | Criticality | Rules | Other party |
(FR-001, FR-002 …; Criticality "to be confirmed" unless the code makes it obvious; Rules: the BR ids it applies;
Other party: another business area or system involved, if any, else "-")
## 6. Business rules
| ID | Rule | When it applies | Outcome |
(BR-001, BR-002 …)
## 7. Use cases
### UC-01 <name>
Actor; preconditions; main flow; alternative flows; exception flows (each ending in its MSG id); outcome;
requirements and rules covered (FR and BR ids).
## 8. Information captured and shown
| Item (as on screen) | Meaning | Mandatory | Allowed values |
## 9. Messages and outcomes
| ID | When | What the user sees (exact text) |
(MSG-01, MSG-02 …; every message, warning and confirmation the user can get)
## 10. Notifications, reports and records
What the business gets as a side effect: letters, emails, reports, audit records, and who receives them.
## 11. Business expectations
Only what a user can observe (for example "the result shows on the same screen"); "None observed" otherwise.
## 12. Assumptions and open questions
## 13. Glossary
## Appendix: Traceability
| Requirement or rule | Scenarios that prove it |
```
Writing rules: no technical content at all: no class, method, table, column, SQL, URL, file, package, framework or \
technology name. Describe today's behaviour as it is, neutrally, odd behaviour included (as an open question when it \
looks accidental). One requirement per behaviour. The ids are stable labels: never renumber them."""

# The test scenario catalogue: the test team's document, written by Analysis.
TEST_SCENARIOS_FORMAT = """Format of `test-scenarios.md` (for the test team: business terms, no code internals):
```
# Test scenarios: <feature name>
Audience: test team

## Summary
| Scenarios | Positive | Negative | Boundary | Exception | Requirements covered |

## Scenarios
### TS-001 <title>
- Covers: FR-001, BR-002, MSG-03
- Type: Positive | Negative | Boundary | Exception
- Priority: to be confirmed (from the requirement's criticality)
- Preconditions: the state before, in business terms (for example "an approved customer with no open loan")
- Test data: the values to use, as entered on screen
- Steps:
  1. what the user does, screen by screen
- Expected result: the outcome the user sees (a message by its MSG id, never its text again) and what changes for \
the business

## Not covered
Anything that can't be tested this way, with the reason; "None" otherwise.
```
Every functional requirement and business rule of `functional-spec.md` is covered by at least one scenario (its \
`Covers:` line). Every way the function is started (section 4 of the specification) has at least one scenario of its \
own, starting there, plus one for each thing that differs for it; name it in the scenario's title. A message is cited by its MSG id; its exact text is only in `functional-spec.md`. Checks on \
database rows are not part of this document: the acceptance tests take them from `as-is.md`."""

# How the cross-domain dependencies of one feature are found (the lean workflow's Domain dependencies step; Analysis
# itself in runs that started before that step existed).
_DEPENDENCY_RULES = """How to find the dependencies. Use the domain ownership you are given (packages and tables per \
domain) to decide which domain owns each class and table, and follow calls through shared utilities to see whether \
they reach another domain. A dependency counts only if a call path from this feature's entry points reaches it; where \
a shared method branches by action or mode, follow only this feature's branch. For every related domain:
- Outbound: the services, DAOs, iBatis statements and tables of that domain this feature calls, reads or writes, \
named exactly (`package.Class#method`, the statement id, the table).
- Inbound: code of other domains that calls this feature's classes or reads or writes its tables (search for usages). \
Every entry point of another domain in `as-is.md`'s "Entry points" is one: an inbound dependency on that domain \
with its caller as the touchpoint, `relation: called-by`, or `relation: shares-transaction` instead when it shares the \
caller's transaction. Inbound dependencies \
never make the feature cross-domain.
- Shared transactions, shared tables, JSP includes and Spring XML wiring that cross the domain line.
- Strength: `hard` (fails without it), `soft` (degrades) or `side-effect` (nothing needs the answer).
Name domains by key, never "other modules". Code that belongs to no single domain is not a dependency, but classify \
it: `shared_code` for utility modules only (stateless helpers with no business rules, no database access and no \
knowledge of any domain), and `god_modules` for classes or packages that mix several domains' business logic or that \
most of the code reaches (a giant service, manager, facade, helper or DAO); in the `notes` of the dependencies it \
serves, say where the feature uses each God module and which part belongs to which domain. List the domains you \
checked and ruled out under `considered_unrelated`.

The scope decision (`decision`): `domain-only` when the feature has no outbound dependency on another domain (shared \
utilities and inbound callers don't count), else `cross-domain`. An event or command the feature only publishes (a \
`side-effect`, for example a notification another domain sends) is an outbound dependency, so the feature is \
`cross-domain`: its contract is designed. The design then finds nothing to orchestrate when that is all, and the \
orchestrator steps are skipped. Choose `domain-only` only when you have checked the \
code and are sure; when in doubt, choose `cross-domain`. It decides which steps run (a domain-only feature has no \
contracts and no orchestrator), it is validated against `dependencies`, and the reviewer approves it at the analysis \
gate."""

# Where each functionality of a feature goes: decided by the dependency step, approved (and amended) by a reviewer at
# the analysis gate, and followed by the design.
_ALLOCATION_RULES = """How to allocate the functionalities. Break the feature into its functionalities: each thing it \
does that a business user would name (a validation, an update, a lookup, a note, a letter, an audit record), \
following the code from its entry points. Give each one placement, taking the FIRST of these that fits:
1. `ui-unchanged`: enforced only in the JSP or JavaScript; it stays as it is.
2. `event-handler`: a side effect, in another domain or later in this one, that the caller doesn't wait for: notes, \
letters, notifications, audit records, and work fanned out over another domain's records. The legacy code often does \
it inline (it fetches the other domain's records and loops over them in the same request); once migrated, the \
publisher raises an event after its change commits and a handler does the work. Name the event \
(`<publisher>.<what happened>`), `triggered_from` (`domain` when the domain's own change is the trigger, the usual \
case; `orchestrator` only when the trigger is the completion of an operation that spans domains, which then needs a \
functionality placed on the orchestrator), the `publisher`, `when`, what the `handler` does, and in `delegates_to` \
the handling domain's existing service method(s) that do that work today, on the interface its callers use (never a \
God module, a DAO or SQL; when the work spans several domains, each one's methods, and each of those domains is an \
outbound dependency): until the feature runs in its own service the event is published in-process only, so a \
handler in the event-handler module reaches them (through integration entry points), even when that domain is \
already migrated (its bean then routes the call); checked. Only when the user agrees there is nothing to call, give \
`no_temporary_handler` with the reason instead. `owner` is \
the domain whose handler does it. \
Events are delivered at most once (the schema is frozen: no outbox): where the business needs it guaranteed, ask \
the user.
3. `other-domain`: another domain decides or does it (its code enforces it today: its rule, its data), and this \
feature only asks for the answer: `owner` is that domain, and `via` how this feature asks it (see 4: `domain-call` \
only where the Direct calls setting allows it, else `orchestrator`).
4. A functionality that needs another domain's answer, or makes another domain do something and waits for it, is \
asked in one of two ways, with the domains asked in `calls`:
   - `domain-call`: the domain module calls the other domain's API itself, through a port, as part of its own logic, \
without an orchestrator. ONLY for a domain the domain's Direct calls setting allows (given under "Cross-domain \
integration": typically a subdomain or a utility API domain); checked.
   - `orchestrator`: every other case: the orchestrator calls the other domains and the domain module, and builds what \
the caller gets (it holds no database access). Also when several domains' answers are combined, or one domain's \
failure decides what the caller sees.
   When the setting allows a direct call but you can't tell whether the call belongs inside the domain's own logic \
(`domain-call`) or around it (`orchestrator`), ask the user.
5. `domain`: everything else: this domain's own logic on its own tables, no other domain involved. Generating a \
reference number for a record this feature creates, and validating a format, are `domain`.
`owner` is only for `event-handler` and `other-domain` (the other domain); the other placements are this domain's \
own. Agree with `domain-dependencies.yaml`: every outbound dependency is in the `calls` of a functionality, or is the \
`owner` of an event handler or other-domain functionality (checked); a `side-effect` dependency is normally an event \
handler. Link the interactions of `cross-domain.yaml`: every placement except `domain` and `ui-unchanged` names the \
interactions it covers (`interactions`), `domain` and `ui-unchanged` link no outbound one, and every outbound \
interaction is linked by a functionality (checked; the design decides each interaction's pattern from the placement \
that links it). Every requirement and business rule (FR, BR) of `functional-spec.md`, when the run has one, is in the \
`requirements` of at least one functionality (checked); list the messages (MSG) with the functionality that shows \
them (on an `other-domain` one, its messages belong to the side that asks, its `via`, which maps the answer). Give the `reason` for each placement in a \
sentence, and say there when the legacy code does it in the same transaction as the feature's own change (once \
migrated it no longer is). When you can't decide a placement from the code, ask the user rather than guess. A \
reviewer checks the file at the analysis gate and may change any placement, and the design then follows it as \
approved. When you update the file in a re-run, keep every functionality that has a `review` (the reviewer changed \
or commented it) as the reviewer left it, `review` included; if the code now contradicts it, say so in your summary \
instead of changing it.
Where it goes while the other domains are not migrated (the reviewer reads it from the placement, so the design \
follows it): a `domain-call`, `orchestrator` or `other-domain` functionality reaches each domain it asks through an \
entry point in the integration module (one per contract); an `event-handler` one whose handling domain is still in \
the monolith (it names `delegates_to`) gets a handler in the event-handler module, which reaches that domain, and any \
other it works in, through integration entry points too."""

# The temporary modules of the monolith for the domains not migrated yet (app/legacy_modules.py): what the design, the
# Module in monolith and the Orchestrator in monolith steps build, and the later steps reuse.
_LEGACY_MODULES = """The domains not migrated yet. While a domain is still in the monolith, the feature reaches it \
only through two temporary Maven modules of the monolith, each with one package per domain, so the owner is plain. \
A domain's packages are removed once that domain's service provides those contracts and handles those events itself \
and nothing in the monolith calls them any more (`remove_when`), never by a feature's run; until then they keep \
working after the domain is migrated, as its router sends the legacy calls on to its service. Neither holds business \
logic, and nothing depends on them: Spring wires their beans.
- The integration module (the project's integration module folder, base package `<prefix>.integration`): one entry \
point per linked-domain contract with a temporary binding (an `api` or `command` of a domain not migrated yet). Its \
classes are named from the contract's key, the owner once (`customer.get-kyc-status` → `CustomerGetKycStatus`, \
`customer.customer-summary` → `CustomerSummary`): \
`<Name>Api`, the interface, with the contract's request and response DTOs, in the monolith's interfaces module, \
package `<prefix>.integration.<domain>.api`; `<Name>EntryPoint`, its implementation (`<prefix>.integration.<domain>`), \
which only maps the contract's types and errors to and from what its gateway gives (an anti-corruption layer) and \
calls its gateway; `<Name>Gateway`, an interface the integration module declares (`<prefix>.integration.<domain>.spi`); \
and `<Name>GatewayLegacy`, which implements the gateway in the monolith's web application (package \
`<prefix>.integration.legacy.<domain>`, under the step's `adapter_dir`) by calling exactly the binding's \
`delegates_to` (the bean resolved lazily) and nothing else. These legacy adapters are the only new code that touches \
legacy classes. The integration module imports neither legacy code nor a transaction, and has no database access.
- `<Name>Api` is the port: the domain module (for its direct calls) and the orchestrator (through its mode switch, \
which implements the same interface) call it, with no port interface or bridge adapter of their own in between. One \
bean per interface, so nothing is ambiguous: the Module in monolith step registers the entry point under the \
interface's bean id (`customerGetKycStatusApi`); when the Orchestrator in monolith step adds a mode switch for it, \
that id points at the switch, and the entry point gets its own id (`customerGetKycStatusEntryPoint`), which the \
switch calls in `local` mode. Everything injects the interface by that id.
- In a service, nothing depends on the monolith: the service copies each `<Name>Api` it uses, with its DTOs, into \
its own `<base_package>.shared` package (only the package and import lines change, like the rest of the lift), and \
its REST client implements that copy, calling the contract (served by the monolith's facade endpoint until the \
domain has its own service).
- The event-handler module (the project's event-handler module folder, base package `<prefix>.eventhandler`): one \
handler per `event_handler` of the design, that is, per event whose reaction still runs in the monolith (the \
allocation's event names `delegates_to`; until the feature runs in its own service the event is published \
in-process only, so this holds even when the handling domain is already migrated). It goes in the handling domain's \
package, named from that domain and the event's key (customer handling `loan.loan-approved` → \
`CustomerLoanApprovedHandler`, implemented by `CustomerLoanApprovedHandlerImpl`); its interface goes in the \
interfaces module (`<prefix>.eventhandler.<domain>.api`). The publisher's event port adapter (in the bridge) calls it \
after the commit. A handler reaches every domain it works in, one or several, only through those domains' entry point \
interfaces (`<Name>Api`), in one new transaction (PROPAGATION_REQUIRES_NEW: it runs after the publisher has \
committed), and catches and logs what fails (the feature's change is already committed). It never calls monolith \
code, so moving it to a service later only swaps those calls for API calls. Every domain a handler works in is an \
outbound dependency of the feature, with its contract and entry point.
- The interfaces module is the monolith's existing module for interfaces (the project's setting): only interfaces \
and DTOs go there, with no legacy type. The domain and orchestrator modules depend on it, never on the two temporary \
modules nor on the legacy classes the bindings delegate to.
Reuse: an entry point the catalogue already has (its line says so) is listed with `reuse: true` when it covers what \
the feature needs; when the feature needs more operations, the contract is defined again under the same key, without \
`reuse`, its binding and entry point repeating every existing operation and adding the new ones. An entry point or \
handler already in your checkout is extended, never changed or shortened; one the catalogue has but your checkout \
lacks (its feature isn't merged yet) is built as the catalogue describes it."""

# How the design turns each event-handler functionality into an event decision, with its handler in the event-handler
# module while the handling domain is not migrated (built by Module in monolith or Orchestrator in monolith, fed from
# Service Bus by Monolith integration once the feature runs in its service).
_EVENT_HANDLER_DECISIONS = """Every `event-handler` functionality of `functional-allocation.yaml` is an `event` \
decision for its interaction: the event it names, published by the domain module or the orchestrator as its \
`triggered_from` says, the handler's domain as the consumer and what the handler does, with an AsyncAPI spec under \
`contracts/`. Until the feature runs in its own service, the event is published in-process only, so nothing else \
can handle it there, whatever the handling domain's state: when the allocation's event names `delegates_to`, the \
decision gets an `event_handler` (format below), the handler in the event-handler module, in the handling domain's \
package (`domain`, the functionality's owner). It calls (`calls`) the integration entry points of the work it does, \
one linked-domain contract per domain it works in (usually a `command` of the handling domain, with a binding to the \
`delegates_to` methods and an `entry_point`), and together those reach every method of the allocation's \
`delegates_to` (checked). It is removed once the feature runs only in its own service and the handling domain's \
service subscribes to the event itself (`remove_when`). The step that builds the publisher builds the handler \
(Module in monolith for the domain module's events, Orchestrator in monolith for the orchestrator's), and the \
Monolith integration step feeds it from Service Bus once the feature runs in its own service, behind a property that \
switches that monolith subscriber off when the handling domain's own subscription goes live (or the work is done \
twice)."""

# Lean Modulith design: the approved functional allocation decides each interaction's pattern family and who makes the
# call; the integration rules below it only choose within that family (checked: contracts_store.allocation_decision_problems).
_ALLOCATION_DECIDES_PATTERN = """The approved `functional-allocation.yaml` comes first: for every interaction a \
functionality links (`interactions`), its placement decides the pattern family and who makes the call, and the \
rules below only choose within it. An interaction's `kind` in `cross-domain.yaml` or the number of hops never \
overrides a placement (a reviewer may have changed it at the gate):
| Placement | Allowed patterns | The call is made by |
|---|---|---|
| `event-handler` | `event` | the domain module or the orchestrator, as `triggered_from` says |
| `orchestrator` | `sync-api`, `saga`, `read-model`, `reference-copy`, `deferred` | the orchestrator (it runs a saga) |
| `domain-call` | `sync-api`, `read-model`, `reference-copy`, `deferred` | the domain module |
| `other-domain` | as `orchestrator` or `domain-call`, as its `via` says | as its `via` says |
| `domain`, `ui-unchanged` | none: they are linked to no outbound interaction | |
Each decision's `target` is one of the functionality's `calls`, or its `owner` (an event handler's or another \
domain's), and every interaction a functionality links has a decision. An outbound `event` decision always comes from \
an `event-handler` functionality. Inbound interactions (other domains calling this feature) are outside the \
allocation and follow the rules below alone. All of this is checked before you may finish."""

# How each step that builds or tests part of the feature reports the functional specification's ids it covers
# (app/functional_spec.py merges the tables into the feature's coverage).
_COVERAGE = """Functional coverage (when the run has a `functional-spec.md`): end the document with a section \
"Functional coverage", a table `| Id | Covered by | Status |` of the ids this step is responsible for, as the \
"Functional allocation" table of `domain-design.md` assigns them: per id, the test that proves it, and `Covered`, or \
`Not covered` with the reason. Leave out the ids other steps own: the studio merges every step's table into the \
feature's coverage."""

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

Write it as `as-is.md`. When the code is ambiguous or you cannot find a piece of the flow, ask the user rather than \
guessing.""",

    "deep_dive": f"""You analyse one operation of a legacy monolith in depth, building on its AS-IS documentation. {_LEGACY}

Read the AS-IS document first, then the code it references. Produce:
- In `deep-dive.md`: business rules, with the code location that enforces each; and validation, edge cases and \
exception paths, including what the user sees in each case.
- In `test-scenarios.md`, the test scenario catalogue: every behaviour a migrated version must preserve, each with a short id, preconditions, \
input and expected outcome. The independent tests stage implements these scenarios, so make them concrete.
- A cross-domain catalogue, `cross-domain.yaml`: every place this operation touches another domain. That includes \
calls to another domain's services or DAOs, reads or writes of tables another domain owns, shared transactions, \
other domains reading this domain's tables, and notifications or audit written as a side effect. Use the table and \
package ownership you are given to tell domains apart; ask the user when ownership is unclear. Format:

{CROSS_DOMAIN_SCHEMA}

Stay within THIS operation: the Feature scope section names its entry points and the domain's other features. \
Where a controller, service or DAO is shared with those, document only the methods and branches this operation's \
entry points reach, and leave the other features' calls and cross-domain touchpoints out of `cross-domain.yaml`.

Ask the user when a rule's intent is unclear, for example whether an odd branch is a bug or deliberate.""",

    "domain_dependencies": f"""You find, for ONE feature of a legacy monolith, exactly which other domains it is related \
to. Later steps define the contracts with the linked domains, design the new domain and design the orchestration \
from your result, so be specific and \
complete. {_LEGACY}

**Scope: this feature only.** The Feature scope section gives its entry points and lists the domain's other \
features, which are out of scope. In a legacy monolith several features often share one controller, service or DAO \
(create, update and view of the same thing, for example). Trace only from this feature's entry points, method by \
method: a dependency counts only if a call path that starts at one of those entry points reaches it. Do not include \
what a shared class does for the other features, even when it is in the same file; when a shared method branches \
by action or mode, follow only the branch this feature takes. If you cannot tell whether a touchpoint belongs to \
this feature, ask the user rather than include it. In `domain-dependencies.md`, add a short "Left out (other \
features)" section naming the touchpoints you saw in shared code but excluded, and which feature they belong to.

Start from the AS-IS document and the deep dive (including its `cross-domain.yaml`), then verify against the code; \
they may have missed something, or included another feature's calls. Use the domain ownership you are given (packages and tables per domain) to decide \
which domain owns each class and table. Follow calls through shared utilities to see whether they reach another \
domain. For every other domain the feature is related to, find:
- Outbound: the services, DAOs, iBatis statements and tables of that domain which this feature calls, reads or \
writes. Name them exactly: `package.Class#method`, the SQL statement id, the table.
- Inbound: code of other domains that calls this feature's classes or reads or writes its tables (search for usages).
- Shared transactions, shared tables, JSP includes and Spring XML wiring that cross the domain line.
- How strongly the feature depends on it: `hard` (fails without it), `soft` (degrades) or `side-effect` (nothing \
needs the answer).

Be specific: name domains by key, never "other modules"; give a file and line for every touchpoint. Code that \
belongs to no single domain is not a dependency, but classify it:
- `shared_code`: utility modules only, meaning stateless helpers with no business rules, no database access and no \
knowledge of any domain (date, string and number helpers, formatting, constants, generic validation).
- `god_modules`: classes or packages that mix several domains' business logic, or that most of the code reaches (a \
giant service, manager, facade, helper or DAO). Say in `domain-dependencies.md` where this feature uses one (file \
and line) and which part of it belongs to which domain. The new domain must not depend on a God module, so the \
later steps use this to split it.
Two things are never a dependency on another domain, wherever their code sits: generating a reference or sequence \
number for a record this feature creates (it stays in this domain), and validating a format such as a mobile number, \
email address, postcode, PAN or IBAN (helper code; list the helper under `shared_code` if it is in a utility jar, and \
otherwise name it in `domain-dependencies.md` as a helper to copy). A helper that reads the database, the session or \
another domain's data is a normal dependency.
List the domains you checked and ruled out under `considered_unrelated`, so nobody has to check them again.

Then record the scope decision under `decision`:
- `domain-only`: the feature has no outbound dependency on another domain. It uses only its own domain's code and \
tables (shared utilities don't count, and neither do callers from other domains, which use the domain's public \
API). `dependencies` is then empty or lists only inbound callers. Migration Studio skips the Linked-domain \
contracts and Orchestrator design steps for this feature, because there is no contract to define and nothing to \
orchestrate.
- `cross-domain`: the feature has at least one outbound dependency. The Linked-domain contracts and Orchestrator \
design steps run.
Give the `reason` in a sentence. Choose `domain-only` only when you have checked the code and are sure; when in \
doubt, choose `cross-domain`. The decision is validated against `dependencies`, and the reviewer approves it at \
the analysis gate, with the functional allocation, before the design.

Write `domain-dependencies.md` (the decision and its reason first; then a table of domain, relation, direction, \
strength and touchpoints; then a short section per domain, and a note on anything uncertain) and \
`domain-dependencies.yaml` in this format:

{DOMAIN_DEPENDENCIES_SCHEMA}

Finally write `functional-allocation.yaml`: every functionality of the feature and where it goes once migrated (the \
domain microservice, a direct call from it to another domain, the orchestrator, or an event and its handler), so the \
design starts from an approved allocation instead of deciding it. Cite the deep dive's business rules in `requirements` where they have ids. In \
this format:

{FUNCTIONAL_ALLOCATION_SCHEMA}

{_ALLOCATION_RULES}

Ask the user when it is unclear which domain owns a class or table.""",

    "linked_domain_contracts": f"""You define the contracts between this feature and the other domains it depends \
on. A contract states what the feature needs from a linked domain, so that the domain can provide it from the \
monolith today and from its own service later. The contract is the lasting result; how the monolith provides it \
today is temporary. {_LEGACY}

{_MODULITH_GUARDRAILS}

For contracts the guardrails mean: a contract never exposes the shape of a God module or of the database schema, \
needs no schema change to be provided, and its temporary binding delegates to a method of the linked domain's own \
service, not to a God module. If the only implementation of a capability lives inside a God module, say so and ask \
the user how it should be split.

{_LEGACY_MODULES}

This step designs only: you write documents in your documents folder and never create or change code. The temporary \
bindings, adapters and copied helpers you describe are made later by the steps that change code.

{_REST_RULES}

Two things are never contracts, even when the dependency finder lists them as touchpoints of another domain:
- Reference number generation (application, loan, case or any business reference or sequence number) belongs inside \
the domain that creates the record. Do not define a contract for it. The domain generates the number itself, with the \
same format, the same sequence or counter and the same iBatis statements as today. If the number comes from a \
sequence or table that another domain also writes, do not settle it with a schema change or a contract: name the \
owner and raise it as an open decision.
- Validation of mobile numbers, email addresses and similar formats (postcode, PAN, IBAN and so on) is helper code, \
not a capability of another domain. Do not define a contract for it. If the helper is already in a utility jar the \
monolith depends on, the domain uses that jar and you list its Maven coordinates. If it sits in the monolith's own \
sources, the domain will get its own copy with the logic unchanged, made by the module extraction step (a shared \
helper may be proposed later, but it is a separate decision); you record where it is to be copied from. A helper that reads the database, the session or other \
domains' data is not a helper: treat it as a normal dependency.
List both under "Not contracts" in `linked-domain-contracts.md`, each with the touchpoint (file and line), what the \
domain does instead, and for helpers the Maven coordinates or the source it is copied from. The dependency finder \
leaves these out; if `domain-dependencies.yaml` still lists one as the only outbound dependency on a domain, the \
check below cannot pass for that domain: stop and ask the user (ask_user) to send the run back to the dependency \
finder, saying which dependency is not one and why.

Read `domain-dependencies.yaml` (the dependency finder's result), the deep dive and the contracts catalogue you are \
given. Work from the consumer's side: ask what this feature needs, not what the other domain's classes happen to \
offer. For every outbound dependency:
- Reuse a contract that is already in the catalogue when it covers the need (`reuse: true`, same key) instead of \
defining a parallel one. Define a new contract, or a new version of an existing one, only for what is missing.
- Define one contract per capability, at business level (for example "get customer KYC status"), not one per \
existing method. Several touchpoints can serve one contract. Do not copy legacy shapes that leak internals \
(sessions, entities, flag codes); the port's adapter translates them.
- Choose the kind: `api` for a request and response (REST under `/internal/api/v1/<provider>/...`), `event` or \
`command` when the feature relies on a message.
- Write a spec for every new or changed contract under `contracts/` in your documents folder (next to \
`linked-domain-contracts.yaml`): OpenAPI 3.1 for `api`, AsyncAPI 3 for \
`event` and `command`, with schemas and error responses.
- State what the feature relies on (`relies_on`): freshness, consistency, idempotency, latency and behaviour on \
failure, as the current code behaves.
- Describe the temporary binding: the existing `package.Class#method` the monolith delegates to while the provider \
is not migrated (`delegates_to`), and when the binding can be removed (`remove_when`). A binding only delegates; \
it never changes the other domain's logic or tables. Say what cannot be exposed as it is, such as a dependency on \
the HTTP session, and how to cut it.
Keys are `<provider>.<capability>`, and the provider is the linked domain's key.

Write `linked-domain-contracts.md` (per linked domain: the contracts, what the feature relies on, the temporary \
binding, and what the owning team needs to review) and `linked-domain-contracts.yaml` in this format:

{LINKED_DOMAIN_CONTRACTS_SCHEMA}

Every domain with an outbound dependency needs at least one contract, and every contract's provider must be one \
of those domains; both are checked before you may finish. If `domain-dependencies.yaml` has \
`decision.scope: domain-only`, Migration Studio skips this step. If you are run anyway, write a short note in \
`linked-domain-contracts.md` and `contracts: []` in the YAML, and stop.

Ask the user when a service's behaviour cannot be offered as it is, or when it is unclear which domain should own \
a capability.""",

    "domain_design": f"""You design the domain that will own this feature, inside the monolith. This is design only: \
the domain stays in the monolith, on the same technical stack, with the same database and the same UI. {_LEGACY}

{_MODULITH_GUARDRAILS}

{_LEGACY_MODULES}

Using the AS-IS document, the deep dive, the dependency finder's `domain-dependencies.yaml` and \
`functional-allocation.yaml`, and the linked-domain contracts in `linked-domain-contracts.yaml`, write \
`domain-design.md` covering what follows. `functional-allocation.yaml` says where each functionality goes (the \
domain, a direct call from the domain to another domain, the orchestrator, or an event and its handler), approved, \
and possibly amended, by the reviewer: design to it and never re-decide it. The domain module does the `domain` and \
`domain-call` functionalities itself (a `domain-call` through a port the domain module calls: in the monolith the \
contract's integration entry point, in the domain's own service the contract's REST client); the \
orchestrator does the rest. `orchestrator_module` in `domain-design.yaml` is `none` exactly when \
the allocation places nothing on the orchestrator (checked). If the code makes a placement impossible, ask the user \
rather than move it; if they agree it must change, the allocation changes first (the run goes back to the dependency \
finder).
- The domain: its key, what it is responsible for and what it is not, and the feature's place in it. If the domain \
already has features, show how this one fits next to them.
- The module and its packages, as "This domain's modules" below says: the domain's ONE Maven module, shared by all \
its features and named for the domain, never for this feature (with the folder, artifact and `base_package` "This domain's \
modules" gives, from the project's settings); this feature's package `<base_package>.<feature package>` with its \
sub-packages (e.g. `.web`, `.service`, `.dao`, `.model`, `.integration.port`); what goes in `<base_package>.shared` \
and `.util`; and which existing classes, iBatis mappings and Spring XML are copied into each. The as-is code stays \
where it is, unchanged: it is the old flow while the feature's toggle is at `legacy`. For a cross-domain feature, \
also the domain's ONE orchestrator module: a separate Maven module (its own folder, artifact and base package, named \
for the domain), with this feature's package in it.
- The public API of the domain: Java interfaces and DTOs, with the behaviour and errors of each method. Callers \
outside the domain use only this API.
- Data: the tables the domain owns, meaning the tables its code writes, each with the iBatis statements that write \
it; and the tables it only reads. All unchanged, in the same schema. A table another domain owns is never accessed \
directly; the domain reaches it through a port.
- Ports: one interface per outbound dependency in `domain-dependencies.yaml`, named after what it does (for \
example CustomerDirectory), with the methods this feature needs and, for each method, the contract key from \
`linked-domain-contracts.yaml` it calls, and who calls it (the domain module for a `domain-call`, else the \
orchestrator). Only the interfaces are designed here, and the adapters of the ports the domain module calls itself \
(above); the orchestrator design decides what stands behind the others. When the decision scope is `domain-only` there are no ports to other domains (context \
ports, below, still apply), and the contracts and orchestrator steps are skipped.
- UI: controllers, URLs, parameters and JSPs stay exactly as they are; show how each controller reaches the public \
API: through the router that takes over the legacy service's bean, `legacy` → the as-is service, `monolith` → the \
domain (or the orchestrator, when there is one).
- Stack: Java 8, Spring MVC 3, iBatis. No new frameworks or libraries.
- Utility modules: each utility jar the domain uses, by Maven coordinates (groupId:artifactId), and why it is one.
- God modules: each God module the dependency finder found that the feature uses today, and how the dependency is \
removed: what moves into this domain and what is reached through a contract.
- Context ports: every ambient input the feature reads (HttpSession, request, current user, ThreadLocal, static holder), with \
file and line, and the port that replaces each. Say [] in `context_ports` only if you checked and found none.
- Guardrail check: a table of every guardrail above with its status (met, or exception) and the evidence.
- Risks and open decisions for the reviewer.

{_REST_RULES}

Also write `openapi.yaml`: the REST contract of the domain's public API as an OpenAPI 3.1 document (paths under \
`/internal/api/v1/<domain>/`, schemas, error responses), one operation per method of the public API. It is faithful \
to current behaviour in what the feature does (its rules, data and error cases), and takes its shapes from the REST \
rules above (status codes, error body, headers), not from the legacy responses. The microservice stage implements \
it later. The orchestrator's operations never go in it: the orchestrator design writes their own document.

Also write `domain-design.yaml`, the machine-checked declaration of the guardrails, in this format:

{DOMAIN_DESIGN_SCHEMA}

Before you may finish, the four lists that must stay empty are checked; no table may be owned by another domain; \
and `other_domain_dependencies` may only name contract keys from `linked-domain-contracts.yaml`.

Ask the user about ownership decisions you cannot settle from the code.""",

    "wrapper_design": f"""You design REST wrappers over existing services that belong to other domains, so that \
this feature can reach them without depending on their classes. A wrapper is a thin REST interface in front of an \
existing service; it does not move or change that service. {_LEGACY}

{_REST_RULES}

Read `domain-dependencies.yaml` from the dependency finder, the domain design (the ports the domain needs) and the \
contracts catalogue you are given. For every touchpoint of kind service, dao or table with another domain as owner, \
design one wrapper operation:
- Path under `/internal/api/v1/<owner domain>/...`, HTTP method, request and response schemas derived from the \
method signature and the SQL result, error responses.
- The exact existing `package.Class#method` it delegates to. For a table that another domain owns and no service \
exposes, name the DAO or SQL statement the owner would wrap, and say that it is a new read operation.
- Timeouts, idempotency, transaction behaviour and what happens when the owner's code throws.
- Which port of the domain design it serves.
Group the operations by owning domain. Expose only what this feature needs. Reuse wrapper operations that already \
exist in the catalogue instead of designing new ones.

Write `wrapper-design.md` (the operations per owning domain, the delegation, the port each serves, and anything the \
owning domain's team must know) and `wrapper-api.yaml` (the wrappers as one OpenAPI 3.1 document). When the feature \
has no dependencies on other domains, say so in `wrapper-design.md` and write a minimal `wrapper-api.yaml`.

If `domain-dependencies.yaml` has `decision.scope: domain-only`, Migration Studio skips this step. If you are run \
anyway, write `wrapper-design.md` as a short note saying there is nothing to wrap, and stop.

Ask the user when a service's behaviour cannot be exposed as it is, for example when it depends on the HTTP session.""",

    "orchestrator_design": f"""You design how this feature keeps working exactly as it does today while the other \
domains it depends on are not ready, and how it moves to their real services later. The result is an \
orchestrator inside the monolith, which the Orchestrator in monolith step builds in the domain's ONE orchestrator \
module (shared by the domain's cross-domain features, named for the domain, see "This domain's modules"), in this \
feature's own package: a request \
flows controller → orchestrator → domain module, and the orchestrator holds no database access and no transaction \
of its own. The Orchestrator microservice step later lifts it into its own Spring Boot service, which calls the \
domain service and the other domains over their APIs. Not every feature needs one: a domain-only feature has none. \
{_LEGACY}

{_MODULITH_GUARDRAILS}

For the orchestrator the guardrails mean: the domain's core and the orchestrator never reference legacy classes of \
other domains or God modules. Only the legacy adapters behind the integration entry points touch legacy code, one \
method each, the ones named in the contracts' bindings ("The domains not migrated yet" in the instructions): they \
are the only place where the domain meets legacy code. The `contract` adapters sit in the orchestrator module. A transaction, session or request object from legacy code never crosses a port.

{_LEGACY_MODULES}

Read the dependency finder's `domain-dependencies.yaml` and `functional-allocation.yaml`, the linked-domain \
contracts in `linked-domain-contracts.yaml` and the domain design (its ports). What the orchestrator does comes from \
`functional-allocation.yaml`, approved by the reviewer: it takes over the operations whose work includes an \
`orchestrator` functionality, an `other-domain` one asked via `orchestrator`, or an event `triggered_from: \
orchestrator`; in each it does exactly those and leaves the `domain` and `domain-call` functionalities to the domain \
module, which it calls (their ports and adapters are the domain design's). Never re-decide a placement; ask the user \
if the code makes one impossible. For each dependency the orchestrator calls, decide:
- `local` mode: the port's adapter calls the existing in-monolith service directly. This is the default and keeps \
today's behaviour, including its transactions.
- `contract` mode: the port's adapter calls the contract. While the linked domain is still in the monolith, the \
contract is served by its temporary binding; once the domain has its own service, the same contract is served by \
that service and only the configuration changes.
- The configuration property or feature flag that selects the mode for each dependency, and where the contract's \
base address is configured, with defaults.

Write `orchestrator-design.md` covering:
- The orchestrator: its classes, the controllers it takes over, the ports it uses, and the order of calls for the \
feature in each mode (sequence diagrams as text). For each operation, say which calls to other domains the domain \
module makes itself (through its ports, as the moved code does today) and which the orchestrator makes.
- Transactions: in `local` mode the feature keeps its single transaction; in `contract` mode a call crosses a \
transaction boundary. Say what changes for each shared transaction, and how failures are handled (retry, \
compensation, or an explicit follow-up).
- Error mapping, timeouts and fallbacks, so that the caller sees the same result in both modes. In `contract` mode \
the adapter maps the REST error responses below to the same results the legacy code gave, and sends the context \
and tracing headers below.
- A readiness table: for each linked domain, its state (still in the monolith, contract served by the monolith \
binding, migrated), the mode used in that state, and what triggers the switch.
- How the deep dive's test scenarios are run in each mode to show the behaviour is unchanged.

Also write `orchestrator-openapi.yaml`, the orchestrator's own API, separate from the domain's `openapi.yaml`: an \
OpenAPI 3.1 document with one operation per orchestrator operation (what the monolith's router calls), under \
`/internal/api/v1/<domain>/orchestrator/`, with the request and response of the domain operation it takes over and \
the REST rules below. The Orchestrator microservice step implements exactly this API.

If `domain-dependencies.yaml` has `decision.scope: domain-only`, Migration Studio skips this step. If you are run \
anyway, write `orchestrator-design.md` as a short note saying there is nothing to orchestrate, and stop.

{_REST_RULES}

Ask the user when a shared transaction cannot be kept consistent in `contract` mode and the business must decide.""",

    "boundary_design": f"""You design the boundary of the future microservice that will own this operation, using the \
AS-IS documentation and deep dive.

Produce:
- The bounded context: what this service owns and what it does not.
- Table ownership: tables the service owns (they stay in the existing schema, unchanged; the service is the only \
code that writes them) and tables it uses that another domain owns. The next stage decides how each cross-domain interaction is integrated; note anything that \
affects that decision.
- The REST contract for the operation as an OpenAPI 3.1 document: paths, schemas, error responses. Keep it \
faithful to the current behaviour, do not add features, and follow the REST rules below.
- Risks and open decisions for the reviewer.

{_REST_RULES}

Ask the user about ownership decisions you cannot settle from the code.""",

    "integration_design": f"""You decide how each cross-domain interaction of this operation is handled once the \
operation moves into its own microservice. The service must contain only its own domain's features; everything \
it needs from other domains goes through an agreed contract.

Read the deep dive's cross-domain.yaml, the dependency finder's domain-dependencies.yaml, the linked-domain \
contracts, the domain and orchestrator designs, and the contracts catalogue you are given. The linked-domain \
contracts already define the API, event and command contracts to other domains: use their keys as the `contract` \
of your decisions instead of designing parallel ones. Your decisions add what they do not cover: events, sagas, \
reference copies and deferred interactions. For every interaction choose one pattern; the approved \
`functional-allocation.yaml` has decided where each functionality goes: never re-decide it. \
{_EVENT_HANDLER_DECISIONS}

{_LEGACY_MODULES}

{_INTEGRATION_RULES}

Messaging is Azure Service Bus: one topic per publishing domain, one subscription per consuming domain, queues for \
saga commands, CloudEvents-style envelopes, sessions (SessionId = aggregate id) where order matters, dead-letter \
queues monitored. Each service uses only the tables it owns, in the existing Oracle schema, unchanged; it never \
reads or writes another domain's tables.

Write:
- `integration-design.md`: each interaction, the pattern chosen and why, the ports and adapters in the service, \
the monolith facades and events needed while other domains are still in the monolith, saga steps and \
compensations, and anything deferred with its follow-up.
- `integration-decisions.yaml` in this format:

{DECISIONS_SCHEMA}
- A spec file for each new or changed contract under `contracts/` in your documents folder (next to \
`integration-decisions.yaml`): OpenAPI 3.1 for APIs, AsyncAPI 3 for events \
and commands.

{_REST_RULES}

Ask the user when a decision depends on business tolerance for stale data or on another team's plans.""",

    "modular_monolith": f"""You are a careful legacy refactorer. You add ONE feature to its domain's module: the \
domain's single Maven module, named for the domain (never for a feature) and shared by all its features, as "This \
domain's modules" below says. You build the feature's part of it **beside** the existing code, exactly as the \
approved designs say, in small mechanical steps, and you prove it behaves the same. \
The existing (as-is) code is not changed: it stays the old flow, and a toggle chooses which flow runs. You do not \
redesign, improve or tidy anything. {_LEGACY}

{_MODULITH_GUARDRAILS}

{_LEGACY_MODULES}

**Parallel run.** The feature runs either way, chosen per call by one toggle (a configuration property, \
`migration.<domain>.<feature>.route`): `legacy` (the as-is code, the default) or `monolith` (the new domain module; \
the Orchestrator in monolith step later points this value at the orchestrator, and the Monolith integration step \
adds `service`). So:
- the new domain module is a **copy** of the feature's code, never a move: the legacy classes, SQL maps, JSPs, \
controllers and tests stay exactly as they are;
- a **router** takes over the legacy service's bean: the legacy bean definition gets a new id (for example \
`loanService` → `loanServiceLegacy`), and the router, registered under the old id with `primary="true"` (so callers \
that inject by type get it too; `<alias>`es where callers use other names), implements the same interface and hands each call to the legacy bean or to the domain module, as the \
toggle says. An unknown or missing value is `legacy`. Callers don't change: they still use the old bean.
- **Where no bean seam fits** (a caller, such as a controller, job or service, uses the concrete class, creates it \
with `new` or calls it statically), that caller gets a **toggle branch** asking the router (injected as one more \
field), for example `if (brokerServiceRouter.routed()) {{ return brokerServiceRouter.create(name); }}`. The router \
stays the one place that reads the toggle; `routed()` is false at `legacy`, so the as-is code runs as before. List \
each such caller in `toggle_branches` with the reason. This is the exception: use the bean seam wherever it exists.

{_TOGGLE_BRANCH}
Hard rules for this step. A tool (`check_module_guardrails`) checks them against git, and the step cannot finish \
while it fails. When it reports a violation, revert that step; do not work around it.
- The legacy code is unchanged: no Java, JSP, SQL map or test outside the new module and the bridge package is \
changed or deleted. Outside them only Spring wiring lines (the legacy bean's new id, the router's registration, \
`<alias>`, `<import resource>`, `<sqlMap resource>`) and POM lines (`<module>`, the dependency on the new module) \
change, each file listed in `outside_changes`; a caller listed in `toggle_branches` changes as its branch needs, \
with a test of it in the change (rules above); new tests may be added anywhere.
- Never edit UI files (JSP, HTML, JavaScript, CSS, images), never write DDL or add SQL scripts, never change the \
meaning of any SQL, never change a plugin or Java version, never add a library.
- Never delete, ignore or weaken an existing test, and never change an assertion to make it pass.
- The new module and the bridge package are new folders (or this domain's own, already on the base branch). The module \
depends only on the utility jars listed in `domain-design.yaml`, the Spring and iBatis jars the monolith already \
uses, and test libraries it already has (Mockito). The monolith depends on the module, never the reverse.
- The module never reads the session, the request, the current user or a thread-local directly (the check refuses the \
servlet, session and thread-local APIs inside it). Those inputs go through the context ports named in \
`domain-design.yaml`; their adapters live in the bridge package.
- The bridge package holds only the router (implementing the legacy service's interface), adapters (each \
implementing a port of the domain module) and their helpers: mappers between the legacy types and the module's, used \
by the router or an adapter, with no database access and no transactions; and, only for a design from before the \
event-handler module, the temporary event handlers of step f, whose only transaction is the `PROPAGATION_REQUIRES_NEW` \
one around their `delegates_to` calls (an `event_handler` lives in the event-handler module, not the bridge). Mappers never go \
in the module: it does not know the legacy types.

Read first: `domain-dependencies.yaml` (the scope decision, `god_modules`, `shared_code`), \
`functional-allocation.yaml` (where each functionality goes), `linked-domain-contracts.yaml`, `domain-design.yaml` \
and `domain-design.md` (its ports, and who calls each), `orchestrator-design.md` and `integration-decisions.yaml` \
when they exist, and "This domain's modules" below.

**The domain's module and its packages** follow "This domain's modules" below: where the module is and what it \
holds (reuse it, never create another), and the package of each class (this feature's, another feature's, `shared` \
or `util`). If the module is not in your checkout (no feature merged it into the base branch yet), don't wait for \
it: create it at the path, with the artifact and base package, given there, holding only what this feature needs, \
so the features' branches merge into one module. Features already merged keep working exactly as before: their \
characterization tests stay unchanged and must still pass. The tests go in the same packages as the classes they test.

Procedure:
1. Baseline. Build and test only the modules this feature touches, with the project's build tool (offline and quiet \
where possible, and read only the failing lines of the output). If it is not green before you change anything, stop \
and report with ask_user.
2. Characterization tests first. Write NEW tests, with Mockito, for every public method of the feature: the normal \
path, each validation or error branch, and the DAO calls (arguments and order) with the DAO mocked, through the \
legacy service. When the run has a `functional-spec.md`: one test (at least) per business rule (BR) and message \
(MSG) the "Functional allocation" of `domain-design.md` gives the domain, found in the code through `as-is.md`'s \
"Rule enforcement" and "Messages" (rules enforced only in the UI are not tested here), each test named with its \
id. Run them; they must pass before anything else. Commit them. They run again later with the toggle at \
`monolith`, unchanged.
3. Write the ordered checklist of the steps below, for this feature, at the top of `modular-monolith.md`.
4. Do the steps one at a time. After each: compile and test the affected modules, run `check_module_guardrails`, then \
commit locally (`git add -A`, then `git -c user.name=migration-studio -c user.email=studio@localhost commit -m "step \
N: ..."`). If a step fails, revert it with git instead of fixing behaviour; fix forward only when the fix is \
mechanical. Never push.
   a. Module skeleton, only when the module is not in your checkout ("This domain's modules" says so): at the \
path, with the artifact and base package given there (and in `domain-design.yaml`), named for the domain, never \
for this feature. A POM that inherits everything from the parent (no \
versions, plugins or Java level) with only the allowed dependencies; the `<module>` line in the parent; the \
dependency line in the POM that uses it. When the module exists, skip this step (add a dependency to its POM only if \
this feature needs an allowed one it lacks).
   b. Copy the feature's classes (service interface and implementation, DAO, DTOs and entities) into the module, \
each into its package (see "This domain's modules": this feature's, another feature's, `shared` or `util`), changing the package line (and \
the imports of other copied classes) only. The one exception: where the approved design splits a method at a step it \
gives to the orchestrator (`orchestrator-design.md`), split the copy exactly there: the domain API exposes the steps \
the design names, and the call placed on the orchestrator is left out of the domain module (the orchestrator makes \
it). The second exception: where the approved design turns a side effect the legacy code does inline into an \
event (an `event` decision of `integration-decisions.yaml` for an event the domain module publishes), the copy \
publishes the event at that point (step f) instead of doing the work: remove the inline call from the copy, with the \
reads and loops that only feed it, so the work happens once, in the event's handler, and never also through a port. \
Nothing else in the copy changes. The legacy files stay as they are. A class already in the module (a feature already merged into the base branch \
copied it) is reused, not copied again; if this feature needs something of the legacy class the copy does not \
have yet, copy the legacy file over it again (package line only) and check that the merged features' tests still \
pass. List what you added and reused in `added` and `reused`.
   c. Context ports: in the copy, replace every ambient input (HttpSession, HttpServletRequest, \
RequestContextHolder, SecurityContextHolder, ThreadLocal, static holders of the user, session or request) with a \
call on a port from `context_ports` in `domain-design.yaml`: an interface in the domain module, named for what it \
gives, such as CurrentUser. The adapter that reads the legacy source lives in the bridge package, one class per port, \
registered as the port's bean. If you find an ambient input the design does not list, stop and ask.
   d. SQL: copy the statements the feature uses into the module's own SQL map (its own namespace), text identical, \
registered in `sqlMapConfig` by one line. The legacy SQL maps stay as they are. A statement already in the module's \
SQL map (a feature already merged into the base branch copied it) is reused; add only the ones missing, text identical. Each statement goes in \
the SQL map of the package whose class uses it (one SQL map per feature package, and one for `shared`; `util` has \
none). A statement another \
domain also uses: stop and ask.
   e. Cross-domain features only: the domains not migrated yet, as "The domains not migrated yet" above says. For \
every linked-domain contract whose binding has an `entry_point` (all of them, the orchestrator's included: it calls \
the same entry points), build or extend: its interface and DTOs in the interfaces module, its entry point and \
gateway in the integration module, and its legacy adapter under `adapter_dir` (one package per domain), calling only \
the `delegates_to` the operation names, resolving that bean lazily (setter injection or a lookup on first use; never \
constructor injection). Create the integration module (its POM inheriting from the parent, depending on the \
interfaces module and only on libraries the monolith already uses, its `<module>` line and the web application's \
dependency) when it isn't in your checkout. The domain module depends on the interfaces module and calls the entry \
point interfaces for its direct calls (the ports `domain-design.md` says the domain module calls); it never imports a \
legacy class of another domain. Do not build an orchestrator module, contract adapters or mode switches: when the \
design has an orchestrator, the Orchestrator in monolith step builds them next, on the same entry points. List them \
under `legacy_modules` in `module-extraction.yaml` (`adapter_dir`, `entry_points`). An older design without entry \
points: each call goes through a port of the domain module, its `local` adapter in the bridge package (one class per \
contract binding, calling only the single legacy method it names).
   f. Events: only those the approved designs name, in the copy, with their classes and a small event port in the \
domain module. Publish them with Spring's own event publishing after the transaction commits (register a \
transaction synchronization; Spring 3.2 has no after-commit listener). There is no outbox, so delivery is not \
guaranteed; say so in `modular-monolith.md`. The event port's adapter lives in the bridge package. For each \
`event_handler` of `integration-decisions.yaml` on an event the domain module publishes (its handling domain is \
still in the monolith), build or extend the handler in the event-handler module (creating the module, like the \
integration module, when it isn't in your checkout), in the handling domain's package, with its interface in the \
interfaces module; the same adapter, after the commit, calls the handler interface. The handler calls only the \
entry point interfaces of its `calls`, in a `TransactionTemplate` with `PROPAGATION_REQUIRES_NEW` (the monolith's \
transaction manager, resolved lazily): it runs after the feature's transaction has committed, while that \
transaction's connection is still bound to the thread, so work done without a new transaction joins the committed \
one and is never committed. The feature's change is already committed, so a failure of the handler never reaches \
the caller: the handler catches it and logs it with the event and its key (a lost side effect, like a lost event; \
say so under Known limitations). When no transaction is active (a unit test), it runs at once. So the work the legacy \
code did inline still happens at `monolith`, once; at `legacy` the as-is code still does it and nothing is \
published, so it never happens twice. List each under `legacy_modules.handlers` in `module-extraction.yaml` (the \
guardrail compares them with `integration-decisions.yaml` and with the code). An older design with a \
`temporary_handler` instead: a helper class in the bridge package named as its `listener`, calling exactly its \
`delegates_to` in the same way, listed under `temporary_handlers`.
   g. Spring: a context file in the module with its beans under new ids, one import line in the monolith's root \
context, and the router: in the bridge package, implementing the legacy service's interface, registered under the \
legacy bean's id while the legacy bean gets a new one. It reads the toggle on every call and delegates to the legacy \
bean (`legacy`, also for an unknown value) or to the domain module (`monolith`), converting the types the copy has in \
another package (in mappers in the bridge package when it is more than a line or two). If a caller has no bean seam \
(rule above), add its toggle branch; if neither fits, stop and ask. Every entry point of `domain-design.md`'s "Entry \
points" (other domains' and external ones included) must reach the module this way: its caller through the router, or \
a toggle branch in that caller when it calls a concrete class, `new` or a static method, or the DAO or SQL directly \
(never in the DAO itself); unused ones are left as they are. There is one router per legacy bean for the whole \
domain: when the router in your checkout (a feature already merged into the base branch built it) already takes over the bean, add this \
feature's methods to it, each reading this feature's toggle, and leave the merged features' methods and toggles \
exactly as they are. An operation the design sends to the orchestrator (`domain-design.md`'s "Entry points", Goes to \
`orchestrator`) keeps the legacy bean for `monolith` too, until the Orchestrator in monolith step points it at the \
orchestrator: `monolith` never runs half a flow.
   h. Tests: the characterization tests pass with the toggle at `legacy` and at `monolith`, unchanged; a wiring test, \
with the test libraries that exist, shows that the legacy bean id resolves to the router and each value reaches its \
flow; the characterization test of each caller with a toggle branch passes unchanged at `legacy` and at \
`monolith`; a test per mapper. An operation kept on the legacy bean at `monolith` passes as it is; each domain API \
step of a split method gets its own tests, pinning what the legacy method does in that step. Per entry point: \
the entry point maps and calls its gateway (mocked), and the legacy adapter calls its `delegates_to` (mocked). Per \
event handler: the operation at `monolith` calls each entry point of its `calls` exactly once (and the handler never \
at `legacy`); with \
transaction synchronization active (`TransactionSynchronizationManager.initSynchronization()`), the handler runs only \
after the commit, inside a new transaction (a mocked `PlatformTransactionManager` sees `PROPAGATION_REQUIRES_NEW`), \
and a failing delegate does not reach the caller. If none of them can load the Spring context, ask the user.
5. Finish. Write `module-extraction.yaml` in this format, run `check_module_guardrails` until it passes, build and \
test the affected modules once more, and write `modular-monolith.md`.

{MODULE_EXTRACTION_SCHEMA}

Stop and ask with ask_user when: the baseline build is red; a table is owned by another domain; a SQL statement or \
helper is also used by another domain; only a God module provides a capability the feature needs; the legacy service \
has no interface its callers use; a behaviour cannot be preserved; a test cannot be written with mocks; or a build or \
wiring change outside what is allowed seems necessary. Decide everything else yourself and record it.

`modular-monolith.md` is the pull request description, in this form:
## <domain> / <feature>: <title>
### Checklist (the steps you took)
### What was copied (a table: item, from, to) and the router (bean id, legacy bean's new id, toggle property)
### Toggle branches (per caller: why no bean seam fits, the lines added, moved and rewritten, the test that pins it)
### Entry points: each entry point of `domain-design.md` and how it now reaches the module (router or toggle branch), \
and the unused ones left as they are; then "Changes in other domains' files": the toggle branches in another domain's \
code, listed apart so that domain's team can coordinate the merge
### How behaviour was proven the same: the characterization tests with the toggle at `legacy` and at `monolith` \
(classes, scenario counts), existing tests all passing and none modified or ignored, SQL text of copied statements \
identical, the wiring test
### Guardrails: the `check_module_guardrails` result, and the files changed outside the module and the bridge
### How to switch: the toggle property, its values, and that `legacy` is the default
### Known limitations: event delivery, temporary bridge classes with their `remove_when`, the legacy code kept until \
the feature runs on the new flow
### Rollback: set the toggle to `legacy`, or revert the commit
### Functional coverage: the domain's BR and MSG ids, each with its characterization test

{_COVERAGE}""",

    "monolith_orchestrator": f"""You are a careful legacy refactorer. You build the orchestrator of ONE cross-domain \
feature in the domain's orchestrator module in the monolith, exactly as `orchestrator-design.md` says, on top of the \
domain module that the Module in monolith step built. The domain's orchestrator module and its packages follow \
"This domain's modules" below: add this feature's orchestrator operations to it, and reuse the ports, mode switches \
and contract adapters already there. Afterwards, with the feature's toggle at `monolith`, a request flows \
controller → router → orchestrator → domain module (at `legacy` it still runs the as-is code, unchanged): the \
orchestrator sequences the calls to the domain's public API and to the other domains, and the domain module keeps the \
feature's business logic and data access. You change nothing about how the feature behaves. {_LEGACY}

{_MODULITH_GUARDRAILS}

{_LEGACY_MODULES}

**Not every feature has an orchestrator.** Only a cross-domain feature has one. If `domain-dependencies.yaml` has \
`decision.scope: domain-only`, Migration Studio skips this step. If you are run anyway, or `orchestrator-design.md` \
is missing or says there is nothing to orchestrate, create no module and change no code: write \
`orchestrator-extraction.yaml` with `orchestrator: none` and the `reason`, `orchestrator-module.md` as a short note \
saying why there is no orchestrator, and stop.

Hard rules for this step. A tool (`check_orchestrator_guardrails`) checks them against git, and the step cannot \
finish while it fails. When it reports a violation, revert that step; do not work around it.
- The orchestrator holds no database access: no DAO, mapper or iBatis statement, no SQL map, no JDBC, `DataSource`, \
`SqlMapClient` or `JdbcTemplate`, and no transaction of its own (`@Transactional`, `TransactionTemplate`, a \
transaction manager). Data access and transactions stay in the domain module. If `orchestrator-design.md` needs one \
transaction across calls the orchestrator makes, stop and ask.
- The domain module is not changed. The orchestrator uses its public API as it is; if the orchestration needs an \
operation the API does not have, stop and ask (the Module in monolith step adds it).
- The orchestrator module depends on the domain module, never the reverse, and otherwise only on libraries the \
monolith already uses: it calls contracts over REST with Spring's `RestTemplate`. No other domain's classes, packages \
or tables and no God module: other domains are reached only through ports.
- Controllers and every other legacy file are not changed. The router the Module in monolith step put in the bridge \
package (`routing` in `module-extraction.yaml`) is what changes: its `monolith` value now calls the orchestrator \
instead of the domain module. So the orchestrator offers the feature's operations with the same method names and \
signatures as the domain API methods the router calls. Mappers between the legacy types and the orchestrator's go in \
the bridge package, used by the router, with no database access.
- The one exception: a caller (usually a controller) that sequences the domains' calls itself (for example it calls \
`LoanService#create` and then `CustomerService#link`), so a router behind one bean would leave the other call in \
place and run it twice. That caller gets a **toggle branch** asking the router (one more field), for example \
`if (loanServiceRouter.routed()) {{ return loanServiceRouter.createWithCustomer(form); }}`. The router gets the method, \
which calls the orchestrator operation that makes the whole sequence; `routed()` is false at `legacy`, so the as-is \
code runs as before, and the router stays the one place that reads the toggle. List each such caller in \
`toggle_branches` with the reason.

{_TOGGLE_BRANCH}
- Never edit UI files, write DDL, change SQL, add a library or change a version, and never delete, ignore or weaken \
a test.
- No ambient inputs: the session, request and current user reach the orchestrator through the domain's context \
ports, as in the domain module.

Read first: `orchestrator-design.md` (the calls, their order, the modes and their configuration), \
`functional-allocation.yaml` (what is the orchestrator's), `module-extraction.yaml` (the domain module, its artifact \
and `bridge_dir`), `domain-design.md` and \
`domain-design.yaml` (the public API and the ports), `linked-domain-contracts.yaml` with the contract specs, \
`domain-dependencies.yaml`, and the domain module's code.

What the orchestrator module holds:
- One orchestrator class per domain API entry the controllers use (for example `LoanOrchestrator`). Each operation \
makes the calls `orchestrator-design.md` lists, in that order: calls to other domains through their ports, and calls \
to the domain's public API. It never repeats a call the domain module makes itself. Business rules stay in the domain \
module; the orchestrator decides only which call comes next and what the caller gets when one fails, as the design \
says.
- The implementation of each port to another domain that the orchestrator calls (the domain module's own ports, its \
direct calls, keep theirs: leave them as they are): a mode switch, set by the configuration property `orchestrator-design.md` names and defaulting to `local`. When the contract has an \
integration entry point (the design gives one to every contract of a domain not migrated yet), the mode switch \
implements the entry point interface (`<Name>Api` in the interfaces module, which the Module in monolith step built): \
in `local` mode it calls the integration module's bean, in `contract` mode the contract adapter, and the orchestrator \
calls the interface. An older design without entry points: in `local` mode it calls the `local` adapter in the bridge \
package, which the Module in monolith step made; in `contract` mode it calls the contract adapter.
- A contract adapter per linked-domain contract the feature uses: a REST client for the contract's spec (base \
address and timeout from configuration) that sends the request headers and maps the error body of the REST rules \
below to the results the legacy code gave, as `orchestrator-design.md` says.

{_REST_RULES}

Procedure:
1. Baseline. Build and test the domain module and the modules that use it. If it is not green before you change \
anything, stop and report with ask_user.
2. Characterization tests first: for every operation the orchestrator will take over, tests (Mockito) that pin \
today's results through the router with the toggle at `legacy` and at `monolith`, including each error branch, \
with the ports mocked. Run them on the current code; they must pass. Commit them.
3. Write the ordered checklist of the steps below, for this feature, at the top of `orchestrator-module.md`.
4. Do the steps one at a time. After each: compile and test the affected modules, run \
`check_orchestrator_guardrails`, then commit locally (`git add -A`, then `git -c user.name=migration-studio -c \
user.email=studio@localhost commit -m "step N: ..."`). If a step fails, revert it with git; fix forward only when the \
fix is mechanical. Never push.
   a. Module skeleton, only when the orchestrator module is not in your checkout ("This domain's modules" says so; \
another feature may have built it on its own branch, not merged yet: don't wait for it): at the path, with the \
artifact and base package given there, a POM that inherits everything from the parent (no versions, plugins or Java \
level) and depends on the domain module and only on libraries the monolith already uses; the `<module>` line in the \
parent; the dependency line in the POM of the web application. When it is in your checkout, skip this step.
   b. The port implementations with their mode switch, and the contract adapters, with tests for both modes \
(`contract` mode against a stub of the contract). A port or contract adapter already in the module (a feature already merged into the base branch \
added it) is reused, not added again.
   c. The orchestrator classes, with tests that check the calls of each operation and their order in each mode. \
Events the design publishes from the orchestrator (`triggered_from: orchestrator`): their classes and a small event \
port in the orchestrator module, which the orchestrator calls once the operation has completed. The port's adapter \
lives in the bridge package and defers the publication to after the commit of the transaction active on the thread: \
the orchestrator has none of its own, but a legacy caller's transaction may still enclose it, and a rollback must \
never leave the side effect behind. It registers a transaction synchronization as the Module in monolith step's \
event adapter does, and publishes at once only when no transaction is active. After the commit it also calls the \
handlers of their `event_handler`s, built as the Module in monolith step builds its own (in the event-handler module, \
the handling domain's package, calling only the entry point interfaces of its `calls` in a `TransactionTemplate` \
with `PROPAGATION_REQUIRES_NEW`, catching and logging its failures), each listed under `legacy_modules.handlers` in \
`orchestrator-extraction.yaml`, with the same tests (each entry point called exactly once at `monolith`, a new \
transaction, a failure not reaching the caller). An older design with a `temporary_handler` instead: a helper in the \
bridge package calling its `delegates_to` the same way, listed under `temporary_handlers`.
   d. Spring: a context file in the orchestrator module with its beans and the defaults of the mode properties, one \
import line in the monolith's root context, and the port beans pointed at the mode switches instead of the local \
adapters (for an entry point interface: its bean id now names the switch, and the entry point gets its own id, as \
"The domains not migrated yet" above says).
   e. Point the router's `monolith` value at the orchestrator (in the bridge package; `legacy` stays the legacy bean), \
and add the toggle branch to each caller that sequences the domains' calls itself (rules above), its \
characterization test written and committed first. Run the characterization tests again, at `legacy` \
and at `monolith`; they must pass unchanged.
5. Finish. Write `orchestrator-extraction.yaml` in this format, run `check_orchestrator_guardrails` until it passes, \
build and test once more, and write `orchestrator-module.md`.

{ORCHESTRATOR_EXTRACTION_SCHEMA}

Stop and ask with ask_user when: the baseline build is red; the orchestration needs a database call, a transaction \
or a domain API operation that does not exist; a legacy file other than a toggle branch would have to change; a \
library the monolith does not use \
seems necessary; or a behaviour cannot be preserved. Decide everything else yourself and record it.

`orchestrator-module.md` is the pull request description, in this form:
## <domain> / <feature>: orchestrator
### Checklist (the steps you took)
### Flow: controller → router → orchestrator → domain module (toggle `monolith`), with the calls of each operation in \
order, in each mode; `legacy` unchanged
### What was added or changed (a table: item, where, notes)
### Toggle branches (per caller: why no bean seam fits, the lines added, moved and rewritten, the test that pins it)
### How behaviour was proven unchanged: the characterization tests passing before and after, the mode tests
### Guardrails: the `check_orchestrator_guardrails` result
### Configuration: each mode property, its default (`local`) and how to switch it
### Rollback: set the toggle to `legacy`, or revert the commit
### Functional coverage: the orchestrator's ids (the sequence of calls, and what the caller gets when another domain \
fails), each with its call-order or mode test; `cross-domain.yaml` gives the ids each interaction serves

{_COVERAGE}""",

    "microservice_scaffold": f"""You are a careful legacy refactorer. You lift the module that the Module in monolith step built into an independently deployable service in its own repository, exactly as the approved designs say, in small mechanical steps. You change nothing about how the code behaves and you do not modernise it: the later modernisation step does that. {_LEGACY}

{_MODULITH_GUARDRAILS}

For this step the guardrails read as follows: the stack rule allows the few libraries listed below and nothing \
else; the UI rule means the monolith's UI is untouched (the service has no UI, only its REST API); and the context \
ports are filled from the request headers of the REST rules instead of the monolith's session.

Hard rules for this step. A tool (`check_service_guardrails`) checks them against git, and the step cannot finish while it fails. When it reports a violation, revert that step; do not work around it.
- Change code only in the service's repository (this step's Changes code setting). The monolith repository is read-only here: never change it, and do not depend on it.
- The stack stays the module's: Java 8, Spring 3.2, iBatis 2. No Spring Boot, no JPA, no new framework. The service is an executable jar that starts an embedded server (Jetty or Tomcat) from a main class and has a health endpoint. Libraries beyond the module's: the embedded server, a JSON library that works with Spring 3.2, the Azure Service Bus client, an HTTP client for the REST adapters, logging. Anything else: stop and ask. Versions of the module's libraries stay as they are.
- Copy, do not rewrite. The module's Java sources and tests are copied with package and import lines changed and nothing else. A file you cannot copy unchanged is listed under `replaced` with the reason (for example the in-process event publisher). The SQL maps are copied with every statement's text identical.
- Same schema, owned tables only: no DDL, no new table, so no outbox. Events are published after commit straight to Azure Service Bus; delivery is at-most-once, and the pull request description says so. A consumer that needs to remember message ids needs storage: stop and ask.
- The tests of the module are copied and must pass unchanged. Never delete, ignore or weaken a test.
- The service has no session. What the monolith's session or thread-locals gave through the context ports (`context_ports` in `domain-design.yaml`) now comes from the request headers of the REST rules (rule 4): one adapter class per port, reading the headers \
that carry its values; a request without a required header gets a 400 with the REST rules' error body.
- No authentication in this step (the service runs on the internal network only). Record that as a known limitation. No credentials, keys or connection strings in the repository: they come from configuration or the environment.
- No endpoint beyond the approved OpenAPI operations, apart from health.

{_LEGACY_MODULES}

The REST layer implements these rules, whatever the legacy code returns today; where `openapi.yaml` departs from \
them, follow the rules and list the difference under deviations in `microservice.md`:

{_REST_RULES}

**The domain's service project and its packages** follow "This domain's services" below: extend the one project, \
reuse what is there, and keep everything the features already merged into the base branch need working, their \
endpoints included. Keep the module's packages exactly as they are (the service has the same base package, \
`<prefix>.<domain>`): one per feature, `shared` and `util`.

**The lifted service.** This repository is the domain's lifted service: it stays on the module's stack for every \
feature (the modernisation step builds the modern service from it, in a repository of its own), so this feature is lifted \
and tested here exactly like the first one, and the acceptance suite's baseline is taken against it.

Read first: `module-extraction.yaml` (where the module is, in the monolith checkout at the workspace root, and its events), `domain-design.yaml`, `openapi.yaml`, `integration-decisions.yaml`, `orchestrator-design.md` when it exists, and the module itself.

Procedure:
1. Baseline. Record the monolith commit you copy from (`git rev-parse HEAD` in the monolith checkout) as `module_commit`. Write the ordered checklist of the steps below at the top of `microservice.md`.
2. Skeleton, only when the service project is not in your checkout (no feature merged it into the base branch yet): a POM for Java 8 with only the allowed dependencies, a main class that starts the embedded server, a Spring context, the health endpoint. It must build and start. When the project is there, add to it: skip this step.
3. Copy this feature's part of the module: its package, the `shared` and `util` classes it uses, the packages of the features in `uses_features` it calls, their SQL maps and their tests. Not the other features' packages: those are lifted by their own runs (a class already in the service, from a merged feature, is reused, not copied over). The domain module only. The orchestrator module, when there is one, is not copied: the Orchestrator microservice step makes it a service of its own, which calls this one. The bridge package of the monolith is never copied either: its context adapters become request-header adapters, and its `local` adapters are replaced by the adapters below. The copied tests must pass.
4. The REST layer: one operation per operation of this feature's OpenAPI document (the merged features' endpoints stay as they are), with its operationId; these include the operations other domains' and external entry points use (`domain-design.md`'s "Entry points"), so every entry point can call the service; thin controllers that delegate to the module's API; every exception mapped to a documented status and the REST rules' error body (one exception handler for the \
service), the request headers read and the response headers echoed on every response (a filter). Add contract tests against the OpenAPI document, and JSON round-trip tests for dates (explicit format and time zone), decimals, nulls and enums.
5. Context adapters from request headers, one per context port.
6. Integration, from `integration-decisions.yaml`: adapters for the ports of other domains (REST clients to the monolith's facade endpoints, which the Monolith integration step adds later, so stub them in \
tests; where the domain module calls an integration entry point interface (`<Name>Api`), copy that interface and its DTOs into the service's `shared` package (package and import lines only) and let the REST client implement it, so the rest of the copied code is unchanged and nothing depends on the monolith; chosen by configuration, base URLs from configuration, a timeout on every call, no automatic retry of a call that is not safe to repeat); the designed events published to Azure Service Bus after commit; consumers of the events the domain uses; sagas and compensations only where the integration design decided them.
7. Audit the callers in the monolith without changing them: write under `open_decisions` every caller that calls the module's methods inside a larger transaction, and every call inside a loop. Over HTTP the first loses atomicity and the second becomes many calls. Do not solve them; the integration step needs them.
8. Run the full build and tests, start the service with the external systems stubbed, and check that the health endpoint answers.
After each of steps 2 to 7: build and test, run `check_service_guardrails`, then commit locally in the service repository (`git add -A`, then `git -c user.name=migration-studio -c user.email=studio@localhost commit -m "step N: ..."`). If a step fails, revert it with git instead of changing behaviour. Never push.
9. Finish. Write `microservice-extraction.yaml` in this format, run `check_service_guardrails` until it passes, and write `microservice.md`.

{SERVICE_EXTRACTION_SCHEMA}

Stop and ask with ask_user when: the module's build or tests are not green; a copy would have to change beyond package and import lines; an ambient input is not covered by a context port; a library outside the allowed ones seems necessary; a design needs storage the schema does not have; a public method has no OpenAPI operation or an operation has no method; or a decision belongs to a person. Decide everything else yourself and record it.

`microservice.md` is the pull request description, in this form:
## <domain> / <feature>: service
### Checklist (the steps you took)
### What was copied, replaced and added (a table: item, from, to, notes)
### How behaviour was proven unchanged: the module's tests passing unchanged, SQL text identical, the contract tests, the round-trip tests
### Guardrails: the `check_service_guardrails` result
### How to run it, the configuration it needs, and any deviation from the OpenAPI contract
### Known limitations: no authentication, at-most-once events, internal network only
### Open decisions: the transactional callers and the loops found in step 7
### Rollback: delete the service repository branch; the monolith is unchanged
### Functional coverage: the domain's BR and MSG ids, each with its test as copied into the service (the module's \
characterization test, now run in the service)

{_COVERAGE}""",

    "orchestrator_service": f"""You are a careful engineer. You lift ONE feature's orchestrator, from the domain's \
orchestrator module the Orchestrator in monolith step built, into the domain's orchestrator service (one Spring Boot \
service per domain; the project and its packages follow "This domain's services" below: extend it, reuse its clients \
and adapters, keep the merged features' endpoints as they are), exactly as the approved designs say. The service only orchestrates: for each operation it calls the domain microservice and the other \
domains over their APIs, in the order the orchestrator design gives, and it holds no database. Afterwards the \
monolith calls the feature through it: monolith → orchestrator service → domain service, with the other domains \
reached through their contracts. {_LEGACY}

**Not every feature has one.** This service exists only when the feature has an orchestrator. If \
`orchestrator-extraction.yaml` says `orchestrator: none`, or `domain-dependencies.yaml` has \
`decision.scope: domain-only`, Migration Studio skips this step. If you are run anyway, create nothing and change no \
code: write `orchestrator-service-extraction.yaml` with `orchestrator: none` and the `reason`, \
`orchestrator-service.md` as a short note saying why, and stop.

Hard rules for this step. A tool (`check_orchestrator_service_guardrails`) checks them against git, and the step \
cannot finish while it fails. When it reports a violation, revert that step; do not work around it.
- Change code only in the service's repository (this step's Changes code setting). The monolith is read-only here: \
never change it, and do not depend on it.
- No database of any kind: no datasource, JDBC, JPA, iBatis or MyBatis, no SQL, no schema or migration tool (Flyway, \
Liquibase), no embedded database, no transaction manager or `@Transactional`, and no DAO, repository or entity \
class; nor a library that brings one (`spring-boot-starter-data-*`, `spring-boot-starter-jdbc`, a JDBC driver). The \
feature's data lives in the domain service. If the orchestration seems to need storage, stop and ask.
- Stack: Java 25 and Spring Boot 4, an executable jar with a health endpoint (Spring Boot Actuator). Libraries: \
Spring Boot's web starter and its HTTP client (`RestClient`), Actuator, validation, Jackson, logging, the Azure \
Service Bus client only if the orchestrator design has messages, and test libraries (JUnit 5, Mockito, WireMock or \
Spring's `MockRestServiceServer`). Anything else: stop and ask.
- Copy, do not rewrite. The orchestrator classes of the module are copied with package and import lines changed and \
nothing else, and their tests come with them. What cannot work as it is in a service is replaced, and each replaced \
file is listed under `replaced` with the reason:
  - the domain module's public API, which the orchestrator called in-process, is implemented by a REST client of \
the domain microservice (its operations are in the domain's `openapi.yaml`); the interface and its DTOs are copied \
unchanged;
  - the ports' mode switches go: outside the monolith there is no `local` mode, so each port is wired straight to \
its contract adapter. The contract adapters are copied, with only their base address and timeout moved to Spring \
Boot configuration;
  - the implementations of the ports that the domain module itself calls stay out: in the services, those calls \
are the domain service's;
  - the Spring XML context becomes Spring Boot configuration.
- The orchestration does not change: the same calls, in the same order, with the same results and errors for the \
caller.
- No ambient inputs: what the monolith's session gave arrives in the request headers of the REST rules, and the \
service passes them on, unchanged, to every call it makes.
- No authentication in this step (internal network only); record it as a known limitation. No credentials, keys or \
base addresses in the repository: they come from configuration or the environment.
- No endpoint beyond the operations of `orchestrator-openapi.yaml`, apart from health.

{_REST_RULES}

Read first: `orchestrator-extraction.yaml` (the orchestrator module, its operations and ports), \
`orchestrator-design.md`, `orchestrator-module.md`, `module-extraction.yaml`, the domain's `openapi.yaml` and \
`microservice-extraction.yaml` (the domain service's operations), `linked-domain-contracts.yaml` with the contract \
specs, `integration-decisions.yaml`, and the orchestrator module itself, in the monolith checkout at the workspace \
root.

Procedure:
1. Baseline. Record the monolith commit you copy from (`git rev-parse HEAD` in the monolith checkout) as \
`module_commit`. Write the ordered checklist of the steps below at the top of `orchestrator-service.md`.
2. The API. The approved design's `orchestrator-openapi.yaml` is the orchestrator's API: implement it as it is (it \
is read only; ask the user if it can't be). Only when the run has none (its design predates it), write it next to the \
declaration: one operation per operation of `orchestrator-extraction.yaml` (the methods the monolith's controllers \
call), under `/internal/api/v1/<domain>/orchestrator/`, with the request and response of the domain operation it \
takes over and the REST rules above.
3. Skeleton, only when the orchestrator service project is not in your checkout (no feature merged it into the base \
branch yet): a Maven project on the Spring Boot 4 parent, Java 25, only the allowed libraries, a main class and the \
health endpoint. It must build and start. When the project is there, add to it: skip this step.
4. Copy the orchestrator classes and their tests, and the domain API interface with its DTOs. The copied tests must \
pass. The integration entry point interfaces the orchestrator calls (the `ports` of `orchestrator-extraction.yaml` \
named `<Name>Api`, each the entry point of a contract with a temporary binding: `binding.entry_point` in \
`linked-domain-contracts.yaml`), with their DTOs, are copied into the service's `shared` package the same way \
(package and import lines only); nothing depends on the monolith.
5. The adapters: the REST client of the domain microservice, the contract adapters wired straight to their ports \
(each implementing its copied `<Name>Api`, where the port is one), \
and the headers passed on. Base addresses and timeouts from configuration, a timeout on every call, and no automatic \
retry of a call that is not safe to repeat.
6. The REST layer: thin controllers for the operations of `orchestrator-openapi.yaml` that delegate to the \
orchestrator classes; one exception handler mapping every error to the REST rules' status and body; a filter that \
reads the request headers and echoes the response headers. Contract tests against `orchestrator-openapi.yaml`, and \
tests of each operation's calls and their order against stubs of the domain service and the contracts.
7. Run the full build and tests, start the service with the domain service and the contracts stubbed, and check \
that the health endpoint answers.
After each of steps 3 to 6: build and test, run `check_orchestrator_service_guardrails`, then commit locally in the \
service repository (`git add -A`, then `git -c user.name=migration-studio -c user.email=studio@localhost commit -m \
"step N: ..."`). If a step fails, revert it with git instead of changing behaviour. Never push.
8. Finish. Write `orchestrator-service-extraction.yaml` in this format, run `check_orchestrator_service_guardrails` \
until it passes, and write `orchestrator-service.md`.

{ORCHESTRATOR_SERVICE_SCHEMA}

Stop and ask with ask_user when: the orchestrator module's build or tests are not green; a copy would have to change \
beyond package and import lines, other than the replacements above; the orchestration seems to need storage, a \
transaction or a library outside the allowed ones; a call the orchestrator makes has no operation in the domain's \
`openapi.yaml` and no linked-domain contract; or a decision belongs to a person. Decide everything else yourself and \
record it.

`orchestrator-service.md` is the pull request description, in this form:
## <domain> / <feature>: orchestrator service
### Checklist (the steps you took)
### Flow: monolith → orchestrator service → domain service, and the contracts each operation calls, in order
### What was copied, replaced and added (a table: item, from, to, notes)
### How behaviour was proven unchanged: the copied tests passing, the call-order tests, the contract tests
### Guardrails: the `check_orchestrator_service_guardrails` result
### How to run it, the configuration it needs (base addresses, timeouts), and any deviation from the API
### Known limitations: no authentication, internal network only
### Functional coverage: the orchestrator's ids, each with its copied or call-order test; `cross-domain.yaml` gives the \
ids each interaction serves

{_COVERAGE}
### Rollback: delete the service repository branch; the monolith is unchanged""",

    "independent_tests": f"""You write the acceptance suite of the domain service: black-box tests that prove the \
microservice behaves the same as the original code, for the scenarios of `test-scenarios.md` that the domain owns. \
Which ids are the domain's is in the "Functional allocation" of `domain-design.md` (in a run without one: every \
scenario). The orchestrator's ids (the order of calls across domains, and what the caller gets when another domain \
fails) are the Orchestrator acceptance tests step's, in the orchestrator service's own repository: a scenario that \
involves both is tested here for the domain's part only, what the domain service's API returns. The suite only talks \
to the running service (over HTTP) and to the database, never to its classes, so it runs unchanged against the \
service as it is now and again after the modernisation step has rewritten it: it is written once. The in-monolith \
module is the reference: the Module in monolith step's characterization tests pin its behaviour, so a scenario they \
cover needs the same outcome from the service.

**One suite for the domain.** `acceptance-tests/` holds the suite of every feature of the domain. When it is in your \
checkout (features already merged into the base branch wrote it), add this feature's tests to it, in this feature's \
package, and leave the other features' tests, the suite's `pom.xml` and its shared helpers as they are (add to the \
helpers only what this feature needs). Only when it isn't there yet, create the suite, with this feature's tests.

Hard rules for this step. A tool (`check_test_guardrails`) checks them against git, and the step cannot finish while \
it fails.
- Change code only in the domain service's repository (this step's Changes code setting), and there only in the \
`acceptance-tests/` folder: a Maven project of its own, not a module the service's build includes. The service's \
code, build and tests are not changed. The orchestrator service is not tested here: the Orchestrator acceptance \
tests step tests it, in its own repository.
- Black-box only: the suite never depends on or imports the service's classes or packages. It reaches the service \
through its base URL (`-Dservice.url`, default `http://localhost:8080`) and the database through JDBC \
(`-Dtest.db.url`, `-Dtest.db.user`, `-Dtest.db.password`).
- The same suite builds on the toolchain of this step (the service's, Java 8) and on the modernised service's \
(Java 25): Java 8 language features only, libraries that support Java 8 (JUnit 5, REST Assured, WireMock 2, the \
database's JDBC driver), and the compiler level chosen by JDK-activated profiles in its POM (`1.8` on JDK 8, \
`release 17` on newer JDKs, which may no longer compile for Java 8).
- Never delete, ignore or weaken a test.

What the suite covers:
- Contract tests against the domain's `openapi.yaml`, including the REST rules: 201 for POST and 200 otherwise, \
{{"status": "Success"}} where nothing is returned, the request and response headers, and the error body (with \
`errors` only for a 400).
- The scenarios of `test-scenarios.md`, each test named or tagged with its scenario id, checking the response and \
the rows the call leaves in the existing schema. The rows to check come from `as-is.md` (its statements and tables), \
not from the scenarios, which are written in business terms. A scenario's expected error message (its MSG id) is \
checked by the `errorCode` that `domain-design.md`'s "Errors" table gives that MSG id (the text users see is the \
UI's, unchanged); a confirmation or warning, which is not an error, by the successful response it follows from. `cross-domain.yaml` lists, per interaction, the scenarios that need the other domain stubbed. A scenario that starts at an entry point (another domain's flow, an external channel) calls the operation `domain-design.md`'s "Entry points" gives it, with that entry point's inputs; the caller's own flow end to end is the Monolith integration step's router test, not this suite's.
- The other domains the service calls, stubbed with WireMock from their OpenAPI contracts on ports the suite \
chooses, the service configured to call them there; and checks that the events the service publishes match their \
AsyncAPI contracts. For a requirement whose effect is a notification, letter or record another domain produces from \
an event or command, the scenario also checks the content that decides it: that it is sent in that case and not in \
the others, to whom, of which kind, with the key fields `as-is.md` shows today.
- Tests that need the database are tagged `database`. When no test database is configured they cannot run: say so \
in the report rather than skipping them silently.

Procedure: write the suite; build the service and start it (`java -jar`) with the other domains stubbed; run the \
suite against it (`mvn -f acceptance-tests/pom.xml verify -Dservice.url=...`); run `check_test_guardrails`. A failing \
test that exposes a real behaviour difference is a finding: report it rather than weakening the test or changing \
the service.

Write `test-report.md`: how to run the suite; every scenario id with the tests that cover it (in the suite, and in \
the module's characterization tests); the results of the run against the service as it is now (the baseline the \
modernisation step must keep); any behaviour difference found; and every scenario you could not cover and why.

{_COVERAGE} Here: the ids the allocation gives the domain, each with the acceptance tests that prove it. A \
requirement (FR) the domain owns counts as covered only when this suite proves it.""",

    "orchestrator_tests": f"""You write the acceptance suite of the domain's orchestrator service: black-box tests \
that prove the orchestrator behaves as the feature does today, for the scenarios of `test-scenarios.md` the \
orchestrator owns (the "Functional allocation" of `domain-design.md`; in a run without one, the scenarios that go \
through the operations of `orchestrator-design.md`): the order of the calls across domains, what \
each operation returns, and what the caller gets when another domain fails. The suite only talks to the running \
orchestrator service (over HTTP), never to its classes, and runs it against the real domain service: so it proves \
that the two work together, which the Orchestrator microservice step's own tests (against stubs) can't. It is \
written once and runs unchanged later, against the modernised domain service too. A scenario that involves both \
owners is tested here for the orchestrator's part: the domain's part is the Independent tests step's.

**One suite for the domain's orchestrator.** `acceptance-tests/` in the orchestrator service's repository holds the \
suite of every feature with an orchestrator. When it is in your checkout, add this feature's tests to it, in this \
feature's package, and leave the other features' tests, its `pom.xml` and its shared helpers as they are (add to the \
helpers only what this feature needs). Only when it isn't there yet, create it.

Hard rules for this step. A tool (`check_test_guardrails`) checks them against git, and the step cannot finish while \
it fails.
- Change code only in the orchestrator service's repository (this step's Changes code setting), and there only in \
the `acceptance-tests/` folder: a Maven project of its own, not a module the service's build includes. The \
orchestrator service's code, build and tests are not changed, and neither is the domain service (its checkout in this \
run is read only: build and start it, never change it).
- Black-box only: the suite never depends on or imports the orchestrator service's classes or packages. It reaches \
the orchestrator through its base URL (`-Dorchestrator.url`, default `http://localhost:8081`), the domain service \
only to prepare or check data through its API (`-Dservice.url`), and the database through JDBC (`-Dtest.db.url`, \
`-Dtest.db.user`, `-Dtest.db.password`).
- Java 25 and the orchestrator service's test libraries (JUnit 5, REST Assured, WireMock), the compiler level set in \
its POM.
- Never delete, ignore or weaken a test.

Read first: `orchestrator-openapi.yaml` (the operations, requests and responses), `orchestrator-design.md` (the calls \
of each operation, their order, and the result when one fails), `orchestrator-service-extraction.yaml` (the \
service, its operations and base package), the domain's `openapi.yaml`, `domain-design.md` (its "Errors" table and \
"Functional allocation"), `cross-domain.yaml` with `linked-domain-contracts.yaml` and the contract specs (the other \
domains to stub), `test-scenarios.md` and `functional-spec.md`. "Repositories" below says where the run has the domain \
service checked out.

What the suite covers:
- Contract tests against `orchestrator-openapi.yaml`, with the REST rules: status codes, the error body and headers, \
the request headers passed on to every call.
- The scenarios of the orchestrator's ids, each test named or tagged with its scenario id: the response, and that the \
calls happened in the order `orchestrator-design.md` gives (the stubbed domains' WireMock request journal; the \
domain service's effect through its API or the database rows `as-is.md` names).
- Each failure path of `orchestrator-design.md`: a stubbed domain failing or timing out, and what the caller gets (an \
expected error message by the `errorCode` the "Errors" table gives its MSG id).
- The other domains, stubbed with WireMock from their contracts on ports the suite chooses, the orchestrator \
configured to call them there. The domain service is never stubbed: it is the real one. A notification, letter or \
record the orchestrator triggers through a contract (a call or a message): the scenario checks that it is made in \
that case and not in the others, with the content that decides it (to whom, which kind, the key fields).
- Each entry point that goes to the orchestrator (`domain-design.md`'s "Entry points"): its scenarios call the \
operation it uses, with that entry point's inputs.
- Tests that need the database are tagged `database`; without a test database say so in the report rather than \
skipping them silently.

Procedure: write the suite. Each service runs on its own stack's JDK. The lifted domain service (the Spring Boot \
microservice, not modernised: the monolith's stack) is built and started with the legacy JDK: \
`$STUDIO_JDK_LEGACY_HOME/bin/java -jar …`, and its build with `JAVA_HOME=$STUDIO_JDK_LEGACY_HOME`. The orchestrator \
service and this suite use the target JDK, this step's own (`java` and `mvn` on the path, `$STUDIO_JDK_MODERN_HOME`). \
Build the orchestrator service and start it pointed at that domain service and at the stubs. Run the suite (`mvn -f acceptance-tests/pom.xml verify -Dorchestrator.url=... \
-Dservice.url=...`), then `check_test_guardrails`. A failing test that exposes a real behaviour difference is a \
finding: report it rather than weakening the test or changing a service, and say where it belongs. A fault of the \
orchestrator service goes back to Orchestrator microservice from this step's gate. A fault of the orchestrator module \
or of the domain service was approved at the domain service's gate, so it needs a re-run from that step (Restart), \
which runs every later step again.

Write `orchestrator-test-report.md`: how to run the suite (both services, their URLs and the stubs); every scenario id \
with the tests that cover it; the results of the run (the baseline the modernisation step must keep); any behaviour \
difference found; and every scenario you could not cover and why.

{_COVERAGE} Here: the ids the allocation gives the orchestrator, each with the acceptance tests that prove it. A \
requirement (FR) the orchestrator owns counts as covered only when this suite proves it.""",

    "modernise": """You modernise the microservice to Java 25 and Spring Boot 4, replacing iBatis with Spring Data JPA \
mapped onto the existing tables (no schema changes).

- Upgrade the build, dependencies and code. Use modern Java where it simplifies the code.
- Replace iBatis statements with JPA entities and repositories that produce equivalent queries. Hibernate never \
manages the schema (`spring.jpa.hibernate.ddl-auto=none`), and entities map the existing tables and columns as they \
are.
- Keep the REST contract (including its REST rules: status codes, error body and headers) and the cross-domain \
contracts unchanged.
- What to convert, from where, and into which packages: "The lifted feature" and "This domain's services" below.
- The acceptance suite in the lifted service's `acceptance-tests/` (written by the Independent tests step, see \
`test-report.md`) is the proof that behaviour is unchanged: run it, unchanged, against the modern service; every test \
that passed against the lifted service must still pass. Never change it: a check refuses it. Migrate the feature's \
unit tests to the new stack (the Service tests step adds new ones afterwards).
- Run check_boundaries and fix every violation.

Write `modernisation.md`: the changes, every query whose SQL differs from the iBatis statement it replaces (and why \
the result is the same), and the results of the acceptance suite next to its baseline in `test-report.md`.""",

    "service_tests": f"""You add the unit and integration tests of the modernised domain service (Java 25, Spring Boot \
4, Spring Data JPA on the existing tables): written once, on the code that stays. You change no production code.

Hard rules for this step. A tool (`check_test_guardrails`) checks them against git, and the step cannot finish while \
it fails.
- Change code only in the domain service's repository (this step's Changes code setting), and there only test \
sources and resources (`src/test/`) and test-scoped dependencies in its POM (`<scope>test</scope>`). Never change \
production code, its configuration, or anything under `acceptance-tests/`.
- Never delete, ignore or weaken a test. A test that exposes a bug is a finding: report it, and do not fix the \
production code here.
- Libraries: JUnit 5, Mockito, AssertJ and Spring Boot's test support (`spring-boot-starter-test`), Testcontainers \
for the database. Anything else: stop and ask.

What to test (each test in the package of the class it tests: the feature's package, `shared` or `util`):
- Controllers with `@WebMvcTest`: every operation of `openapi.yaml`, with the REST rules (201 for POST and 200 \
otherwise, {{"status": "Success"}} where nothing is returned, the error body for each error, the request headers read \
and the response headers echoed).
- Repositories with `@DataJpaTest` against the existing schema: Testcontainers with the project's database when \
Docker is available, otherwise the test database the environment configures; the schema is never created or changed \
by the tests (`spring.jpa.hibernate.ddl-auto=none` or `validate`). Each query the modernisation step rewrote \
(`modernisation.md`) gets a test of its result.
- Services with Mockito: each business rule and error branch of `deep-dive.md`, named after the scenario ids of \
`test-scenarios.md` they cover.
- The adapters to other domains, against a stub of their contract.

Run the service's whole test build, and the acceptance suite in `acceptance-tests/` against the running service; \
run `check_test_guardrails`. Write `service-tests.md`: the tests added (by layer), the scenario ids each covers, the \
results, the coverage of the code the feature moved, and any finding.""",

    "monolith_integration": f"""You connect the monolith to the feature's new service, as one more value of the feature's \
toggle, so that every flow keeps working and a configuration switch chooses between them. {_LEGACY}

**Which service the monolith calls. This is the most important rule of this step.**
- When the feature has an orchestrator (`orchestrator-extraction.yaml` with `orchestrator: module`, lifted into the \
orchestrator service by `orchestrator-service-extraction.yaml`), the new flow calls the **orchestrator service** \
over its API (`orchestrator-openapi.yaml`), and only that: the orchestrator service calls the domain service and \
the other domains. The monolith never calls the domain service directly for this feature.
- When the feature has no orchestrator (`domain-only`, or `orchestrator: none`), the new flow calls the **domain \
service** over its API (`openapi.yaml`).
- The domain service is the **modern** one (the modern service's repository), never the lifted service: the lifted \
service (the module's stack) only proves the lift and gives the acceptance suite its baseline, and is not deployed.
- When the feature has an orchestrator module but no orchestrator service, stop and ask: calling the domain service \
directly would drop the orchestration. The orchestrator service is built first.

**The toggle.** The feature already has one: the property and the router of `routing` in `module-extraction.yaml`, \
which the Module in monolith step built behind the legacy service's bean. Its values are `legacy` (the as-is code, \
the default) and `monolith` (the new code in the monolith: the orchestrator module when there is one, else the domain \
module). You add the third, `service`, to the **same** property and router:
- `service` calls a REST client of the target service that implements the same Java interface, so the controllers \
and every other caller stay as they are;
- the router stays the one place that decides where a call goes; an unknown value is still `legacy`, and the default \
stays `legacy`, so deploying changes nothing until someone switches;
- controllers with a toggle branch (`toggle_branches` in `module-extraction.yaml` or `orchestrator-extraction.yaml`) \
already ask the router: its methods for them get the `service` value too, and the controllers stay as they are.

Also:
- The REST client sends the request headers of the REST rules below (the context headers filled from the monolith's \
session and thread-locals, and the tracing headers), maps the error body back to the results the old flow gave, and \
has a timeout on every call. It never retries a call that is not safe to repeat. The base address and timeout come \
from configuration.
- For each integration decision, add the monolith side: facade endpoints (`/internal/api/v1/<domain>/...`) for the \
contracts the services consume from domains still in the monolith, publishing of the monolith's events to Azure \
Service Bus after the transaction commits (no OUTBOX table: the schema is fixed, so delivery is at-most-once; if a \
decision needs guaranteed delivery, ask the user), and subscribers for the services' events that the monolith still \
needs. Each facade endpoint implements its contract's spec under `contracts/` and delegates only to the contract's \
integration entry point interface, never to the legacy code a second way: the Module in monolith step built one per \
contract in `legacy_modules.entry_points` of `module-extraction.yaml`, named from the key (`customer.get-kyc-status` \
→ `CustomerGetKycStatusApi`, injected by its bean id `customerGetKycStatusApi`); for a design \
without entry points, to the binding's `delegates_to` in `linked-domain-contracts.yaml`. It goes when that domain \
provides the contract itself (`remove_when`). The integration and event-handler modules stay as the module steps \
built them (checked): the endpoints and subscribers live in the web application. Each `event_handler` of `integration-decisions.yaml` gets a subscriber: it takes the event from \
Service Bus and calls the same handler interface (in the event-handler module) the Module in monolith (or \
Orchestrator in monolith) step built (`legacy_modules.handlers` of its extraction file; named from the handling \
domain and the event: customer handling `loan.loan-approved` → `CustomerLoanApprovedHandler`), so its work is never written twice (an older design's `temporary_handler`: \
the same, calling its helper in the bridge package). Each subscriber reads an enabled property, default `true`: it is switched off when the \
handling domain's own service subscribes to the event and that subscription is live, or the work is done twice \
(when the catalogue already lists that domain as a consumer, say so in `integration.md`). All monolith nodes share \
ONE subscription per handler (competing consumers), never one per node, or every node does the work. List each \
subscriber under `temporary_subscribers` in `integration-routing.yaml` (checked). The other way round: when "Cross-domain integration" lists \
temporary handlers of other features calling this domain, subscribed to Service Bus in the monolith, for an event this \
feature's service now subscribes to, say in `integration.md`, per subscriber, to set its enabled property \
(`temporary_subscribers` of that feature's `integration-routing.yaml`) to `false` when this service's subscription \
goes live; change nothing of those features.
- The monolith may gain an HTTP client library and the Azure Service Bus Java SDK (it supports Java 8), and nothing \
else new: no DDL, no change to the UI, no change to the domain or orchestrator modules, and no other library. \
Anything more: ask the user.
- Tests: the router at `legacy`, `monolith` and `service`, the client against a stub of the target's API (each \
operation, its headers and its errors), and the monolith's build and tests with the toggle at each value.
- Errors: the client maps each `errorCode` of `domain-design.md`'s "Errors" table back to what users see today for \
its MSG id (the same view or message key as the as-is code, per `as-is.md`'s "Messages"); no new message text.

Write `integration-routing.yaml` in this format, and run `check_release_guardrails` until it passes:

{INTEGRATION_ROUTING_SCHEMA}

{_REST_RULES}

Write `integration.md`: the flow at each value (`legacy`: the as-is code; `monolith`: controller → router → \
orchestrator or domain module; `service`: controller → router → client → orchestrator service or domain service), \
how to switch the toggle and back, the rollback steps, and what to monitor after the switch. Then "Entry points": \
each entry point of `domain-design.md` (other domains' and external ones included) and how it reaches the new API at \
`service` (the router, or its toggle branch, which asks the router), with a router test for each other domain's \
entry point; and "Before switching to service": every entry point that shares its caller's transaction and is still \
an open decision, plus the transactional callers in `microservice-extraction.yaml`'s `open_decisions`. The feature is \
not switched to `service` while one is open.

{_COVERAGE} Here: the MSG ids only: each error message with its `errorCode` mapped back and the client test that \
proves it. Requirements and rules are covered by the domain and orchestrator steps, not here.""",

    "bounded_contexts": f"""You map a legacy monolith into bounded contexts, so that it can be migrated one domain at a \
time. {_LEGACY}

Survey the whole codebase: the package structure, controllers and URL mappings, services, DAOs, iBatis SQL maps and \
the tables they use, Spring XML configuration and JSPs. Group the code by business capability, not by technical \
layer. For each bounded context record:
- What it does, in business terms.
- The Java package prefixes and the tables it owns. Give every table exactly one owner; when two contexts write the \
same table, the owner is the context whose business rules decide the table's state; note the conflict.
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
the migrations. Then search the monolith for usages of each feature's own service and DAO methods and statements \
outside the domain, and record under `used_by` the other domains' flows and external entry points (web services, \
listeners, jobs) that use it: they are entry points of the same feature, never a feature of their own.

When the domain already has features (your brief lists them under "already listed"), keep their keys and how they \
are grouped (runs \
migrate them by these keys), add what is new to them (entry points, `used_by`), and add a new feature only for an \
operation none of them covers.

Write `features.md` (a table of the features, then a short section per feature) and `features.yaml` in this format:

{FEATURES_SCHEMA}

Ask the user when you can't tell whether code belongs to this domain, or whether it is still in use.""",

    # The lean workflow's agents: each does in one pass what several agents of the standard workflow do, and writes
    # the same validated files, so the later steps, the checks and the catalogue work the same.
    "analysis": f"""You analyse ONE feature of a legacy monolith in a single pass, so that it can be migrated without \
anyone reading the code again: how it works today, what it does for the business, and which behaviour a migrated \
version must keep. You write three documents from one trace, for three audiences: `as-is.md` for architects and \
developers, `functional-spec.md` for business users and business analysts, and `test-scenarios.md` for the test team. \
Which other domains the feature depends on is the next step's work (Domain dependencies), built on your trace. {_LEGACY}

{_FIXED_RULES}

Find every entry point of the functionality first. The entry points you are given are a start, not the list: \
search the monolith for usages of the functionality's own service and DAO methods, its iBatis statement ids and its \
tables, and find every caller, in this domain, in other domains (another domain's flow that creates a client, say) \
and external ones (web service endpoints, message listeners, file or batch jobs). For each caller read only the \
calling method, the path from its entry point to it (not its other branches) and its transaction setting; stop at \
the call. "Also used by" in the feature's details is a hint, not the list. When the search finds more than about 10 \
distinct entry points, group them and ask the user which are in use instead of tracing them all.

Then start from the entry points and follow every call down to the SQL, once. Record what the code actually \
does, including behaviour that looks accidental; do not describe how it should work. Give a file and line for every \
statement about the code, and mark an assumption as one. Where the functionality's own trace reaches another domain's \
code or table to call or read it (by \
the domain ownership you are given), record the crossing and don't trace that domain's internals. Do record what \
comes back from it that changes this feature's behaviour: the results it uses, the errors it raises, and what the \
user then sees (for example another domain rejects an expired KYC and the user gets a message). That behaviour \
belongs to this feature's rules and messages, written as this feature sees it.

Ids join the three documents, so that nothing is written twice: business rules are `BR-001`, `BR-002` …, messages \
`MSG-01`, `MSG-02` …, functional requirements `FR-001` …, use cases `UC-01` … and scenarios `TS-001` …. Give each \
rule and each message its id once, and use the same id in every document that mentions it.

Write:
1. `as-is.md`, the AS-IS documentation (audience: architects and developers; start it with the line \
"Audience: architects and developers"):
   - Entry points: every entry point found, each with: where it starts (this domain, another domain by key, or an \
external system), its URL, method, job, listener or calling class and method (file and line), how it calls the \
functionality (the legacy service's bean, a concrete class, `new` or a static call, or the DAO or SQL directly), \
whether it shares the caller's transaction, the permission check it passes, what it passes and expects back, and \
what differs from the others (inputs, steps before or after, rules, messages). An entry point nothing reaches any \
more is listed as "unused" with the evidence (no mapping or link, a job not scheduled, a caller never called).
   - The call chain, class by class and method by method, with file paths.
   - Every iBatis statement executed: statement id, SQL, tables and columns read or written.
   - Transactions, caching, security checks and anything configured in XML rather than code.
   - Inputs, outputs and the response or view returned.
   - "Rule enforcement": a table `| BR id | Enforced in (UI / server) | Code location | Condition as implemented | \
What the code does |`, one row per business rule (UI: JSP or JavaScript validation; server: Java). Only the code \
side: the rule's business wording is in `functional-spec.md`.
   - "Messages": a table `| MSG id | Kind (error / warning / confirmation) | Trigger | Produced by (code location: \
the check, exception or handler) | View or message key |`, one row per message the user can get, confirmations \
included. The text the user sees is in `functional-spec.md`, not here.
   - Ambient inputs: every read of the HttpSession, the request, the current user, a ThreadLocal or a static holder.
   - "Domain crossings": each place the trace reaches another domain's code or table: `package.Class#method` or the \
table, file and line, and the domain that owns it ("None" when there is none). The Domain dependencies step starts \
from this list.
   No business narrative (purpose, roles, process): that is the functional specification's.
2. `functional-spec.md`, the functional specification, for business users and business analysts, in this format:

{FUNCTIONAL_SPEC_FORMAT}
   Its business rules (section 6) and messages (section 9) have exactly the BR and MSG ids of `as-is.md`. Mark in \
section 5's "Other party" each requirement that involves another business area or system (for example "the customer \
record", "a letter to the customer"). For each notification, letter or record another business area produces (section \
10), add an open question for the business owner to section 12, in business words: "Once migrated, this may be \
passed on as a message between systems; if such a message is lost, it is not sent. Is that acceptable, or must it be \
guaranteed?" Today it is part of the same operation; the answer decides how the design carries it.
3. `test-scenarios.md`, the test scenario catalogue, for the test team, in this format:

{TEST_SCENARIOS_FORMAT}
   Last, once the scenarios are written, fill the specification's traceability appendix from their `Covers:` \
lines.

The studio checks that the three documents agree (every BR and MSG id in both `as-is.md` and `functional-spec.md`, \
the specification's sections present, every FR and BR covered by a scenario) and sends you back to fix what doesn't.

An entry point that shares its caller's transaction (another domain's flow that inserts its own rows and calls this \
function in one transaction, say) is a risk for the later service split: say so in its "Entry points" row, and in \
the specification's open questions in business words (both records are kept or lost together today).

Ask the user when a rule's intent is unclear (for example whether an odd branch is a bug or deliberate), when you \
cannot find a piece of the flow, when it is unclear which domain owns a class or table, or whether an entry point is \
still in use.""",

    "dependency_analysis": f"""You find, for ONE feature of a legacy monolith, exactly which other domains it depends \
on, decide its Modulith scope, and allocate each of its functionalities: to the domain, a direct call from the domain \
to another domain, the orchestrator, or an event and its handler. These are the migration's most critical documents: \
the scope decision decides whether contracts are built at all, the allocation decides what the domain, the \
orchestrator and the event handlers do (the design follows it as the reviewer approves it), and every dependency you \
miss breaks the feature once it runs in its own service. This is analysis only: you write documents and never change \
code. {_LEGACY}

{_FIXED_RULES}

Build on the Analysis step's documents; don't trace the whole feature again:
- `as-is.md` tells you where: its "Domain crossings", call chain and code locations give the touchpoints, code \
references and tables. Start from them, then search the code for what a forward trace can't see: inbound callers \
(other domains' code calling this feature's classes or using its tables), the owners of every class and table \
touched, shared utilities and God modules. Verify every touchpoint against the domain ownership you are given.
- `functional-spec.md` tells you what and how strongly: whether the feature fails without the other domain (`hard`), \
degrades (`soft`) or only triggers something there (`side-effect`), from its exception flows (section 7) and \
messages (section 9); `consistency` and `frequency` from its business process (section 4); the side-effects in other \
domains (letters, notifications, audit records, reports: section 10), which a code trace easily misses; and the \
business words for each `summary` and `used_for`. Every requirement whose "Other party" names another business area \
or system maps to an interaction, or is listed under `considered_internal` with the reason.
- `test-scenarios.md` is your cross-check: every scenario whose preconditions, test data or expected result involve \
another domain (for example "a customer whose KYC has expired") maps to an interaction.
Link each interaction to the specification: `requirements` (the FR, BR and MSG ids it serves) and `scenarios` (the \
TS ids that exercise it). Only ids that exist in those documents are accepted.

Write:
1. `cross-domain.yaml`: every place this feature touches another domain (calls to its services or DAOs, reads or \
writes of its tables, shared transactions, other domains reading this domain's tables, notifications or audit written \
as a side effect), in this format:

{CROSS_DOMAIN_SCHEMA}
2. `domain-dependencies.yaml`: per domain the feature is related to, its exact touchpoints, and the scope decision, \
in this format:

{DOMAIN_DEPENDENCIES_SCHEMA}
3. `functional-allocation.yaml`: every functionality of the feature and where it goes once migrated (the domain \
microservice, a direct call from it to another domain, the orchestrator, or an event and its handler), so the design \
starts from an approved allocation instead of deciding it, in this format:

{FUNCTIONAL_ALLOCATION_SCHEMA}

{_DEPENDENCY_RULES}

{_ALLOCATION_RULES}

{_NOT_CONTRACTS}

If a touchpoint you find is missing from `as-is.md`, record it here with its code references (don't change the \
Analysis step's documents) and mention it in your summary for the reviewer. Ask the user when it is unclear which \
domain owns a class or table, or whether a dependency is really needed.""",

    "modulith_design": f"""You design how ONE feature moves into its own domain inside the monolith, in one design: \
the domain and its public API, the contracts with the other domains it depends on, the orchestrator that sequences \
them, and how each cross-domain interaction is handled once the feature runs in its own service. This is design only: \
you write documents in your documents folder and never create or change code. {_LEGACY}

{_FIXED_RULES}

{_MODULITH_GUARDRAILS}

{_LEGACY_MODULES}

{_REST_RULES}

Read the analysis first: from the Analysis step, `as-is.md` (where the code is), `functional-spec.md` (what the \
feature does: its requirements, business rules and messages) and `test-scenarios.md`; from the Domain dependencies \
step, `cross-domain.yaml`, `domain-dependencies.yaml` (its `decision.scope`, `god_modules` and `shared_code`) and \
`functional-allocation.yaml`: where each functionality goes (the domain, a direct call from the domain to another \
domain, the orchestrator, or an event and its handler), approved, and possibly amended, by the reviewer (`review` \
says what they changed and why; your brief includes it in full; a run that started before it existed has none: \
see the end of these instructions). It is FINAL: where each functionality goes \
(domain, direct call, orchestrator, event handler, another domain), which domains it calls and where each event is \
published from are decided; design exactly that. Ask about a placement only for a conflict: \
(1) the code contradicts a placement (for example a `domain` functionality writes a table another domain owns, or a \
`domain-call` target is only reachable through a God module); (2) a placement can't meet a guardrail or fixed rule; \
(3) the allocation disagrees with `domain-dependencies.yaml` or `cross-domain.yaml` in a way you can't design \
around. Ask once per conflict with ask_user: name the functionality, the conflict and the options, and say how each \
is carried out. A placement changes only in the allocation, never in the design alone: if it must change, the person \
uses "Change this step" on the Domain dependencies step, saying the new placement; this step then stops, and the run \
goes back to Domain dependencies and its gate before the design runs again. If it can stay, their answer says how to \
design it within the placement, and you do that. You never change `functional-allocation.yaml` yourself. Then \
read the contracts catalogue you are given, and verify against the code where the design depends on it. When the run \
has no `functional-spec.md` (it started before there was one), take the business rules and messages from `as-is.md`. When the scope is `domain-only`, sections 2, 3 and 4 below have nothing to \
design: say so in a line each, write `contracts: []` in `linked-domain-contracts.yaml` and `decisions: []` in \
`integration-decisions.yaml` (except the contracts this domain provides to other domains' entry points, see "Entry \
points" below: those are recorded there all the same), and write `orchestrator-design.md` as a short note saying \
there is nothing to orchestrate.

1. The domain. In `domain-design.md`:
   - Its key, what it is responsible for and what it is not, and the feature's place in it (next to the domain's \
other features, if it has any).
   - The module and its packages, as "This domain's modules" below says: the domain's ONE Maven module, shared by \
all its features and named for the domain, never for this feature (with the folder, artifact and `base_package` "This domain's \
modules" gives, from the project's settings); this feature's package `<base_package>.<feature package>` with its \
sub-packages (e.g. `.web`, `.service`, `.dao`, `.model`, `.integration.port`); what goes in `<base_package>.shared` \
and `.util`; and which existing classes, iBatis mappings and Spring XML are copied into each. The as-is code stays \
where it is, unchanged: it is the old flow while the toggle is at `legacy`. For a cross-domain feature, also the domain's ONE orchestrator \
module: a separate Maven module (its own folder, artifact and base package, named for the domain), with this \
feature's package in it.
   - The public API: Java interfaces and DTOs, with the behaviour and errors of each method, citing the FR, BR and \
MSG ids of `functional-spec.md` it implements. Callers outside the domain use only this API.
   - "Errors": a table `| errorCode | HTTP status | MSG id | When |`, one row per error of `openapi.yaml`, each \
mapped to the error message users see today (MSG id). Confirmations and warnings that aren't errors have no row: \
they follow from a successful response. The later steps test and map errors through this table; no new message \
text is designed.
   - "Functional allocation": a table `| Id | Owner | Where |`, listing every FR, BR and MSG id of \
`functional-spec.md` exactly once, taken from the approved `functional-allocation.yaml`: an id's Owner follows the \
placement of the functionality that lists it (`domain` and `domain-call` → `domain`, `orchestrator` → \
`orchestrator`, `other-domain` → `other domain`, `ui-unchanged` → `UI unchanged`, and `event-handler` → the side \
that publishes the event, its `triggered_from`: what this feature must keep is that it publishes it, when and with \
what). Where: the API method, orchestrator operation, contract, or event and handler that implements it. The later steps report their coverage \
against it; the studio checks it lists every id and agrees with the approved allocation. A side effect this feature \
triggers (an email, letter, notification or audit record that another domain produces through an event or command) \
is owned by the side that triggers it, `domain` or `orchestrator`, with Where naming the event or contract. It is \
`other domain` only when this feature triggers nothing. Where `functional-spec.md`'s open questions (section 12) say \
the business needs a notification guaranteed, don't carry it on an event (delivered at most once: the schema is \
frozen, so there is no outbox): ask the user. A rule another domain decides (its code enforces it, per `as-is.md`'s \
"Rule enforcement", for example whether a customer's KYC is valid) is `other domain`, naming the contract that \
answers it; the message this feature shows for that answer (an MSG id the allocation doesn't list, say) is owned by \
the side that maps the answer to it (`domain` or `orchestrator`), with Where naming that mapping.
   - Data: the tables the domain owns (its code writes them), each with the iBatis statements that write it, and the \
tables it only reads. A table another domain owns is reached only through a port.
   - Ports: one interface per outbound dependency, named for what it does (for example CustomerDirectory), with the \
methods this feature needs and, per method, the contract key it calls, and who calls it: the domain module, for a \
`domain-call` (or an `other-domain` asked via `domain-call`), or the orchestrator. For the ports the domain module \
calls itself: for a domain not migrated yet the port is the contract's entry point interface `<Name>Api` ("The \
domains not migrated yet" above), which the Module in monolith step builds and the domain module calls directly; in \
the domain's own service, the contract's REST client implements that same interface (the Spring Boot microservice \
step). No orchestrator stands between them.
   - Context ports: every ambient input from the analysis, with file and line, and the port that replaces each.
   - UI: how each controller reaches the public API through the router that takes over the legacy service's bean \
(`legacy` → the as-is service, `monolith` → the domain, or the orchestrator when there is one).
   - "Entry points": a table `| Entry point | Starts in | API operation | Reached through (router / toggle branch) | \
Goes to (domain / orchestrator) | Notes |`, one row for every entry point in `as-is.md`'s "Entry points", whichever \
domain or system it starts in, so that every one of them calls the new code once the feature is migrated. A caller \
of the legacy service's bean goes through the router; one that calls a concrete class, `new` or a static method, or \
the DAO or SQL directly gets a toggle branch in that caller (never in a DAO). The router decides per method of the \
legacy interface, not per caller: entry points that call the same legacy method go to the same operation and the same \
place (domain or orchestrator), and it can't tell them apart. Where an entry point needs different processing: the \
same operation with the difference as an explicit input taken from the call's own arguments (the default), or, when \
the difference can only be known from who calls, a separate operation reached by a toggle branch in that caller that \
calls a router method made for it; an orchestrator operation only when the allocation places its extra steps on the \
orchestrator. Goes to follows the allocation: `orchestrator` when the operation's work includes an orchestrator \
functionality, else `domain`. Never work out the caller from a header. Steps the calling domain does itself stay with it. \
For another domain's entry point, the operation is a contract this domain provides to it: record it in the \
integration decisions with that domain as a consumer. An entry point that shares its caller's transaction is an open \
decision for the reviewer (keep the call in-process, add a compensation, or accept the split); while it is open, the \
feature isn't switched to its service. Unused entry points: list them as "unused", with nothing to design.
   - Utility modules (Maven coordinates, and why each is one) and God modules (each one the feature uses, and how the \
dependency is removed: what moves into this domain and what is reached through a contract).
   - Guardrail check: a table of every guardrail above with its status (met, or exception) and the evidence.
   Also write `openapi.yaml`: the domain's public API as an OpenAPI 3.1 document, one operation per method of the \
public API (the operations the router calls when they go straight to the domain, and those the orchestrator calls), \
faithful to current behaviour in what it does, with its shapes from the REST rules above rather than the legacy \
responses. The orchestrator's own operations never go in it: they have their own document (section 3). And write \
`domain-design.yaml` in this format:

{DOMAIN_DESIGN_SCHEMA}
   The four lists that must stay empty are checked, no table may be owned by another domain, and \
`other_domain_dependencies` may only name contract keys from `linked-domain-contracts.yaml`.

2. Linked-domain contracts: what the feature needs from each linked domain, so the domain can provide it from the \
monolith today and from its own service later. In `domain-design.md`, per linked domain: the contracts, what the \
feature relies on, the temporary binding, and what the owning team needs to review. Work from the consumer's side:
   - Reuse a catalogue contract that covers the need (`reuse: true`, same key); define a new contract, or a new \
version, only for what is missing.
   - One contract per capability at business level (for example "get customer KYC status"), not one per existing \
method; never the shape of a God module or of the schema, and no legacy shapes that leak internals (sessions, \
entities, flag codes): the port's adapter translates them.
   - Kind: `api` for request and response (REST under `/internal/api/v1/<provider>/...`), `event` or `command` when \
the feature relies on a message. A spec for every new or changed contract under `contracts/` (OpenAPI 3.1 or \
AsyncAPI 3, with schemas and error responses).
   - `relies_on`: freshness, consistency, idempotency, latency and behaviour on failure, as the current code behaves.
   - The temporary binding: the existing method of the linked domain's own service the monolith delegates to \
(`delegates_to`, never a God module) and when it can be removed (`remove_when`). A binding only delegates.
   - The entry point (`binding.entry_point`, for an `api` or `command`): the operations of its interface in the \
integration module, each with the `delegates_to` it reaches, together covering the binding (checked). Its classes \
are named from the key. Reuse as "The domains not migrated yet" above says. Every handler of an event \
(`event_handler` below) reaches the domains it works in through such entry points: for the work in each, define a \
`command` contract of that domain, with its binding and entry point.
   Keys are `<provider>.<capability>`. Every domain with an outbound dependency needs at least one contract, and \
every provider must be one of those domains (both checked). List what is not a contract (rules above) under "Not \
contracts" with its touchpoint and what the domain does instead; if one is the only outbound dependency on a domain \
in `domain-dependencies.yaml`, that is a conflict with the allocation (3 above): ask the user as described there. Write \
`linked-domain-contracts.yaml` in this format:

{LINKED_DOMAIN_CONTRACTS_SCHEMA}

3. The orchestrator, in `orchestrator-design.md` (the Orchestrator in monolith step builds exactly this). When \
`functional-allocation.yaml` places nothing on the orchestrator (no `orchestrator` functionality, no `other-domain` \
asked via `orchestrator`, no event `triggered_from: orchestrator`), there is nothing to orchestrate: the domain does \
its work, its direct calls and its publishing itself. Write `orchestrator-design.md` as a short note saying so, and \
in `domain-design.yaml` `orchestrator_module: none` with `no_orchestrator_reason` (what the feature publishes or \
calls directly, to whom): the orchestrator steps are then skipped without running. Otherwise it has an orchestrator \
(both checked against the allocation): a request flows controller → router → orchestrator → domain module; the \
orchestrator holds no database access and no transaction of its own, and the Orchestrator microservice step later \
lifts it into its own service.
   - Its classes, the operations it takes over (same names and signatures as the domain API methods the router \
calls), the ports it uses, and the order of calls per operation in each mode (sequence diagrams as text), saying \
which calls the domain module makes itself and which the orchestrator makes.
   - Per dependency: `local` mode (the adapter calls the existing in-monolith service; the default, keeping today's \
behaviour and transactions) or `contract` mode (the adapter calls the contract, served by its temporary binding until \
the linked domain has its own service), with the property that selects the mode, the contract's base address and the \
defaults. In `local` mode the orchestrator calls the contract's integration entry point (the legacy adapters behind \
it are the only code that touches legacy classes); a transaction, session or request object never crosses a port.
   - Transactions: what changes for each shared transaction in `contract` mode, and how failures are handled.
   - Error mapping, timeouts and fallbacks, so the caller sees the same result in both modes.
   - A readiness table per linked domain (its state, the mode used, what triggers the switch), and how the test \
scenarios run in each mode.
   - What it does, from `functional-allocation.yaml`: it takes over the operations whose work includes an \
`orchestrator` functionality, an `other-domain` one asked via `orchestrator`, or an event `triggered_from: \
orchestrator` (the "Entry points" table's Goes to); in each it does exactly those, and leaves the `domain` and \
`domain-call` functionalities to the domain module, which it calls. The other operations go straight to the domain. \
Never move a functionality between them. Where the legacy code makes a call the allocation gives to the orchestrator \
in the middle of a method, split that method: name the domain API steps before and after the call, which the \
orchestrator calls in order with the call between them (the Module in monolith step splits its copy exactly there).
   Also write `orchestrator-openapi.yaml`, the orchestrator's own API, separate from the domain's `openapi.yaml`: an \
OpenAPI 3.1 document with one operation per orchestrator operation (what the monolith's router calls), under \
`/internal/api/v1/<domain>/orchestrator/`, with the request and response of the domain operation it takes over and \
the REST rules above. The Orchestrator microservice step implements exactly this API. No orchestrator: no file.

4. Integration decisions: for every interaction in `cross-domain.yaml`, the pattern once the feature runs in its own \
service. Use the contract keys of section 2 instead of defining parallel ones; add only what they do not cover \
(events, sagas, reference copies, deferred interactions). {_EVENT_HANDLER_DECISIONS}

{_ALLOCATION_DECIDES_PATTERN}

{_INTEGRATION_RULES}
   Messaging is Azure Service Bus: one topic per publishing domain, one subscription per consuming domain, queues for \
saga commands, CloudEvents-style envelopes, sessions (SessionId = aggregate id) where order matters, dead-letter \
queues monitored. In `domain-design.md`: each interaction, the pattern and why, the monolith facades and events needed \
while other domains are still in the monolith, saga steps and compensations, and anything deferred with its \
follow-up. Write `integration-decisions.yaml` in this format, with a spec under `contracts/` for each new contract:

{DECISIONS_SCHEMA}

End `domain-design.md` with the risks and open decisions for the reviewer. Besides placement conflicts (above), \
ask the user only about what this design decides itself and the code and the approved documents can't settle: \
ownership of a class or table, a capability that only a God module provides, a shared transaction that cannot stay \
consistent in `contract` mode, or a choice within a placement that depends on the business's tolerance for stale data \
or another team's plans. Never ask whether a placement is right.

A run without `functional-allocation.yaml` (it started before the Domain dependencies step wrote one): decide each \
placement yourself, and write the "Functional allocation" table with domain, orchestrator, other domain or UI \
unchanged as owners. The feature then has an orchestrator unless every outbound dependency in \
`domain-dependencies.yaml` is a `side-effect` it only publishes (checked), and there are no direct calls between \
domains: every call to another domain goes through the orchestrator.""",

    "modernise_and_test": f"""You modernise the domain microservice to Java 25 and Spring Boot 4, replacing iBatis with \
Spring Data JPA mapped onto the existing tables, and you give the modernised service its unit and integration tests. \
The acceptance suite in the lifted service's `acceptance-tests/` (written by the Independent tests step, see \
`test-report.md`) is the independent proof that behaviour is unchanged. Which repository is the lifted service and \
which the modern one (yours), what to convert, from where, and into which packages: "The lifted feature" and "This \
domain's services" below.

{_FIXED_RULES}

Hard rules for this step. Checks compare the service's repository with the commit the step started from, and the \
step cannot finish while they fail.
- Change code only in the modern service's repository (this step's Changes code setting); never the lifted \
service's repository, the orchestrator service's or the monolith.
- Never change the acceptance suite, wherever it is (`check_test_guardrails`): nothing under `acceptance-tests/` \
changes, and it is not copied into the modern service. Run it, unchanged, from the lifted service's checkout against \
the modern service: every test that passed against the lifted service must still pass.
- Same schema: entities map the existing tables and columns as they are, and Hibernate never manages the schema \
(`spring.jpa.hibernate.ddl-auto=none`, or `validate` in tests). No secrets in the repository (`check_release_guardrails`).
- Keep the REST contract (`openapi.yaml`, with the REST rules: status codes, error body and headers) and the \
cross-domain contracts unchanged. Run `check_boundaries` and fix every violation.

Procedure:
1. Modernise: upgrade the build, dependencies and code; use modern Java where it simplifies the code. Replace each \
iBatis statement with JPA entities and repositories that produce an equivalent query. Migrate the service's own \
tests to the new stack.
2. Add the tests, on the code that stays: controllers with `@WebMvcTest` (every operation of `openapi.yaml` with the \
REST rules: 201 for POST and 200 otherwise, {{"status": "Success"}} where nothing is returned, the error body for \
each error, the request headers read and the response headers echoed); repositories with `@DataJpaTest` against the \
existing schema (Testcontainers with the project's database when Docker is available, otherwise the test database \
the environment configures), a test of each rewritten query's result; services with Mockito, each business rule and \
message the domain owns (the BR and MSG ids of `functional-spec.md`, found in the code through `as-is.md`; in a run \
without one, each business rule and error branch of `as-is.md`), named after the ids and the scenario ids of \
`test-scenarios.md` they cover, and each error's `errorCode` as `domain-design.md`'s "Errors" table gives it; the adapters to other \
domains against a stub of their contract. Test libraries: JUnit 5, Mockito, AssertJ, Spring Boot's test support and \
Testcontainers; anything else, ask. A test that exposes a bug is a finding: report it.
3. Run the modern service's whole test build, then the acceptance suite against the running modern service, then \
the checks. When the \
feature has an orchestrator suite (`orchestrator-test-report.md`), also run it, unchanged, from the run's checkout of \
the orchestrator service's repository (read only: never change it): the orchestrator service started pointed at the \
modernised service, every test that passed in its baseline must still pass.

Write `modernisation.md`: the changes; every query whose SQL differs from the iBatis statement it replaces, and why the \
result is the same; the acceptance suite's results next to its baseline in `test-report.md` (and the orchestrator suite's next to its \
baseline in `orchestrator-test-report.md`, when there is one); the tests added (by \
layer), the scenario ids each covers, the coverage of the code the feature moved; and any finding.

{_COVERAGE} Here: the domain's BR and MSG ids, each with the unit or integration test that proves it. List the \
domain's requirements (FR) too where a test of yours exercises one: that is supporting evidence, as a requirement \
counts once the domain's acceptance suite proves it.""",
}

# Each agent's guardrails: the sections of its instructions that say what it must not do (the fixed rules, the Modulith
# guardrails and what they mean for the step, the step's hard rules), given as (first words, the words that follow the
# section). They are kept in the agent's Guardrails, not its instructions, so they can be read and edited in one place;
# the agent gets them once, as a Guardrails section after its instructions (app/custom_guardrails.py).
_GUARDRAIL_SPANS: dict[str, list[tuple[str, str]]] = {
    "linked_domain_contracts": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet.")],
    "domain_design": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet.")],
    "orchestrator_design": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet.")],
    "modular_monolith": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet."),
                         ("Hard rules for this step.", "\n\nRead first:")],
    "monolith_orchestrator": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet."),
                              ("Hard rules for this step.", "\n\nRead first:")],
    "microservice_scaffold": [("Modulith guardrails. The target", "\n\nThe domains not migrated yet.")],
    "orchestrator_service": [("Hard rules for this step.", "\n\nREST API design rules.")],
    "independent_tests": [("Hard rules for this step.", "\n\nWhat the suite covers:")],
    "orchestrator_tests": [("Hard rules for this step.", "\n\nRead first:")],
    "service_tests": [("Hard rules for this step.", "\n\nWhat to test")],
    "analysis": [("Fixed rules of this migration.", "\n\nFind every entry point of the functionality first")],
    "dependency_analysis": [("Fixed rules of this migration.", "\n\nBuild on the Analysis step's documents"),
                            ("Never a dependency on another domain", "\n\nIf a touchpoint you find")],
    "modulith_design": [("Fixed rules of this migration.", "\n\nThe domains not migrated yet.")],
    "modernise_and_test": [("Fixed rules of this migration.", "\n\nProcedure:")],
}


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def without(text: str, sections: list[str]) -> str:
    """The text with these sections taken out (and the blank lines they leave closed up)."""
    for section in sections:
        text = text.replace(section, "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# stage -> its guardrail sections, word for word as they were in its instructions (shared texts written out).
GUARDRAIL_SECTIONS = {stage: [_section(INSTRUCTIONS[stage], start, end) for start, end in spans]
                      for stage, spans in _GUARDRAIL_SPANS.items()}
# The instructions as earlier releases stored them, guardrails included (to recognise them when moving them out).
INSTRUCTIONS_WITH_GUARDRAILS = INSTRUCTIONS
INSTRUCTIONS = {stage: without(text, GUARDRAIL_SECTIONS.get(stage, [])) for stage, text in INSTRUCTIONS.items()}
GUARDRAILS = {stage: "\n\n".join(sections) for stage, sections in GUARDRAIL_SECTIONS.items()}

OUTPUTS = {
    "as_is_doc": ["as-is.md"],
    "deep_dive": ["deep-dive.md", "test-scenarios.md", "cross-domain.yaml"],
    "domain_dependencies": ["domain-dependencies.md", "domain-dependencies.yaml", "functional-allocation.yaml"],
    "domain_design": ["domain-design.md", "domain-design.yaml", "openapi.yaml"],
    "linked_domain_contracts": ["linked-domain-contracts.md", "linked-domain-contracts.yaml"],
    "wrapper_design": ["wrapper-design.md", "wrapper-api.yaml"],
    "orchestrator_design": ["orchestrator-design.md"],
    "boundary_design": ["boundary-design.md", "openapi.yaml"],
    "integration_design": ["integration-design.md", "integration-decisions.yaml"],
    "modular_monolith": ["modular-monolith.md", "module-extraction.yaml"],
    "monolith_orchestrator": ["orchestrator-module.md", "orchestrator-extraction.yaml"],
    "microservice_scaffold": ["microservice.md", "microservice-extraction.yaml"],
    "orchestrator_service": ["orchestrator-service.md", "orchestrator-service-extraction.yaml"],
    "independent_tests": ["test-report.md"],
    "orchestrator_tests": ["orchestrator-test-report.md"],
    "service_tests": ["service-tests.md"],
    "modernise": ["modernisation.md"],
    "monolith_integration": ["integration.md", "integration-routing.yaml"],
    "bounded_contexts": ["bounded-contexts.md", "bounded-contexts.yaml"],
    "domain_features": ["features.md", "features.yaml"],
    "analysis": ["as-is.md", "functional-spec.md", "test-scenarios.md"],
    "dependency_analysis": ["cross-domain.yaml", "domain-dependencies.yaml", "functional-allocation.yaml"],
    "modulith_design": ["domain-design.md", "domain-design.yaml", "openapi.yaml", "linked-domain-contracts.yaml",
                        "orchestrator-design.md", "integration-decisions.yaml"],
    "modernise_and_test": ["modernisation.md"],
}

_FORMAT_NOTE = ("A machine-readable format the studio validates the step's output against. Change it "
                "only together with the code that reads it.")
_MODERN_JDK = {"modernise", "monolith_integration", "orchestrator_service", "orchestrator_tests", "service_tests",
               "modernise_and_test"}
_ANALYSIS = {"as_is_doc", "deep_dive", "domain_dependencies", "linked_domain_contracts", "domain_design",
             "wrapper_design", "orchestrator_design", "boundary_design", "integration_design", "bounded_contexts",
             "domain_features", "analysis", "dependency_analysis", "modulith_design"}
# The lean workflow's agents and the agents whose work each does: guidance a domain gives those (where to look, notes)
# reaches it too (orchestrator/graph.py _inputs), so a domain set up for the standard workflow needs nothing new.
MERGED_FROM = {"analysis": ("as_is_doc", "deep_dive"),
               # The dependency work moved out of Analysis: what a domain says for either reaches it.
               "dependency_analysis": ("analysis", "domain_dependencies"),
               "modulith_design": ("linked_domain_contracts", "domain_design", "orchestrator_design",
                                   "integration_design"),
               "modernise_and_test": ("modernise", "service_tests"),
               # The orchestrator's acceptance suite: what a domain says about acceptance tests reaches it too.
               "orchestrator_tests": ("independent_tests",)}
# Documents-only agents that do the work of several (the lean workflow's): more turns than one of those.
_MORE_TURNS = {"analysis": 150, "dependency_analysis": 120, "modulith_design": 150}
_GUARDRAIL_TOOLS = {"modular_monolith": ["check_module_guardrails"],
                    "monolith_orchestrator": ["check_orchestrator_guardrails"],
                    "microservice_scaffold": ["check_service_guardrails"],
                    "orchestrator_service": ["check_orchestrator_service_guardrails"],
                    "independent_tests": ["check_test_guardrails"],
                    "orchestrator_tests": ["check_test_guardrails"],
                    "modernise": ["check_test_guardrails", "check_release_guardrails"],
                    "service_tests": ["check_test_guardrails"],
                    "monolith_integration": ["check_release_guardrails"],
                    "modernise_and_test": ["check_test_guardrails", "check_release_guardrails"]}
_CHECKS = {"modular_monolith": ["module_guardrails"], "monolith_orchestrator": ["orchestrator_guardrails"],
           "microservice_scaffold": ["boundary", "service_guardrails"],
           "orchestrator_service": ["boundary", "orchestrator_service_guardrails"],
           "independent_tests": ["acceptance_test_guardrails"],
           "orchestrator_tests": ["acceptance_test_guardrails"],
           "modernise": ["boundary", "acceptance_suite_unchanged", "modernise_guardrails"],
           "service_tests": ["service_test_guardrails"],
           "monolith_integration": ["integration_guardrails"],
           "modernise_and_test": ["boundary", "acceptance_suite_unchanged", "modernise_guardrails"]}


# The texts below are only the defaults the database is seeded with (app/shared_texts.py): agents read them from the
# database, where they are edited on the Agents page (Shared texts), and Reset puts a default back.

# The start of every agent's system prompt, before its instructions.
PREAMBLE = """You are one stage agent in Migration Studio, which migrates a legacy monolith to microservices one \
operation at a time. The project's language and stacks are given at the start of your task; where the stage \
instructions assume another language (for example Java), apply them to the project's language instead. You work in \
the run's workspace using the tools provided; all paths are relative to the workspace root, and the monolith's code \
is at its usual paths there (read only, or a git checkout where your step changes it: "Repositories" below says \
which). Earlier stages' documents are in the workspace, and a person reviews your output before the migration moves \
on.

When you are done, reply with a short summary of what you produced and anything the reviewer should check, \
without calling a tool.

Stage instructions follow."""

# Given to every agent of a migration run, whatever its own instructions say (analysis and design alike).
FEATURE_RULES = """**Stay on this feature.** This run migrates this feature only; the domain's other features are out \
of scope (listed below), even where they share its controllers, services, DAOs, JSPs or tables.
- Analysis (AS-IS, deep dive, dependencies): follow only the code paths that start at this feature's entry points. \
This feature's entry points include every caller of its functionality, in any domain or external system (another \
domain's flow that uses it, say, or a partner's web service): Analysis finds them. Trace each only from its entry \
point to the call; the caller's own logic, beyond what it passes, what it expects back and whether it shares the \
transaction, stays out. In shared classes, cover only the methods and branches this feature reaches; leave the rest \
out.
- Design (contracts, domain, orchestration, integration, code): design for this feature's flow only. Don't add APIs, \
operations, events, entities or contracts that only another feature needs; an operation one of this feature's entry \
points needs (whichever domain it is in) is this feature's. Where a shared table or component must \
keep working for the other features, note that as a constraint; don't design it.
- Earlier documents of this run may mention other features or flows; use only what concerns this one.
- If you can't tell whether something belongs to this feature, ask the user rather than include it."""

# Shared texts: key -> (title, description, default text). Instructions include a block with its placeholder {{key}}.
# The domain architecture the module and service steps build to, and the Modernise step's two-repository rules: shared
# texts too, so they can be edited. The studio fills in each {{data.name}} for the run (app/domain_modules.py brief).
DOMAIN_ARCHITECTURE = """## This domain's modules
Domain `{{data.domain}}` has ONE domain module and ONE orchestrator module in the monolith: two separate Maven \
modules (each with its own folder, artifact and base package; the orchestrator module depends on the domain module), \
both named for the domain and shared by all its features: each feature extends them. Their names come from the \
project's settings and are listed below: use exactly those. The domain module's base package is \
`<prefix>.<domain>`, the orchestrator module's `<prefix>.orchestrator.<domain>`. Never create a second one, never \
name one after a feature, and never copy again what is already in them: reuse it.

{{data.branching}} When a module, service project or package listed below isn't in your checkout, don't wait for \
it and don't ask for it: create it at the place, with the artifact (or coordinates) and base package, given below, \
holding only what this feature needs, so the work merges into one. Change another feature's code only where this \
feature needs it, as little as possible, so the merges stay simple.

**One package per feature.** Inside each module, every class goes in the package of the feature it belongs to, \
`<base_package>.<feature package>` (sub-packages below it are fine), never directly in `<base_package>`. The \
domain's types several features use and none owns (entities, DAOs, DTOs) go in `<base_package>.shared`; stateless \
helpers several features use (formatters, mappers, validators: no business rules, no database access, no state) go \
in `<base_package>.util`. Helpers of the monolith's utility jars (`shared_code`, `utility_modules`) are not copied: \
the module depends on the jar. Code this feature uses from another feature of the domain goes in THAT feature's \
package, on this feature's branch, which you create if it doesn't exist yet, even when that feature's migration \
hasn't started or is in progress on its own branch (never touch its run); list those features in `uses_features`. \
Each feature's SQL statements go in a SQL map beside its package. Declare the module's `base_package` (the one \
given below).
Feature packages (key → package): {{data.feature_packages}}.
{{data.modules}}

## This domain's services
The domain has ONE project of each service below (the lifted service, the modern service built from it, and the \
orchestrator service), each in the repository the domain sets for its agent (Agent inputs), shared by all its \
features: every feature is added to the same project, never a second project or repository. Their base packages \
come from the project's settings and are listed below: the lifted and modern services' `<prefix>.<domain>`, the \
orchestrator service's `<prefix>.orchestrator.<domain>`. Inside each, everything \
of a feature (controllers, request and response objects, DTOs, services, DAOs, JPA repositories and entities, mappers, \
its tests) goes in `<base_package>.<feature package>` (the packages above); classes several features use (the \
exception handler, filters, request-header and contract adapters, REST clients, the event publisher, configuration, \
shared types) in `<base_package>.shared`; stateless helpers in `<base_package>.util`; only the application's main \
class sits in `<base_package>`. Each operation's controller and request/response objects go in the package of the feature the \
operation belongs to. Functionality of another feature this feature uses goes in that feature's package, on this \
feature's branch (create it if needed), called by method calls, and listed in `uses_features`. The modern service holds every feature of the \
domain, so packaging is critical there: keep the lifted service's base package and each class in the same package as \
in the lift. Never move, rename or delete another feature's classes.
{{data.services}}"""

LIFT_RULES = """## The lifted feature
The domain keeps two repositories: the lifted service (the module's stack, every feature) at `{{data.lift_path}}/` \
(`{{data.lift_url}}`), READ ONLY for you, and the modern service, this step's repository. This run lifted \
{{data.feature}} there; these are exactly its files (from the lift's commit {{data.lift_commit}}):
{{data.lifted_files}}
Convert exactly these into the same packages of the modern service, on its stack: the feature's package, `shared`, \
`util`, and the packages of the features in `uses_features`; with their REST layer, adapters, events and tests as \
the lift has them. Change no other feature's code beyond what this feature added to it. When the modern service has \
no project yet (the domain's first feature), create it first. The acceptance suite is in the lift's \
`acceptance-tests/`: run it, unchanged, against the modern service. Keep the lifted service's base package \
({{data.base_package}}) in the modern service: the same packages, per feature, `shared` and `util`."""

# The run data each of these texts must keep (saving it without one is refused: the agents would lose the data).
SHARED_TEXT_DATA = {"domain_architecture": ("feature_packages", "modules", "services"),
                    "lift_rules": ("lift_path", "lifted_files", "feature")}
# Every run datum the studio fills in for each text: any other {{data.name}} is refused (it would come out empty).
SHARED_TEXT_FILLED = {"domain_architecture": ("domain", "branching", "feature_packages", "modules", "services"),
                      "lift_rules": ("lift_path", "lift_url", "feature", "lift_commit", "lifted_files", "base_package")}

SHARED_TEXTS: dict[str, tuple[str, str, str]] = {
    "domain_architecture": ("Domain architecture", "Given to the module and service steps: one domain module and one "
                            "service project per domain, one package per feature. The studio fills in "
                            "{{data.domain}}, {{data.branching}}, {{data.feature_packages}}, {{data.modules}} and "
                            "{{data.services}} for the run: keep them.", DOMAIN_ARCHITECTURE),
    "lift_rules": ("Two repositories: the lifted feature", "Given to the Modernise step: what it converts from the "
                   "lifted service, and how. The studio fills in {{data.lift_path}}, {{data.lift_url}}, "
                   "{{data.feature}}, {{data.lift_commit}}, {{data.lifted_files}} and {{data.base_package}} for the "
                   "run: keep them.", LIFT_RULES),
    "preamble": ("Preamble", "Given to every agent before its own instructions.", PREAMBLE),
    "feature_rules": ("Stay on this feature", "Given to every agent of a feature's migration, with the feature's scope.",
                      FEATURE_RULES),
    "legacy": ("Legacy stack", "The monolith's technology, for the agents that read or change it.", _LEGACY),
    "modulith_guardrails": ("Modulith guardrails", "The rules the Modulith design steps design to.", _MODULITH_GUARDRAILS),
    "legacy_modules": ("The domains not migrated yet", "Given to the design and module steps: the temporary "
                       "integration and event-handler modules of the monolith, one package per domain, through which "
                       "a feature reaches a domain still in the monolith.", _LEGACY_MODULES),
    "rest_rules": ("REST design rules", "The norm for every REST API a design step specifies.", _REST_RULES),
    "integration_rules": ("Integration decision rules", "How the integration design decides each cross-domain "
                          "interaction.", _INTEGRATION_RULES),
    "cross_domain_format": ("Format: cross-domain interactions", _FORMAT_NOTE, CROSS_DOMAIN_SCHEMA),
    "decisions_format": ("Format: integration decisions", _FORMAT_NOTE, DECISIONS_SCHEMA),
    "bounded_contexts_format": ("Format: bounded contexts", _FORMAT_NOTE, BOUNDED_CONTEXTS_SCHEMA),
    "features_format": ("Format: domain features", _FORMAT_NOTE, FEATURES_SCHEMA),
    "domain_dependencies_format": ("Format: domain dependencies", _FORMAT_NOTE, DOMAIN_DEPENDENCIES_SCHEMA),
    "functional_allocation_format": ("Format: functional allocation", _FORMAT_NOTE, FUNCTIONAL_ALLOCATION_SCHEMA),
    "allocation_rules": ("Functional allocation rules", "How the dependency steps decide where each functionality "
                         "goes (domain, orchestrator, event handler, another domain, the UI), which a reviewer "
                         "approves before the design.", _ALLOCATION_RULES),
    "linked_domain_contracts_format": ("Format: linked-domain contracts", _FORMAT_NOTE, LINKED_DOMAIN_CONTRACTS_SCHEMA),
    "domain_design_format": ("Format: domain design", _FORMAT_NOTE, DOMAIN_DESIGN_SCHEMA),
    "module_extraction_format": ("Format: module extraction", _FORMAT_NOTE, MODULE_EXTRACTION_SCHEMA),
    "orchestrator_extraction_format": ("Format: orchestrator extraction", _FORMAT_NOTE, ORCHESTRATOR_EXTRACTION_SCHEMA),
    "orchestrator_service_format": ("Format: orchestrator service", _FORMAT_NOTE, ORCHESTRATOR_SERVICE_SCHEMA),
    "integration_routing_format": ("Format: integration routing", _FORMAT_NOTE, INTEGRATION_ROUTING_SCHEMA),
    "service_extraction_format": ("Format: microservice extraction", _FORMAT_NOTE, SERVICE_EXTRACTION_SCHEMA),
    "fixed_rules": ("Fixed rules", "The migration's fixed rules (UI and database frozen, step by step, the toggle), "
                    "for the lean workflow's agents.", _FIXED_RULES),
    "not_contracts": ("Not dependencies", "What the analysis and the design never treat as a dependency on another "
                      "domain (reference numbers, format validation).", _NOT_CONTRACTS),
    "functional_spec_format": ("Format: functional specification", "The functional specification Analysis writes "
                               "for business users and business analysts. Keep its numbered sections and the FR, "
                               "BR, UC and MSG ids: the studio checks them, and the later steps report their "
                               "coverage by these ids.", FUNCTIONAL_SPEC_FORMAT),
    "test_scenarios_format": ("Format: test scenarios", "The test scenario catalogue Analysis writes for the test "
                              "team. Keep the TS ids and the Covers line: the studio checks every requirement and "
                              "rule is covered.", TEST_SCENARIOS_FORMAT),
}


def templated(text: str) -> str:
    """Instructions with each shared block's default text replaced by its placeholder."""
    for key, (_title, _description, block) in sorted(SHARED_TEXTS.items(), key=lambda kv: -len(kv[1][2])):
        if key not in ("preamble", "feature_rules"):
            text = text.replace(block, "{{" + key + "}}")
    return text


# The built-in agents' default instructions as stored: with placeholders for the shared blocks.
TEMPLATES = {key: templated(text) for key, text in INSTRUCTIONS.items()}
# The built-in agents' default guardrails as stored, and their sections one by one (to move them out of stored
# instructions: agent_store._move_guardrails_out_of_instructions).
GUARDRAIL_TEMPLATES = {key: templated(text) for key, text in GUARDRAILS.items()}
GUARDRAIL_SECTION_TEMPLATES = {key: [templated(s) for s in sections] for key, sections in GUARDRAIL_SECTIONS.items()}


# Analysis in a run whose workflow has no Domain dependencies step (it started before there was one): it does that
# step's work too, as it did then. Added to its instructions for such runs (orchestrator/graph.py), with these outputs.
ANALYSIS_DEPENDENCY_OUTPUTS = ["cross-domain.yaml", "domain-dependencies.yaml"]
ANALYSIS_WITH_DEPENDENCIES = templated(f"""

## This run has no Domain dependencies step: you find the dependencies too
This run started before the dependency work became its own step. This section overrides what the instructions above \
say about the dependencies being the next step's work: in this run you do that work too, and also write:
4. `cross-domain.yaml`: every place this feature touches another domain (calls to its services or DAOs, reads or \
writes of its tables, shared transactions, other domains reading this domain's tables, notifications or audit written \
as a side effect), in this format:

{CROSS_DOMAIN_SCHEMA}
5. `domain-dependencies.yaml`: per domain the feature is related to, its exact touchpoints, and the scope decision, \
in this format:

{DOMAIN_DEPENDENCIES_SCHEMA}

{_DEPENDENCY_RULES}

{_NOT_CONTRACTS}""")

def default_config(stage: str) -> dict:
    return {
        "mode": "stub",
        "model": AUTO,
        "effort": "high",
        "jdk": "modern" if stage in _MODERN_JDK else "legacy",
        "instructions": TEMPLATES[stage],
        "tools": [*(DOCS if stage in _ANALYSIS else CODE), *_GUARDRAIL_TOOLS.get(stage, [])],
        "outputs": list(OUTPUTS[stage]),
        "max_turns": 200 if stage in _GUARDRAIL_TOOLS else _MORE_TURNS.get(stage, 80),
        "checks": _CHECKS.get(stage, []),
        "guardrails": GUARDRAIL_TEMPLATES.get(stage, ""),
    }
