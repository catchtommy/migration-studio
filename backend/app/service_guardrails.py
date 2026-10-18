"""Guardrails of the microservice step, checked from git rather than trusted from the agent's word.

The step lifts the module that Module in monolith built into its own service repository. It must stay a lift:
the copied code equals the module's apart from package and import lines (and what is declared as replaced), the SQL
is the module's SQL, the stack and libraries are the module's plus the few the service needs to run on its own, the
monolith repository is untouched, and the service exposes the approved operations and nothing else.

`check()` returns one line per violation, empty when the step is within its limits.
"""

import re
from pathlib import Path

import yaml

from app import module_guardrails as mg

# Libraries a standalone service needs that the module does not have: embedded server, JSON, Azure Service Bus, an
# HTTP client for the REST adapters, logging and the test tools the module already uses. Anything else: ask.
INFRA_GROUPS = ("com.azure", "com.microsoft.azure", "org.eclipse.jetty", "org.apache.tomcat", "com.fasterxml.jackson",
                "org.codehaus.jackson", "org.slf4j", "ch.qos.logback", "org.apache.logging.log4j", "log4j",
                "org.apache.httpcomponents", "org.mockito", "org.powermock", "junit", "org.hamcrest")
SECRET_QUOTED = re.compile(r"""(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?key|token|connection[_-]?string)"?\s*[=:]\s*["'](?!\$\{|\{\{|<)[^"'\s$]{4,}["']""")
SECRET_PROPERTY = re.compile(r"""(?im)^\s*[\w.\-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|token)[\w.\-]*\s*[=:]\s*(?!\$\{|\{\{|<|\s)[^\s#]{4,}\s*$""")
PARITY_SUFFIXES = (".java",)
METHOD_REF = re.compile(r"^\w+#\w+$")


def _normal(text: str) -> str:
    """Code without its package and import lines and without blank lines or indentation: what must be equal."""
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line and not line.startswith(("package ", "import ")))


def _files(root: Path, module: str, kind: str) -> list[Path]:
    base = root / module / "src" / kind
    return sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else []


