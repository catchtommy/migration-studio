"""Guardrails of the Orchestrator in monolith step, checked from git rather than trusted from the agent's word.

The step builds the orchestrator of one cross-domain feature as its own Maven module beside the domain module that
the Module in monolith step built; the feature's router (in the bridge package) then sends its `monolith` value to the
orchestrator, so a request flows controller → router → orchestrator → domain module, while `legacy` still runs the
as-is code. Compared with the commit the step started from, the limits of the module extraction apply (see
module_guardrails.py: the as-is code unchanged, controllers too but for the toggle branch of one listed in
`toggle_branches` (a controller that sequences the domains' calls itself, so no bean seam fits); only Spring wiring
and POM lines outside the module and the bridge; in the bridge, the router, adapters and their helpers (mappers); no
UI, DDL or version changes, no other domain's code or tables, no God module, no ambient inputs), and in addition:

- the orchestrator holds no database access (DAO, iBatis, JDBC, JPA, SQL maps) and no transaction of its own;
- the orchestrator module is a new folder (or this domain's own, from an earlier feature);
- the domain module does not change;
- the orchestrator module depends on the domain module;
- a feature declared without an orchestrator (`orchestrator: none`) changes no code at all.

`check()` returns one line per violation, empty when the step is within its limits.
"""

from pathlib import Path

from app import module_guardrails as mg
from app.guardrail_problems import Blocker, blocker

DB_ACCESS, TRANSACTION, COMMENTS = mg.DB_ACCESS, mg.TRANSACTION, mg.COMMENTS


def check(root: Path, base: str, declaration: dict, module_decl: dict | None, design: dict | None,
          ownership: dict[str, dict], domain: str, god_modules: list[str] | None = None,
          earlier: set[str] | None = None) -> list[str]:
    """Violations of the step's limits since `base`. `declaration` is orchestrator-extraction.yaml (parsed),
    `module_decl` module-extraction.yaml of the Module in monolith step and `design` domain-design.yaml."""
    try:
        diff = mg.changes(root, base)
    except (RuntimeError, OSError) as e:
        return [blocker(f"Could not compare with the starting commit: {e}")]
    if declaration.get("orchestrator") == "none":
        return [f"{c.path} changed, but orchestrator-extraction.yaml says there is no orchestrator: this step then "
                "changes no code" for c in diff[:mg.MAX_REPORTED]]
    if not diff:
        return []
    orchestrator = str(declaration["module"]["path"]).strip("/")
    domain_modules = [m for m in (module_decl or {}).get("modules", []) if m.get("kind") == "domain"]
    domain_paths = [str(m["path"]).strip("/") for m in domain_modules]

    # The module extraction's limits, with the orchestrator as the new module and the domain module as the one it uses:
    # the as-is code unchanged (controllers too, but for a toggle branch); the router in the bridge now calls the orchestrator.
    routing = (module_decl or {}).get("routing") or {}
    as_module = {"modules": [{"path": orchestrator, "kind": "orchestrator", "artifact": declaration["module"]["artifact"]}],
                 "bridge_dir": (module_decl or {}).get("bridge_dir") or "",
                 "router": routing.get("router") or declaration.get("router") or "", "routes": routing.get("routes") or [],
                 "toggle_branches": declaration.get("toggle_branches") or [],
                 "outside_changes": declaration.get("outside_changes") or []}
    problems = [(blocker if isinstance(p, Blocker) else str)(p.replace("module-extraction.yaml", "orchestrator-extraction.yaml"))
                for p in mg.check(root, base, as_module, design, ownership, domain, god_modules,
                                  extra_artifacts=[m["artifact"] for m in domain_modules],
                                  wiring=_classes(root, domain_paths) | _route_beans(module_decl))]
    problems += mg._own_folders(root, base, diff, [orchestrator], [], {"modules": set(earlier or ())})

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


def _classes(root: Path, folders: list[str]) -> set[str]:
    return {f.stem for d in folders if (root / d).is_dir() for f in (root / d).rglob("*.java")}


def _route_beans(module_decl: dict | None) -> set[str]:
    routes = ((module_decl or {}).get("routing") or {}).get("routes") or []
    return {str(r[k]) for r in routes for k in ("bean", "legacy_bean") if r.get(k)}


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run, using the declaration and the designs in the step's documents."""
    from app import contracts_store as cs

    if not ctx.base_sha:
        return [blocker("The step's starting commit is unknown, so the changes cannot be checked: is the code a git checkout?")]
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
        return [blocker(f"{cs.MODULE_EXTRACTION_FILE} of the Module in monolith step is missing: it says where the domain "
                "module is")]
    return check((ctx.workspace / ctx.code_dir).resolve(), ctx.base_sha, declaration, module_decl, design,
                 ctx.ownership, ctx.domain, (finder or {}).get("god_modules"), earlier=_earlier(ctx.run_id, ctx.domain))


def _earlier(run_id: str, domain: str) -> set[str]:
    """The orchestrator module folders runs of the domain declared: extended, not created again."""
    from app import contracts_store as cs

    out = set()
    for text in mg.declarations(run_id, domain, cs.ORCHESTRATOR_EXTRACTION_FILE):
        try:
            data = cs.parse_orchestrator_extraction(text)
        except cs.InvalidDocument:
            continue
        if data["orchestrator"] == "module":
            out.add(str(data["module"]["path"]).strip("/"))
    return out


def report(problems: list[str]) -> str:
    if not problems:
        return "Orchestrator guardrails: PASS. Nothing outside the allowed changes."
    return ("Orchestrator guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
