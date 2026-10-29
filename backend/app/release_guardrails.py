"""Guardrails of the release steps, checked from git rather than trusted from the agent's word.

- `modernise_guardrails` (Modernisation, in the domain service's repository): the schema stays as it is (no DDL, no
  schema scripts, no Flyway or Liquibase, Hibernate never creates or updates tables), the service is Java 25 on Spring
  Boot 4, no secrets are committed, no test is disabled, and the monolith is untouched.
- `integration_guardrails` (Monolith integration, in the monolith's repository): no UI change, no DDL or schema
  script, SQL statements unchanged, only an HTTP client and the Azure Service Bus SDK added to the build (existing
  dependencies unchanged), the domain and orchestrator modules unchanged (the switch lives outside them), no secrets,
  and no test deleted or disabled. And the routing of `integration-routing.yaml`: the router and the toggle are in
  the change, the toggle defaults to the old flow, and the new code calls only the target service's API (the
  orchestrator service when the feature has an orchestrator: never the domain service directly).

`for_context()` runs the ones the agent must pass; each problem is one line.
"""

import re
from pathlib import Path

from app import module_guardrails as mg
from app import orchestrator_service_guardrails as osg
from app import service_guardrails as sg
from app import test_guardrails as tg
from app.guardrail_problems import blocker

SCHEMA_MANAGEMENT = re.compile(
    r"(?im)(?:ddl-auto|hbm2ddl\.auto)\s*[:=]\s*[\"']?(?!none\b|validate\b)\w+|generate-ddl\s*[:=]\s*true|"
    r"spring\.sql\.init\.mode\s*[:=]\s*always|\bsql:\s*\n\s*init:\s*\n\s*mode:\s*always")
SCHEMA_TOOLS = re.compile(r"flyway|liquibase", re.IGNORECASE)
# What the monolith may gain to call the services: an HTTP client and the Azure Service Bus SDK.
INTEGRATION_GROUPS = ("com.azure", "com.microsoft.azure", "org.apache.httpcomponents", "com.squareup.okhttp3",
                      "com.fasterxml.jackson")


def _monolith_touched(monolith: Path | None, monolith_base: str | None) -> list[str]:
    if monolith is None or not monolith_base:
        return []
    try:
        touched = mg.changes(monolith, monolith_base)
    except (RuntimeError, OSError):
        return []
    return [f"The monolith repository changed: {c.path}. This step changes only the service's repository"
            for c in touched[:5]]


def _secrets(root: Path, c: mg.Change) -> list[str]:
    if c.status == "D" or mg._is_test(c.path) or not c.path.endswith((".properties", ".yml", ".yaml", ".xml", ".java")):
        return []
    pattern = sg.SECRET_PROPERTY if c.path.endswith((".properties", ".yml", ".yaml")) else sg.SECRET_QUOTED
    hit = next((line for line in c.added if pattern.search(line)), None)
    return [f"Possible secret in {c.path}: `{hit.strip()[:60]}…`. Credentials come from configuration or the "
            "environment (a ${...} placeholder), never the repository"] if hit else []


def _ddl(c: mg.Change) -> list[str]:
    if c.status == "D" or mg._is_test(c.path):
        return []
    if c.status == "A" and Path(c.path).suffix.lower() in (".sql", ".ddl"):
        return [f"New SQL script {c.path}. The schema is fixed: no schema or data scripts"]
    if Path(c.path).suffix.lower() in (*mg.DDL_SUFFIXES, ".yml", ".yaml"):
        hit = next((line for line in c.added if mg.DDL.search(line)), None)
        if hit:
            return [f"DDL in {c.path}: `{hit.strip()[:100]}`. The schema is fixed"]
    return []


