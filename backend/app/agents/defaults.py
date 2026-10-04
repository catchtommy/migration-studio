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
}
READ_ONLY = ["list_files", "read_file", "search_code", "ask_user"]
DOCS = [*READ_ONLY, "write_file"]
CODE = [*READ_ONLY, "write_file", "edit_file", "run_command"]

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
- Calls into other domains (services, DAOs or tables owned elsewhere) and shared state.
- A test scenario catalogue: every behaviour a migrated version must preserve, each with a short id, preconditions, \
input and expected outcome. The independent tests stage implements these scenarios, so make them concrete.

Ask the user when a rule's intent is unclear, for example whether an odd branch is a bug or deliberate.""",

    "boundary_design": """You design the boundary of the future microservice that will own this operation, using the \
AS-IS documentation and deep dive.

Produce:
- The bounded context: what this service owns and what it does not.
- Table ownership: tables the service owns, tables it reads that another domain owns, and how each cross-domain \
dependency is handled (API call, event, replicated read model, or left in the monolith for now).
- The REST contract for the operation as an OpenAPI 3.1 document: paths, schemas, error responses. Keep it \
faithful to the current behaviour; do not add features.
- Risks and open decisions for the reviewer.

Ask the user about ownership decisions you cannot settle from the code.""",

    "modular_monolith": f"""You extract one operation into a module inside the monolith, shaped like the future \
microservice. {_LEGACY}

Using the approved boundary design:
- Create the module's package structure with a clear public API (service interface + DTOs) matching the REST contract.
- Move or copy the operation's controller, service, DAO and iBatis mappings into the module. Callers outside the \
module must go through its public API only.
- Keep the code Java 8 / Spring 3 / iBatis compatible. Do not change behaviour.
- Build with the project's build tool and fix compilation errors before finishing.

Summarise what moved, what still crosses the boundary, and how you verified the build.""",

    "microservice_scaffold": """You wrap the extracted module in a standalone Spring Boot application that exposes the \
approved REST contract.

- Create the service as a separate project in the workspace, reusing the module's code.
- Implement the endpoints from the OpenAPI document exactly, including error responses.
- Configure data access against the existing Oracle tables; no schema changes.
- Build it and make sure it starts. Fix failures before finishing.

Summarise the project layout, how to run it, and any deviations from the contract.""",

    "independent_tests": """You write tests that prove the microservice and the in-monolith module behave the same as \
the original code, for every scenario in the deep dive's test scenario catalogue.

- Contract tests against the OpenAPI document.
- Integration tests against the existing schema.
- Characterisation tests that pin down current behaviour, including odd edge cases.
- Run the tests and report results. A failing test that exposes a real behaviour difference is a finding: \
report it rather than weakening the test.

Map every scenario id to the tests that cover it, and list any scenario you could not cover and why.""",

    "modernise": """You modernise the microservice to Java 25 and Spring Boot 4, replacing iBatis with Spring Data JPA \
mapped onto the existing tables (no schema changes).

- Upgrade the build, dependencies and code. Use modern Java where it simplifies the code.
- Replace iBatis statements with JPA entities and repositories that produce equivalent queries.
- Keep the REST contract unchanged. All independent tests must still pass; run them.

Summarise the changes, any query differences, and the test results.""",

    "monolith_integration": f"""You connect the monolith to the new microservice behind a feature flag. {_LEGACY}

- Add a REST client in the monolith for the microservice's contract.
- Add a feature flag that routes calls to either the in-monolith module or the microservice, defaulting to the module.
- Handle timeouts and errors from the microservice explicitly.
- Build the monolith and run its tests with the flag in both positions.

Document how to switch the flag, rollback steps, and what to monitor after the switch.""",
}

OUTPUTS = {
    "as_is_doc": ["as-is.md"],
    "deep_dive": ["deep-dive.md", "test-scenarios.md"],
    "boundary_design": ["boundary-design.md", "openapi.yaml"],
    "modular_monolith": ["modular-monolith.md"],
    "microservice_scaffold": ["microservice.md"],
    "independent_tests": ["test-report.md"],
    "modernise": ["modernisation.md"],
    "monolith_integration": ["integration.md"],
}

_MODERN_JDK = {"modernise", "monolith_integration"}
_ANALYSIS = {"as_is_doc", "deep_dive", "boundary_design"}


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
    }
