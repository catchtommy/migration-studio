"""Guardrails of the Orchestrator in monolith step, checked from git rather than trusted from the agent's word.

The step builds the orchestrator of one cross-domain feature as its own Maven module beside the domain module that
the Module in monolith step built, so that a request flows controller → orchestrator → domain module. Compared with
the commit the step started from, the limits of the module extraction apply (see module_guardrails.py: no UI, DDL or
version changes, only mechanical call-site and wiring edits outside the module, no other domain's code or tables, no
God module, no ambient inputs), and in addition:

- the orchestrator holds no database access (DAO, iBatis, JDBC, JPA, SQL maps) and no transaction of its own;
- the domain module does not change;
- the orchestrator module depends on the domain module;
- a feature declared without an orchestrator (`orchestrator: none`) changes no code at all.

`check()` returns one line per violation, empty when the step is within its limits.
"""

import re
from pathlib import Path

from app import module_guardrails as mg

DB_ACCESS = re.compile(r"\b(?:java\.sql|javax\.sql|DataSource|JdbcTemplate|NamedParameterJdbcTemplate|SimpleJdbc\w+|"
                       r"SqlMapClient\w*|SqlMapSession|SqlSession\w*|com\.ibatis|org\.apache\.ibatis|org\.mybatis|"
                       r"EntityManager\w*|javax\.persistence|org\.hibernate|\w*(?:Dao|DAO))\b")
TRANSACTION = re.compile(r"@Transactional\b|\b(?:TransactionTemplate|PlatformTransactionManager|TransactionStatus|"
                         r"TransactionSynchronization\w*)\b|org\.springframework\.transaction")
COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def check(root: Path, base: str, declaration: dict, module_decl: dict | None, design: dict | None,
          ownership: dict[str, dict], domain: str, god_modules: list[str] | None = None) -> list[str]:
    """Violations of the step's limits since `base`. `declaration` is orchestrator-extraction.yaml (parsed),
    `module_decl` module-extraction.yaml of the Module in monolith step and `design` domain-design.yaml."""
    try:
        diff = mg.changes(root, base)
    except (RuntimeError, OSError) as e:
        return [f"Could not compare with the starting commit: {e}"]
    if declaration.get("orchestrator") == "none":
        return [f"{c.path} changed, but orchestrator-extraction.yaml says there is no orchestrator: this step then "
                "changes no code" for c in diff[:mg.MAX_REPORTED]]
    if not diff:
        return []
    orchestrator = str(declaration["module"]["path"]).strip("/")
    domain_modules = [m for m in (module_decl or {}).get("modules", []) if m.get("kind") == "domain"]
    domain_paths = [str(m["path"]).strip("/") for m in domain_modules]

    # The module extraction's limits, with the orchestrator as the new module and the domain module as the one it uses.
    # Callers may swap the domain API classes the orchestrator takes over (`replaces`) for the orchestrator's.
    as_module = {"modules": [{"path": orchestrator, "kind": "orchestrator", "artifact": declaration["module"]["artifact"]}],
                 "bridge_dir": (module_decl or {}).get("bridge_dir") or "",
                 "outside_changes": declaration.get("outside_changes") or []}
    problems = [p.replace("module-extraction.yaml", "orchestrator-extraction.yaml")
                for p in mg.check(root, base, as_module, design, ownership, domain, god_modules,
                                  extra_artifacts=[m["artifact"] for m in domain_modules],
                                  extra_names=[str(op["replaces"]).split("#")[0].split(".")[-1]
                                               for op in declaration.get("operations") or []])]

    # The domain module stays as the Module in monolith step left it.
    for c in diff:
        if any(mg._under(path, domain_paths) for path in {c.path, c.old_path or c.path}):
            problems.append(f"The domain module changed: {c.path}. The orchestrator uses the domain's public API as it "
                            "is; if it needs more, ask the user")

    # No database access and no transactions in the orchestrator.
    for c in diff:
        if c.status == "D" or not mg._under(c.path, [orchestrator]) or mg._is_test(c.path):
            continue
        text = mg._read(root, c.path)
        if c.path.endswith(".java"):
            code = COMMENTS.sub("", text)
            hit = DB_ACCESS.search(code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0)}. The orchestrator holds no database access: data stays "
                                "in the domain module, reached through its public API")
            hit = TRANSACTION.search(code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0).strip()}. The orchestrator has no transaction of its own: "
                                "transactions stay in the domain module")
        elif c.path.endswith(".xml") and (mg.statements(text) or "<sqlMap" in text):
            problems.append(f"{c.path} is an SQL map. The orchestrator holds no SQL")

    # The orchestrator is built on the domain module.
    pom = root / orchestrator / "pom.xml"
    if pom.is_file() and domain_modules:
        deps = {f"{g}:{a}" for g, a, _v in mg._pom_deps(pom.read_text(encoding="utf-8", errors="replace"))}
        if not any(m["artifact"] in deps for m in domain_modules):
            problems.append(f"{orchestrator}/pom.xml does not depend on the domain module "
                            f"({', '.join(m['artifact'] for m in domain_modules)}); the orchestrator calls its public API")
    return problems[:mg.MAX_REPORTED]


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run, using the declaration and the designs in the step's documents."""
    from app import contracts_store as cs

    if not ctx.base_sha:
        return ["The step's starting commit is unknown, so the changes cannot be checked: is the code a git checkout?"]
    out = ctx.output_dir

    def load(name, parse):
        f = out / name
        return parse(f.read_text(encoding="utf-8")) if f.is_file() else None

    try:
        declaration = load(cs.ORCHESTRATOR_EXTRACTION_FILE, cs.parse_orchestrator_extraction)
        module_decl = load(cs.MODULE_EXTRACTION_FILE, cs.parse_module_extraction)
        design = load(cs.DOMAIN_DESIGN_FILE, cs.parse_domain_design)
        finder = load(cs.DOMAIN_DEPENDENCIES_FILE, cs.load_domain_dependencies)
    except cs.InvalidDocument as e:
        return [str(e)]
    if declaration is None:
        return [f"Write {cs.ORCHESTRATOR_EXTRACTION_FILE} (in {ctx.rel(out)}/) first: it declares the orchestrator "
                "module (or that there is none), which the check needs"]
    if module_decl is None and declaration.get("orchestrator") != "none":
        return [f"{cs.MODULE_EXTRACTION_FILE} of the Module in monolith step is missing: it says where the domain "
                "module is"]
    return check((ctx.workspace / ctx.code_dir).resolve(), ctx.base_sha, declaration, module_decl, design,
                 ctx.ownership, ctx.domain, (finder or {}).get("god_modules"))


def report(problems: list[str]) -> str:
    if not problems:
        return "Orchestrator guardrails: PASS. Nothing outside the allowed changes."
    return ("Orchestrator guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
