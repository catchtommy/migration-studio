/** What each document the agents write is, and how the migration uses it: shown when a document is picked or opened,
 * so a reviewer knows what they are looking at and why it matters. Keyed by file name (contracts by their folder). */

export interface DocumentGuide {
  /** What the document is, in one sentence. */
  what: string;
  /** Who reads it and what it decides or feeds later. */
  usage: string;
  /** Who should review it, when it matters. */
  audience?: string;
}

const GUIDE: Record<string, DocumentGuide> = {
  // Discovery
  "bounded-contexts.md": {
    what: "The monolith's bounded contexts: what each does, the tables and packages it owns, and where its code starts.",
    usage: "Approving it creates the domains and fills in their ownership (Boundary tab), which every later agent uses to tell domains apart.",
    audience: "Architects",
  },
  "bounded-contexts.yaml": {
    what: "The bounded contexts in machine-readable form.",
    usage: "Read by the studio when the gate is approved: each context becomes a domain with its owned tables and packages.",
  },
  "features.md": {
    what: "Every feature (business operation) of the domain, with its entry points, tables and calls to other domains.",
    usage: "Approving it makes the features available to migrate, one run per feature.",
    audience: "Architects, business analysts",
  },
  "features.yaml": {
    what: "The domain's feature list in machine-readable form.",
    usage: "Read by the studio when the gate is approved: it replaces the domain's feature list.",
  },

  // Analysis
  "as-is.md": {
    what: "How the feature works today: entry points, the call chain down to the SQL, transactions, where each rule and message is enforced, and where it crosses into other domains.",
    usage: "The technical baseline every later step builds on; the dependency step starts from its domain crossings, and the code steps copy what it locates.",
    audience: "Architects, developers",
  },
  "functional-spec.md": {
    what: "What the feature does in business language: requirements (FR), business rules (BR), use cases (UC) and messages (MSG), each with an id.",
    usage: "The ids are the migration's yardstick: the allocation and the design assign each one, and every later step reports which it covers (Functional coverage).",
    audience: "Business owners, business analysts",
  },
  "test-scenarios.md": {
    what: "The test scenario catalogue (TS ids): preconditions, test data, steps and expected result, each covering FR, BR and MSG ids.",
    usage: "The acceptance suites implement these scenarios, so the new service is proven to behave as the old code did.",
    audience: "Test team",
  },
  "deep-dive.md": {
    what: "Business rules, edge cases and exception paths, with the code that enforces each and what the user sees.",
    usage: "The rules and error cases the design must keep; the Standard workflow's counterpart of the functional specification.",
    audience: "Architects, business analysts",
  },
  "cross-domain.yaml": {
    what: "Every place the feature touches another domain (calls, table reads and writes, shared transactions, side effects), each an interaction with an id (X1, X2, …).",
    usage: "The integration decisions give each interaction a pattern; the allocation and the contracts link back to these ids.",
  },
  "domain-dependencies.yaml": {
    what: "Exactly which other domains the feature depends on (relation, direction, strength, touchpoints), and the scope decision: domain-only or cross-domain.",
    usage: "The scope decision decides which steps run (a domain-only feature has no contracts and no orchestrator); every outbound dependency needs a contract.",
    audience: "Architects",
  },
  "domain-dependencies.md": {
    what: "The dependencies and the scope decision explained for the reviewer, with what was left out and why.",
    usage: "Read at the analysis gate together with domain-dependencies.yaml.",
    audience: "Architects",
  },
  "functional-allocation.yaml": {
    what: "Where each functionality goes once migrated: the domain, a direct call from the domain to another domain, the orchestrator, an event and its handler, another domain, or the unchanged UI.",
    usage: "Approved (and changed if needed, with Change placements) at the analysis gate; the design follows it without re-deciding, and it decides whether the feature has an orchestrator.",
    audience: "Architects",
  },

  // Design
  "domain-design.md": {
    what: "The design of the feature's domain: module and packages, public API, owned tables, ports to other domains, entry points, the functional allocation table, and the linked-domain contracts and integration decisions.",
    usage: "The plan the code steps build exactly; reviewed at the design gate.",
    audience: "Architects, developers",
  },
  "domain-design.yaml": {
    what: "The design's checked declaration: modules, owned and read-only tables, utility modules, contracts used, context ports, and whether there is an orchestrator.",
    usage: "Validated against the guardrails (no schema, UI or stack change, no God module); decides whether the orchestrator steps run.",
  },
  "openapi.yaml": {
    what: "The domain microservice's REST API (OpenAPI 3.1): the operations the router or the orchestrator calls.",
    usage: "The Spring Boot microservice step implements it; the acceptance tests and the monolith integration call it.",
    audience: "Developers, API reviewers",
  },
  "orchestrator-openapi.yaml": {
    what: "The orchestrator's own REST API (OpenAPI 3.1), separate from the domain's: the operations the monolith's router calls when the feature has an orchestrator.",
    usage: "The Orchestrator microservice step implements exactly this API; the monolith calls it once the feature is switched to its services.",
    audience: "Developers, API reviewers",
  },
  "linked-domain-contracts.yaml": {
    what: "What this feature needs from each domain it depends on, as contracts (API, event or command), each with its spec, what the feature relies on, and the temporary binding: the existing legacy method that serves it until that domain is migrated.",
    usage: "Recorded in the contracts catalogue as proposed when the design is approved; the code steps build the adapters and, later, the monolith facade endpoints from it.",
    audience: "Architects, the providing domains' teams",
  },
  "linked-domain-contracts.md": {
    what: "The linked-domain contracts explained per providing domain, with what its team needs to review.",
    usage: "Share with the providing domain's team: it is what they will eventually provide from their own service.",
    audience: "Providing domains' teams",
  },
  "orchestrator-design.md": {
    what: "The orchestrator: the operations it takes over, the order of calls in each mode, local and contract modes per dependency, error mapping, and a readiness table per linked domain. A short note when there is none.",
    usage: "The Orchestrator in monolith step builds exactly this.",
    audience: "Architects, developers",
  },
  "integration-decisions.yaml": {
    what: "How each cross-domain interaction is handled once the feature runs in its own service: sync API, event, saga, read model, reference copy or deferred, with the contract it uses.",
    usage: "Recorded as the domain's dependencies (the dependency map that guides migration order); the service and monolith integration steps build REST clients, events and facades from it.",
    audience: "Architects",
  },
  "integration-design.md": {
    what: "Each cross-domain interaction, the pattern chosen and why, saga steps and compensations, and what is deferred.",
    usage: "Read at the design gate with integration-decisions.yaml.",
    audience: "Architects",
  },
  "wrapper-design.md": {
    what: "REST wrappers over other domains' existing services (an earlier release's design step).",
    usage: "Kept for runs that used it; replaced by the linked-domain contracts.",
  },
  "wrapper-api.yaml": {
    what: "The wrappers' API (an earlier release's design step).",
    usage: "Kept for runs that used it; replaced by the contract specs.",
  },
  "boundary-design.md": {
    what: "The boundary of the future service (an earlier release's design step).",
    usage: "Kept for runs that used it; replaced by the Modulith design.",
  },
  "domain-events.yaml": {
    what: "The domain events the feature publishes (AsyncAPI).",
    usage: "The service publishes them to Azure Service Bus; consumers subscribe.",
  },

  // Code steps
  "modular-monolith.md": {
    what: "What the Module in monolith step built: the feature in its domain module, the router and toggle, the local adapters, and how behaviour was proven the same.",
    usage: "The pull request description for the module change; reviewed at the domain service gate.",
    audience: "Developers, reviewers",
  },
  "module-extraction.yaml": {
    what: "The Module in monolith step's declaration of what it created and changed (modules, bridge package, router, events).",
    usage: "The guardrail check compares it with the git changes; later steps read it to find the module.",
  },
  "orchestrator-module.md": {
    what: "What the Orchestrator in monolith step built: the orchestrator module, its mode switches and contract adapters.",
    usage: "The pull request description for the orchestrator change.",
    audience: "Developers, reviewers",
  },
  "orchestrator-extraction.yaml": {
    what: "The Orchestrator in monolith step's declaration: the module, the operations it took over, its ports and their modes.",
    usage: "The guardrail check compares it with the git changes; the Orchestrator microservice step lifts what it lists.",
  },
  "microservice.md": {
    what: "What the Spring Boot microservice step built: the lifted domain service, what was copied and replaced, and how behaviour was proven unchanged.",
    usage: "The pull request description for the domain service.",
    audience: "Developers, reviewers",
  },
  "microservice-extraction.yaml": {
    what: "The microservice step's declaration: operations, events and open decisions of the lifted domain service.",
    usage: "The guardrail check compares it with the service repository.",
  },
  "orchestrator-service.md": {
    what: "What the Orchestrator microservice step built: the orchestrator lifted into its own Spring Boot service.",
    usage: "The pull request description for the orchestrator service.",
    audience: "Developers, reviewers",
  },
  "orchestrator-service-extraction.yaml": {
    what: "The Orchestrator microservice step's declaration: operations, the domain API it calls, contracts.",
    usage: "The guardrail check compares it with the orchestrator service's repository.",
  },
  "test-report.md": {
    what: "The domain service's black-box acceptance suite: which scenarios it covers and the results, with functional coverage.",
    usage: "Proves the domain service behaves as the old code did; reviewed at the domain service gate, and run again after modernisation.",
    audience: "Test team, reviewers",
  },
  "orchestrator-test-report.md": {
    what: "The orchestrator service's acceptance suite: scenarios, results and functional coverage.",
    usage: "Proves the orchestrator service behaves as the old code did; reviewed at the orchestrator service gate.",
    audience: "Test team, reviewers",
  },
  "service-tests.md": {
    what: "The modernised service's unit and integration tests.",
    usage: "Reviewed at the release gate.",
    audience: "Developers",
  },
  "modernisation.md": {
    what: "What modernisation changed: Java 25, Spring Boot 4, iBatis to Spring Data JPA on the same tables, with its tests.",
    usage: "The pull request description for the modernised service; the acceptance suite runs unchanged against it.",
    audience: "Developers, reviewers",
  },
  "integration.md": {
    what: "How the monolith was connected to the new service: the toggle's service value, the REST client, facades and messaging.",
    usage: "The pull request description for the monolith change; reviewed at the release gate.",
    audience: "Developers, reviewers",
  },
  "integration-routing.yaml": {
    what: "The Monolith integration step's declaration of its toggle: the target service, its API, and each operation's monolith and service implementation.",
    usage: "The guardrail check verifies every operation of the target API is routed.",
  },
};

const CONTRACT: DocumentGuide = {
  what: "The spec of one contract (OpenAPI 3.1 for an API, AsyncAPI 3 for an event or command): what one domain provides to another.",
  usage: "Implemented for now by the monolith (its temporary binding), later by the providing domain's own service; recorded in the contracts catalogue.",
  audience: "API reviewers, the providing domain's team",
};

/** The guide for a document, by its path or name; undefined for one the studio doesn't describe. */
export function documentGuide(pathOrName: string): DocumentGuide | undefined {
  const parts = pathOrName.split("/");
  const name = parts[parts.length - 1];
  if (parts.includes("contracts") && /\.ya?ml$/.test(name)) return CONTRACT;
  return GUIDE[name];
}