def openapi_operation_ids(text: str) -> tuple[set[str], list[str]]:
    """The operationIds of an OpenAPI document and the operations that lack one."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return set(), []
    ids, missing = set(), []
    for path, item in (data.get("paths") or {}).items():
        for verb, op in (item or {}).items():
            if verb.lower() in ("get", "put", "post", "delete", "patch", "options", "head") and isinstance(op, dict):
                if op.get("operationId"):
                    ids.add(str(op["operationId"]))
                else:
                    missing.append(f"{verb.upper()} {path}")
    return ids, missing


def check(service: Path, base: str, monolith: Path | None, monolith_base: str | None, module_decl: dict,
          service_decl: dict | None, design: dict | None, openapi_text: str | None, ownership: dict[str, dict],
          domain: str, god_modules: list[str] | None = None) -> list[str]:
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
    modules = [m["path"].strip("/") for m in module_decl.get("modules", [])]
    replaced = {str(r.get("file")) for r in (service_decl or {}).get("replaced", []) if isinstance(r, dict)}

    # 2. UI, DDL and secrets.
    for c in diff:
        if Path(c.path).suffix.lower() in mg.UI_SUFFIXES and not mg._is_test(c.path):
            problems.append(f"UI file in the service: {c.path}. The service has no UI")
        if Path(c.path).suffix.lower() in mg.DDL_SUFFIXES and not mg._is_test(c.path):
            hit = next((line for line in c.added if mg.DDL.search(line)), None)
            if hit:
                problems.append(f"DDL in {c.path}: `{hit.strip()[:100]}`. The schema is frozen; there is no outbox table")
        if c.status == "A" and Path(c.path).suffix.lower() in (".sql", ".ddl") and not mg._is_test(c.path):
            problems.append(f"New SQL script {c.path}. The schema is frozen")
        if c.status != "D" and not mg._is_test(c.path) and c.path.endswith((".properties", ".yml", ".yaml", ".xml", ".java")):
            pattern = SECRET_PROPERTY if c.path.endswith((".properties", ".yml", ".yaml")) else SECRET_QUOTED
            hit = next((line for line in c.added if pattern.search(line)), None)
            if hit:
                problems.append(f"Possible secret in {c.path}: `{hit.strip()[:60]}…`. Credentials come from configuration "
                                "or the environment (a ${...} placeholder), never the repository")

    # 3. Code and test parity with the module: only package and import lines may differ.
    service_files: dict[str, list[Path]] = {}
    for kind in ("main", "test"):
        for f in sorted((service).rglob("*.java")):
            if f.relative_to(service).as_posix().find(f"src/{kind}/") >= 0:
                service_files.setdefault(f"{kind}:{f.name}", []).append(f)
    for module in modules:
        for kind in ("main", "test"):
            for f in _files(monolith or service, module, kind):
                if not f.name.endswith(PARITY_SUFFIXES) or f.name == "package-info.java":
                    continue
                if f.name in replaced:
                    continue
                matches = service_files.get(f"{kind}:{f.name}", [])
                if not matches:
                    problems.append(f"{f.name} ({kind}) of the module was not copied to the service. Copy it, or list it "
                                    "under `replaced` with the reason")
                elif not any(_normal(m.read_text(encoding="utf-8", errors="replace")) ==
                             _normal(f.read_text(encoding="utf-8", errors="replace")) for m in matches):
                    problems.append(f"{f.name} ({kind}) differs from the module's beyond package and import lines. "
                                    "The service is a lift: copy it unchanged, or list it under `replaced` with the reason")
    for c in diff:
        if mg._is_test(c.path) and c.status != "D":
            hit = next((line for line in c.added if re.search(r"@Ignore|@Disabled|\bassume(?:True|False)\(", line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be ignored (`{hit.strip()[:60]}`)")

    # 4. SQL: every statement in the service is a statement of the module, text for text.
    pool: list[str] = []
    for module in modules:
        for f in _files(monolith or service, module, "main"):
            if f.suffix == ".xml":
                pool += mg.statements(f.read_text(encoding="utf-8", errors="replace"))
    for c in diff:
        if c.path.endswith(".xml") and c.status != "D" and not mg._is_test(c.path):
            for s in mg.statements(mg._read(service, c.path)):
                if s in pool:
                    pool.remove(s)
                else:
                    problems.append(f"SQL in {c.path} is not a statement of the module, text for text: {s[:100]}…")
                    break

    # 5. Libraries: the module's, with the same versions, plus infrastructure; no monolith module; Java 8.
    module_deps: set[tuple[str, str]] = set()
    for module in modules:
        for pom in (monolith or service).glob(f"{module}/pom.xml"):
            module_deps |= {(g, a) for g, a, _v in mg._pom_deps(pom.read_text(encoding="utf-8", errors="replace"))}
    used, versions, monolith_group = (mg.base_pom_info(monolith, "HEAD") if monolith else (set(), {}, None))
    own = {tuple(m["artifact"].split(":", 1)) for m in module_decl.get("modules", []) if ":" in m.get("artifact", "")}
    for c in diff:
        if Path(c.path).name != "pom.xml" or c.status == "D":
            continue
        text = mg._read(service, c.path)
        level = re.findall(r"<(?:source|target|release)>\s*([^<\s]+)\s*<|(?:maven\.compiler\.(?:source|target|release)|"
                           r"java\.version)>\s*([^<\s]+)\s*<", text)
        if any(v not in ("1.8", "8") for pair in level for v in pair if v):
            problems.append(f"{c.path}: the Java level must stay Java 8 (the module's)")
        for g, a, v in mg._pom_deps(text):
            ga = f"{g}:{a}"
            if (g, a) in own or g == monolith_group:
                problems.append(f"{c.path}: dependency {ga} is a module of the monolith. The service has its own copy of "
                                "the code and must not depend on the monolith")
            elif (g, a) in module_deps or (g, a) in used:
                if v and not v.startswith("${") and versions.get((g, a)) and v not in versions[(g, a)]:
                    problems.append(f"{c.path}: dependency {ga} declares version {v}; the module uses "
                                    f"{', '.join(sorted(versions[(g, a)]))}")
            elif not g.startswith(INFRA_GROUPS):
                problems.append(f"{c.path}: dependency {ga} is not one the module uses or an allowed infrastructure "
                                "library (embedded server, JSON, Azure Service Bus, HTTP client, logging). Ask the user")

    # 6. God modules and other domains' code do not appear in the service.
    gods = [g for g in (god_modules or []) if g]
    for c in diff:
        if gods and c.status != "D" and c.path.endswith((".java", ".xml")):
            text = mg._read(service, c.path)
            hit = next((g for g in gods if g in text or re.search(rf"\b{re.escape(g.split('.')[-1])}\b", text)), None)
            if hit:
                problems.append(f"{c.path} uses the God module {hit}. The service must not depend on one")
    other_pkgs = [p for k, o in ownership.items() if k != domain for p in o.get("packages") or []]
    for c in diff:
        if other_pkgs and c.status != "D" and c.path.endswith(".java"):
            text = mg._read(service, c.path)
            hit = next((p for p in other_pkgs if re.search(rf"^\s*import\s+{re.escape(p)}\b", text, re.M)), None)
            if hit:
                problems.append(f"{c.path} imports {hit}, which belongs to another domain")

    # 7-9 need the declaration.
    if service_decl is None:
        return [*problems, "Write microservice-extraction.yaml (in the step's documents folder): the checks of the "
                           "operations, events and context adapters need it"][:mg.MAX_REPORTED]

    # 7. The service exposes exactly the approved operations.
    declared = {str(o.get("operation_id")) for o in service_decl.get("operations", [])}
    if openapi_text is None:
        problems.append("openapi.yaml of the domain design is missing, so the operations cannot be checked")
    else:
        ids, missing = openapi_operation_ids(openapi_text)
        problems += [f"openapi.yaml: {m} has no operationId; add one so the operation can be traced" for m in missing]
        ids = {i for i in ids if not i.lower().startswith("health")}
        declared_ids = {i for i in declared if not i.lower().startswith("health")}
        problems += [f"Operation {i} is in openapi.yaml but not in microservice-extraction.yaml" for i in sorted(ids - declared_ids)]
        problems += [f"Operation {i} is in microservice-extraction.yaml but not in openapi.yaml: no extra endpoints"
                     for i in sorted(declared_ids - ids)]
    for op in service_decl.get("operations", []):
        cls, _, method = str(op.get("method", "")).partition("#")
        hits = [f for module in modules for f in _files(monolith or service, module, "main") if f.name == f"{cls}.java"]
        if not hits or not any(re.search(rf"\b{re.escape(method)}\s*\(", f.read_text(encoding="utf-8", errors="replace"))
                               for f in hits):
            problems.append(f"Operation {op.get('operation_id')}: {op.get('method')} is not a method of the module")

    # 8. Events are the designed ones.
    extra = set(service_decl.get("events") or []) - set(module_decl.get("events") or [])
    problems += [f"Event {e} is published by the service but the approved designs do not name it" for e in sorted(extra)]

    # 9. Every context port has an adapter in the service.
    for port in (design or {}).get("context_ports", []):
        if not any(re.search(rf"\bimplements\b[^{{]*\b{re.escape(str(port))}\b", mg._read(service, c.path))
                   for c in diff if c.path.endswith(".java") and c.status != "D" and not mg._is_test(c.path)):
            problems.append(f"Context port {port} has no adapter in the service: add a class that implements it and "
                            "reads the value from the request")
    return problems[:mg.MAX_REPORTED]


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run, reading the declarations in the step's documents folder."""
    from app import contracts_store as cs

    if ctx.code_dir in (".", ""):
        return ["This step must change code in the service's own repository (the step's Changes code setting: In another "
                "repository), never in the monolith"]
    if not ctx.base_sha:
        return ["The service repository's starting commit is unknown, so the changes cannot be checked"]
    out = ctx.output_dir

    def load(name, parse):
        f = out / name
        return parse(f.read_text(encoding="utf-8")) if f.is_file() else None

    try:
        module_decl = load(cs.MODULE_EXTRACTION_FILE, cs.parse_module_extraction)
        service_decl = load(cs.MICROSERVICE_EXTRACTION_FILE, cs.parse_microservice_extraction)
        design = load(cs.DOMAIN_DESIGN_FILE, cs.parse_domain_design)
        finder = load(cs.DOMAIN_DEPENDENCIES_FILE, cs.load_domain_dependencies)
    except cs.InvalidDocument as e:
        return [str(e)]
    if module_decl is None:
        return [f"{cs.MODULE_EXTRACTION_FILE} of the Module in monolith step is missing: it says where the module is"]
    openapi = out / "openapi.yaml"
    root_is_git = mg.head(ctx.workspace) is not None
    return check((ctx.workspace / ctx.code_dir).resolve(), ctx.base_sha, ctx.workspace if root_is_git else None,
                 getattr(ctx, "monolith_sha", None), module_decl, service_decl, design,
                 openapi.read_text(encoding="utf-8") if openapi.is_file() else None, ctx.ownership, ctx.domain,
                 (finder or {}).get("god_modules"))


def report(problems: list[str]) -> str:
    if not problems:
        return "Service guardrails: PASS. The service is a lift of the module within the allowed changes."
    return ("Service guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