def modernise(service: Path, base: str, monolith: Path | None, monolith_base: str | None) -> list[str]:
    """The Modernisation step: same schema, the target stack, nothing secret, every test still running."""
    problems = _monolith_touched(monolith, monolith_base)
    try:
        diff = mg.changes(service, base)
    except (RuntimeError, OSError) as e:
        return [*problems, blocker(f"Could not compare with the starting commit: {e}")]
    for c in diff:
        if mg._under(c.path, [tg.ACCEPTANCE_DIR]):
            continue  # its own check: acceptance_suite_unchanged
        problems += _ddl(c) + _secrets(service, c)
        if c.status != "D" and c.path.endswith((".properties", ".yml", ".yaml")) and not mg._is_test(c.path):
            hit = SCHEMA_MANAGEMENT.search(mg._read(service, c.path))
            if hit:
                problems.append(f"{c.path}: `{hit.group(0).strip()[:60]}`. Hibernate never creates or changes tables: "
                                "spring.jpa.hibernate.ddl-auto is none or validate, and nothing initialises the schema")
        if Path(c.path).name == "pom.xml" and c.status != "D":
            for g, a, _v in mg._pom_deps(mg._read(service, c.path)):
                if SCHEMA_TOOLS.search(f"{g}:{a}"):
                    problems.append(f"{c.path}: dependency {g}:{a} manages the schema. The schema is fixed")
        if c.status != "D" and c.path.endswith(".java"):
            hit = next((line for line in c.added if tg.DISABLED.search(line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be disabled or ignored (`{hit.strip()[:60]}`)")
    root = service / "pom.xml"
    if root.is_file():
        text = root.read_text(encoding="utf-8", errors="replace")
        boot = osg.spring_boot_version(text)
        if not boot or not boot.startswith("4."):
            problems.append("pom.xml: the modernised service is on Spring Boot 4 (its parent or BOM, "
                            f"{'found ' + boot if boot else 'not found'})")
        levels = set(osg.JAVA_LEVEL.findall(text))
        if levels != {"25"}:
            problems.append(f"pom.xml: the Java level is 25 (found {', '.join(sorted(levels)) or 'none set'})")
    return problems[:mg.MAX_REPORTED]


def integration(monolith: Path, base: str, modules: list[str], routing: dict | None = None,
                domain: str | None = None) -> list[str]:
    """The Monolith integration step: the switch to the services, and nothing else in the monolith."""
    try:
        diff = mg.changes(monolith, base)
    except (RuntimeError, OSError) as e:
        return [blocker(f"Could not compare with the starting commit: {e}")]
    problems = []
    for c in diff:
        paths = {c.path, c.old_path or c.path}
        if any(Path(p).suffix.lower() in mg.UI_SUFFIXES and not mg._is_test(p) for p in paths):
            problems.append(f"UI file changed: {c.path}. The UI is fixed")
        inside = next((m for m in modules if any(mg._under(p, [m]) for p in paths)), None)
        if inside:
            problems.append(f"{c.path} is in the module {inside}, which this step does not change: the switch to the "
                            "service lives outside it")
        problems += _ddl(c) + _secrets(monolith, c)
        if mg._is_test(c.path) and c.status == "D":
            problems.append(f"Existing test deleted: {c.path}")
        if c.status != "D" and c.path.endswith(".java"):
            hit = next((line for line in c.added if tg.DISABLED.search(line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be disabled or ignored (`{hit.strip()[:60]}`)")
        if c.path.endswith(".xml") and c.status != "A":
            before = mg.statements(mg._show(monolith, base, c.old_path or c.path), strict=True)
            after = mg.statements(mg._read(monolith, c.path), strict=True) if c.status != "D" else []
            if before and sorted(before) != sorted(after):
                problems.append(f"SQL changed in {c.path}. The monolith's SQL stays as it is")
        if Path(c.path).name == "pom.xml":
            problems += _integration_pom(monolith, base, c)
    if routing is not None:
        problems += _routing(monolith, diff, routing, domain or routing["domain"])
    return problems[:mg.MAX_REPORTED]


def _routing(monolith: Path, diff: list[mg.Change], routing: dict, domain: str) -> list[str]:
    """The toggle is there and defaults to the old flow, and the new flow calls the target service's API only."""
    problems = []
    live = [c for c in diff if c.status != "D"]
    added = [(c.path, line) for c in live for line in c.added]
    prop = str(routing["toggle"]["property"])
    router = str(routing["router"]).strip("/")
    if not any(c.path == router for c in live):
        problems.append(f"The router {router} (integration-routing.yaml) is not part of this step's change: the "
                        "feature's router gets the service value")
    if not any(prop in line for _path, line in added):
        problems.append(f"The toggle {prop} is not read or configured anywhere in this step's change")
    default = re.compile(rf"^\s*{re.escape(prop)}\s*[:=]\s*[\"']?(\S+?)[\"']?\s*$")
    for path, line in added:
        m = default.match(line)
        if m and path.endswith((".properties", ".yml", ".yaml")) and m.group(1) != "legacy":
            problems.append(f"{path} sets {prop} to {m.group(1)}. It defaults to legacy (the as-is code)")
    domain_api = re.compile(rf"/internal/api/v1/{re.escape(domain)}/(?!orchestrator\b)")
    orchestrator_api = re.compile(rf"/internal/api/v1/{re.escape(domain)}/orchestrator\b")
    for path, line in added:
        if mg._is_test(path) or not path.endswith((".java", ".xml", ".properties", ".yml", ".yaml")):
            continue
        if routing["target"] == "orchestrator-service" and domain_api.search(line):
            problems.append(f"{path} calls the {domain} domain service directly (`{line.strip()[:80]}`). The feature has "
                            "an orchestrator: the monolith reaches it only through the orchestrator service")
        if routing["target"] == "domain-service" and orchestrator_api.search(line):
            problems.append(f"{path} calls an orchestrator service (`{line.strip()[:80]}`), but the feature has no "
                            "orchestrator: the monolith calls the domain service")
    return problems


def _integration_pom(root: Path, base: str, c: mg.Change) -> list[str]:
    if c.status in ("D", "R"):
        return [f"{c.path} was {'deleted' if c.status == 'D' else 'moved'}. Only an HTTP client and the Azure "
                "Service Bus SDK may be added"]
    old, new = mg._show(root, base, c.path) or "", mg._read(root, c.path)
    old_deps, new_deps = tg.DEPENDENCY.findall(old), tg.DEPENDENCY.findall(new)
    old_set, new_set = {tg._norm(b) for b in old_deps}, {tg._norm(b) for b in new_deps}
    problems = [f"{c.path}: dependency {tg._field(b, 'groupId')}:{tg._field(b, 'artifactId')} was removed or changed. "
                "The monolith's existing dependencies stay as they are" for b in old_deps if tg._norm(b) not in new_set]
    for b in new_deps:
        if tg._norm(b) in old_set or tg._field(b, "scope") == "test":
            continue
        if not tg._field(b, "groupId").startswith(INTEGRATION_GROUPS):
            problems.append(f"{c.path}: dependency {tg._field(b, 'groupId')}:{tg._field(b, 'artifactId')} is not an "
                            "HTTP client or the Azure Service Bus SDK. Ask the user before adding anything else")
    if tg._norm(tg.DEPENDENCY.sub("", old)) != tg._norm(tg.DEPENDENCY.sub("", new)):
        problems.append(f"{c.path}: more than dependencies changed (versions, plugins, Java level stay as they are)")
    return problems


def for_context(ctx, checks: list[str]) -> list[str]:
    """Run the release checks among `checks` for the agent's run."""
    from app import contracts_store as cs

    wanted = [c for c in checks if c in ("modernise_guardrails", "integration_guardrails")]
    if not wanted:
        return []
    if not ctx.base_sha:
        return [blocker("The repository's starting commit is unknown, so the changes cannot be checked: is it a git checkout?")]
    code = (ctx.workspace / ctx.code_dir).resolve()
    problems = []
    if "modernise_guardrails" in wanted:
        if ctx.code_dir in (".", ""):
            problems.append(blocker("This step must change code in the domain service's repository (the step's Changes code "
                            "setting: In another repository), never in the monolith"))
        else:
            from app import domain_modules
            monolith = ctx.workspace if mg.head(ctx.workspace) is not None else None
            problems += modernise(code, ctx.base_sha, monolith, getattr(ctx, "monolith_sha", None))
            problems += domain_modules.for_service(ctx, "service", None)  # the feature packages, kept
            problems += domain_modules.modern_problems(ctx)  # the lifted feature, converted; the lift untouched
    if "integration_guardrails" in wanted:
        target, why = cs.routing_target(ctx.output_dir)
        if target is None:  # known before the agent writes anything: which service it calls is the earlier steps'
            return [*problems, blocker(f"Monolith integration can't route this feature: {why}")]
        modules = []
        for name, parse, field in ((cs.MODULE_EXTRACTION_FILE, cs.parse_module_extraction, "modules"),
                                   (cs.ORCHESTRATOR_EXTRACTION_FILE, cs.parse_orchestrator_extraction, "module")):
            f = ctx.output_dir / name
            try:
                data = parse(f.read_text(encoding="utf-8")) if f.is_file() else None
            except cs.InvalidDocument:
                data = None
            if data and field == "modules":
                modules += [str(m["path"]).strip("/") for m in data["modules"]]
            elif data and data.get("orchestrator") == "module":
                modules.append(str(data["module"]["path"]).strip("/"))
        f = ctx.output_dir / cs.INTEGRATION_ROUTING_FILE
        try:
            routing = cs.parse_integration_routing(f.read_text(encoding="utf-8")) if f.is_file() else None
        except cs.InvalidDocument as e:
            return [*problems, str(e)]
        if routing is None:
            problems.append(f"Write {cs.INTEGRATION_ROUTING_FILE} (in {ctx.rel(ctx.output_dir)}/): which service the new "
                            "flow calls, the toggle and every operation it routes")
        problems += integration(code, ctx.base_sha, modules, routing, ctx.domain)
    return problems


def report(problems: list[str]) -> str:
    if not problems:
        return "Release guardrails: PASS. Nothing outside the allowed changes."
    return ("Release guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending change instead of working around it, or ask the user if the rule blocks you.")
