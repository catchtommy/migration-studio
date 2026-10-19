"""Guardrails of the Orchestrator microservice step, checked from git rather than trusted from the agent's word.

The step lifts one feature's orchestrator module (built by the Orchestrator in monolith step) into its own Spring Boot
service, which calls the domain microservice and the other domains over their APIs. Compared with the commit the step
started from in the service's repository:

- the monolith repository is untouched;
- the service holds no database of any kind: no data access or transactions in the code, no SQL, no data-access
  library (JDBC, JPA, iBatis, MyBatis, a driver, a migration tool) and no datasource configuration;
- the orchestrator module's classes and tests are copied with only package and import lines changed, unless the
  declaration lists them under `replaced`;
- the service is Java 25 on Spring Boot, with no secrets, UI or DDL, and no other domain's code;
- it exposes exactly the operations of its declaration, which are the operations of its OpenAPI document;
- a feature declared without an orchestrator service (`orchestrator: none`) changes no code at all.

`check()` returns one line per violation, empty when the step is within its limits.
"""

import re
from pathlib import Path

from app import module_guardrails as mg
from app import orchestrator_guardrails as og
from app import service_guardrails as sg

DATA_ACCESS_LIBRARY = re.compile(r"jdbc|jpa|hibernate|ibatis|mybatis|ojdbc|h2database|hsqldb|derby|postgresql|mysql|"
                                 r"mariadb|mssql|flyway|liquibase|spring-boot-starter-data|spring-data|spring-tx|"
                                 r"spring-orm|r2dbc|jooq", re.IGNORECASE)
DATASOURCE_CONFIG = re.compile(r"(?im)^\s*(?:spring\.(?:datasource|jpa|flyway|liquibase|sql)\b|(?:datasource|jpa|flyway|"
                               r"liquibase)\s*:)|jdbc:")
SPRING_BOOT = re.compile(r"<artifactId>\s*spring-boot-(?:starter-parent|dependencies)\s*</artifactId>\s*"
                         r"<version>\s*([^<\s]+)\s*</version>")
JAVA_LEVEL = re.compile(r"<(?:java\.version|maven\.compiler\.(?:release|source|target)|release|source|target)>\s*"
                        r"([^<\s]+)\s*<")


def check(service: Path, base: str, monolith: Path | None, monolith_base: str | None, declaration: dict,
          module_path: str | None, openapi_text: str | None, ownership: dict[str, dict], domain: str) -> list[str]:
    """Violations of the step's limits since `base` in the service repository. `declaration` is
    orchestrator-service-extraction.yaml (parsed), `module_path` the orchestrator module's folder in the monolith and
    `openapi_text` the service's own OpenAPI document."""
    problems: list[str] = []

    # 1. The monolith repository is read-only in this step.
    if monolith is not None and monolith_base:
        try:
            touched = mg.changes(monolith, monolith_base)
        except (RuntimeError, OSError):
            touched = []
        problems += [f"The monolith repository changed: {c.path}. This step changes only the service's repository"
                     for c in touched[:5]]
    try:
        diff = mg.changes(service, base)
    except (RuntimeError, OSError) as e:
        return [*problems, f"Could not compare with the starting commit: {e}"]
    if declaration.get("orchestrator") == "none":
        return [*problems, *[f"{c.path} changed, but orchestrator-service-extraction.yaml says there is no orchestrator "
                             "service: this step then changes no code" for c in diff]][:mg.MAX_REPORTED]
    live = [c for c in diff if c.status != "D"]

    # 2. No database of any kind.
    for c in live:
        text = mg._read(service, c.path)
        if c.path.endswith(".java") and not mg._is_test(c.path):
            code = og.COMMENTS.sub("", text)
            hit = og.DB_ACCESS.search(code) or re.search(r"@(?:Entity|Table|Repository)\b|\bJpaRepository\b|"
                                                         r"\bCrudRepository\b", code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0)}. The orchestrator service holds no database: data stays "
                                "in the domain service, reached through its API")
            hit = og.TRANSACTION.search(code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0).strip()}. The orchestrator service has no transactions")
        if Path(c.path).suffix.lower() in (".sql", ".ddl") or (c.path.endswith(".xml") and mg.statements(text)):
            problems.append(f"{c.path} holds SQL. The orchestrator service holds no database")
        if c.path.endswith((".properties", ".yml", ".yaml")) and not mg._is_test(c.path) and DATASOURCE_CONFIG.search(text):
            problems.append(f"{c.path} configures a datasource or a database tool. The orchestrator service holds no "
                            "database")

    # 3. UI, DDL and secrets.
    for c in live:
        if Path(c.path).suffix.lower() in mg.UI_SUFFIXES and not mg._is_test(c.path):
            problems.append(f"UI file in the service: {c.path}. The service has no UI")
        if not mg._is_test(c.path) and c.path.endswith((".properties", ".yml", ".yaml", ".xml", ".java")):
            pattern = sg.SECRET_PROPERTY if c.path.endswith((".properties", ".yml", ".yaml")) else sg.SECRET_QUOTED
            hit = next((line for line in c.added if pattern.search(line)), None)
            if hit:
                problems.append(f"Possible secret in {c.path}: `{hit.strip()[:60]}…`. Credentials and addresses come "
                                "from configuration or the environment (a ${...} placeholder), never the repository")

    # 4. Build: Java 25 on Spring Boot, and no data-access library.
    poms = [c for c in live if Path(c.path).name == "pom.xml"]
    for c in poms:
        text = mg._read(service, c.path)
        levels = {v for v in JAVA_LEVEL.findall(text)}
        if any(v != "25" for v in levels):
            problems.append(f"{c.path}: the Java level must be 25 (found {', '.join(sorted(levels))})")
        for g, a, _v in mg._pom_deps(text):
            if DATA_ACCESS_LIBRARY.search(f"{g}:{a}"):
                problems.append(f"{c.path}: dependency {g}:{a} is for data access. The orchestrator service holds no "
                                "database")
    root_pom = service / "pom.xml"
    if poms and root_pom.is_file():
        boot = SPRING_BOOT.search(root_pom.read_text(encoding="utf-8", errors="replace"))
        if not boot:
            problems.append("pom.xml: the service is a Spring Boot application: use the Spring Boot 4 parent "
                            "(spring-boot-starter-parent) or import its BOM (spring-boot-dependencies)")
        elif not boot.group(1).startswith("4."):
            problems.append(f"pom.xml: Spring Boot {boot.group(1)}; the service uses Spring Boot 4")

    # 5. The orchestrator module's code is copied unchanged, unless replaced.
    replaced = {str(r.get("file")) for r in declaration.get("replaced") or [] if isinstance(r, dict)}
    copies: dict[str, list[Path]] = {}
    for kind in ("main", "test"):
        for f in sorted(service.rglob("*.java")):
            if f"src/{kind}/" in f.relative_to(service).as_posix():
                copies.setdefault(f"{kind}:{f.name}", []).append(f)
    if module_path:
        for kind in ("main", "test"):
            for f in sg._files(monolith or service, module_path, kind):
                if not f.name.endswith(sg.PARITY_SUFFIXES) or f.name == "package-info.java" or f.name in replaced:
                    continue
                matches = copies.get(f"{kind}:{f.name}", [])
                if not matches:
                    problems.append(f"{f.name} ({kind}) of the orchestrator module was not copied to the service. Copy "
                                    "it, or list it under `replaced` with the reason")
                elif not any(sg._normal(m.read_text(encoding="utf-8", errors="replace")) ==
                             sg._normal(f.read_text(encoding="utf-8", errors="replace")) for m in matches):
                    problems.append(f"{f.name} ({kind}) differs from the orchestrator module's beyond package and import "
                                    "lines. Copy it unchanged, or list it under `replaced` with the reason")
    for c in live:
        if mg._is_test(c.path):
            hit = next((line for line in c.added if re.search(r"@Ignore|@Disabled|\bassume(?:True|False)\(", line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be ignored (`{hit.strip()[:60]}`)")

    # 6. No other domain's code.
    other_pkgs = [p for k, o in ownership.items() if k != domain for p in o.get("packages") or []]
    for c in live:
        if other_pkgs and c.path.endswith(".java"):
            text = mg._read(service, c.path)
            hit = next((p for p in other_pkgs if re.search(rf"^\s*import\s+{re.escape(p)}\b", text, re.M)), None)
            if hit:
                problems.append(f"{c.path} imports {hit}, which belongs to another domain: reach it through its "
                                "contract")

    # 7. Exactly the declared operations.
    declared = {str(o.get("operation_id")) for o in declaration.get("operations") or []}
    if openapi_text is None:
        problems.append(f"{declaration.get('openapi')} (the service's API) is missing, so the operations cannot be "
                        "checked")
    else:
        ids, missing = sg.openapi_operation_ids(openapi_text)
        problems += [f"{declaration.get('openapi')}: {m} has no operationId; add one so the operation can be traced"
                     for m in missing]
        ids = {i for i in ids if not i.lower().startswith("health")}
        problems += [f"Operation {i} is in the service's API but not in orchestrator-service-extraction.yaml"
                     for i in sorted(ids - declared)]
        problems += [f"Operation {i} is in orchestrator-service-extraction.yaml but not in the service's API"
                     for i in sorted(declared - ids)]
    return problems[:mg.MAX_REPORTED]


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run, reading the declarations in the step's documents folder."""
    from app import contracts_store as cs

    if ctx.code_dir in (".", ""):
        return ["This step must change code in the orchestrator service's own repository (the step's Changes code "
                "setting: In another repository), never in the monolith"]
    if not ctx.base_sha:
        return ["The service repository's starting commit is unknown, so the changes cannot be checked"]
    out = ctx.output_dir

    def load(name, parse):
        f = out / name
        return parse(f.read_text(encoding="utf-8")) if f.is_file() else None

    try:
        declaration = load(cs.ORCHESTRATOR_SERVICE_FILE, cs.parse_orchestrator_service)
        module = load(cs.ORCHESTRATOR_EXTRACTION_FILE, cs.parse_orchestrator_extraction)
    except cs.InvalidDocument as e:
        return [str(e)]
    if declaration is None:
        return [f"Write {cs.ORCHESTRATOR_SERVICE_FILE} (in {ctx.rel(out)}/) first: it declares the service's operations "
                "(or that there is no orchestrator service), which the check needs"]
    module_path = str(module["module"]["path"]).strip("/") if module and module["orchestrator"] == "module" else None
    if declaration["orchestrator"] == "service" and module_path is None:
        return [f"{cs.ORCHESTRATOR_EXTRACTION_FILE} of the Orchestrator in monolith step declares no orchestrator "
                "module, so there is nothing to lift"]
    api = out / str(declaration.get("openapi") or "")
    root_is_git = mg.head(ctx.workspace) is not None
    return check((ctx.workspace / ctx.code_dir).resolve(), ctx.base_sha, ctx.workspace if root_is_git else None,
                 getattr(ctx, "monolith_sha", None), declaration, module_path,
                 api.read_text(encoding="utf-8") if declaration.get("openapi") and api.is_file() else None,
                 ctx.ownership, ctx.domain)


def report(problems: list[str]) -> str:
    if not problems:
        return "Orchestrator service guardrails: PASS. Nothing outside the allowed changes."
    return ("Orchestrator service guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
