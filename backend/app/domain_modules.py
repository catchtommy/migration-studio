"""Each domain has one domain module and one orchestrator module in the monolith, which every feature extends.

The first feature of a domain creates them where the project's settings say (`domain_module_dir_pattern`,
`orchestrator_module_dir_pattern`); each later feature adds to them, reusing the classes, SQL statements, router and
ports earlier features copied, instead of copying them again. The domain's features share one branch
(app/delivery.py, one branch per domain), so a later feature's checkout has the modules as the earlier ones left them.

Inside a module, every feature has its own package, `<base_package>.<feature>` (the feature's key without dashes,
e.g. `com.acme.broker.domain.createbroker`); the domain's types several features use, owned by none (entities, DAOs,
DTOs), go in `<base_package>.shared`, and stateless helpers several features use (formatters, mappers, validators)
in `<base_package>.util`. Helpers of the monolith's utility jars are not copied: the module depends on the jar. Code one feature uses from another goes in that other feature's package, created even when
its migration hasn't started, so the module shows which feature uses which, and each package can later move to a
service as it is. `package_problems()` checks it.

`existing()` gathers what the domain's earlier runs declared (module-extraction.yaml, orchestrator-extraction.yaml)
and what the checkout holds; `brief()` tells the agents of the module and service steps.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from app import contracts_store as cs
from app import settings_store
from app.db.models import Artifact, Feature, Run, StageExecution
from app.db.session import session_scope
from app.tools import git_ops

STEPS = ("modular_monolith", "monolith_orchestrator", "microservice_scaffold", "orchestrator_service", "modernise",
         "modernise_and_test", "service_tests")
STATEMENT = re.compile(r"<\s*(?:select|insert|update|delete|statement|procedure)\b[^>]*\bid\s*=\s*\"([^\"]+)\"",
                       re.IGNORECASE)
MAX_LISTED = 80
SHARED = "shared"  # the package of the domain's types several features use and none owns (entities, DAOs, DTOs)
UTIL = "util"  # the package of stateless helpers several features use (formatters, mappers, validators)
COMMON = (SHARED, UTIL)  # packages of no single feature: using them is no feature-to-feature interaction
PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.MULTILINE)


def feature_segment(key: str) -> str:
    """A feature's package name: its key in lower case without dashes or other separators (create-broker →
    createbroker)."""
    seg = re.sub(r"[^a-z0-9]", "", key.lower()) or "feature"
    seg = seg if seg[0].isalpha() else f"f{seg}"
    return f"{seg}feature" if seg in COMMON else seg  # a feature called util or shared doesn't take those packages


def domain_features(project: str, domain: str) -> list[tuple[str, str]]:
    """(key, name) of the domain's listed features."""
    with session_scope() as s:
        return [(f.key, f.name) for f in s.scalars(select(Feature).where(Feature.project == project,
                                                                         Feature.domain == domain)
                                                    .order_by(Feature.key))]


@dataclass
class Module:
    kind: str  # domain | orchestrator
    path: str
    artifact: str | None = None
    features: list[str] = field(default_factory=list)  # the features that built it, oldest first
    bridge: str | None = None
    routes: list[dict] = field(default_factory=list)  # [{"bean", "interface", "property", "feature"}]
    operations: list[str] = field(default_factory=list)  # orchestrator: Class#method
    ports: list[str] = field(default_factory=list)  # orchestrator: port (contract, property)
    events: list[str] = field(default_factory=list)
    base_package: str | None = None  # the module's package; each feature's is <base_package>.<feature>
    uses: list[str] = field(default_factory=list)  # "f1 → f2": a feature using another one's package
    present: bool = False  # in this checkout
    classes: list[str] = field(default_factory=list)  # main Java classes in the checkout
    statements: list[str] = field(default_factory=list)  # SQL statement ids in the module's SQL maps


def posix(path) -> str:
    """A repository-relative path as git writes it (forward slashes, no leading or trailing one), whatever the
    machine: an agent on Windows may write `loan-domain\\src` in its declaration."""
    return str(path or "").replace("\\", "/").strip("/")


def pattern_path(project: str, kind: str, domain: str) -> str:
    key = "domain_module_dir_pattern" if kind == "domain" else "orchestrator_module_dir_pattern"
    pattern = settings_store.get_project(project).get(key) or settings_store.PROJECT_DEFAULTS[key]
    return posix(pattern.format(domain=domain))


def _declarations(project: str, domain: str, run_id: str | None, name: str) -> list[tuple[str, str]]:
    """(feature, text) of the latest declaration `name` of each other run of the domain, oldest run first."""
    with session_scope() as s:
        rows = s.execute(select(Run.id, Run.operation, Run.created_at, Artifact.content, Artifact.id)
                         .join(Artifact, Artifact.run_id == Run.id)
                         .where(Run.project == project, Run.domain == domain, Artifact.name == name,
                                *([Run.id != run_id] if run_id else []))
                         .order_by(Run.created_at, Artifact.id)).all()
    latest: dict[str, tuple[str, str]] = {}
    for rid, operation, _created, content, _id in rows:
        latest[rid] = (operation, content)  # later attempts replace earlier ones
    return list(latest.values())


def _scan(module: Module, ws: Path | None) -> None:
    root = (ws / module.path) if ws else None
    module.present = bool(root) and root.is_dir()
    if not module.present:
        return
    for f in sorted(root.rglob("*.java")):
        rel = f.relative_to(root).as_posix()
        if "/test/" in f"/{rel}" or "/target/" in f"/{rel}":
            continue
        module.classes.append(f.stem)
    for f in sorted(root.rglob("*.xml")):
        rel = f.relative_to(root).as_posix()
        if "/target/" in f"/{rel}" or f.name == "pom.xml":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        module.statements += [f"{f.name}:{sid}" for sid in STATEMENT.findall(text)]


def existing(project: str, domain: str, run_id: str | None = None, ws: Path | None = None) -> dict[str, Module]:
    """The domain's modules: where they are (declared by earlier runs, else where the settings put them), what the
    earlier features declared, and what this checkout (`ws`) holds."""
    out = {"domain": Module("domain", pattern_path(project, "domain", domain)),
           "orchestrator": Module("orchestrator", pattern_path(project, "orchestrator", domain))}
    declared_domain = False
    for feature, text in _declarations(project, domain, run_id, cs.MODULE_EXTRACTION_FILE):
        try:
            data = cs.parse_module_extraction(text)
        except cs.InvalidDocument:
            continue
        m = out["domain"]
        dm = next(x for x in data["modules"] if x["kind"] == "domain")
        if not declared_domain:  # the first feature that built it says where it is
            m.path, m.artifact, declared_domain = posix(dm["path"]), str(dm["artifact"]), True
        if feature not in m.features:
            m.features.append(feature)
        m.bridge = m.bridge or posix(data["bridge_dir"])
        routing = data["routing"]
        m.routes += [{"bean": r["bean"], "interface": r["interface"], "property": routing["property"],
                      "router": posix(routing["router"]), "feature": feature} for r in routing["routes"]]
        m.events += [e for e in data.get("events") or [] if e not in m.events]
        m.base_package = m.base_package or data.get("base_package")
        m.uses += [f"{feature} → {u}" for u in data.get("uses_features") or [] if f"{feature} → {u}" not in m.uses]
    declared_orchestrator = False
    for feature, text in _declarations(project, domain, run_id, cs.ORCHESTRATOR_EXTRACTION_FILE):
        try:
            data = cs.parse_orchestrator_extraction(text)
        except cs.InvalidDocument:
            continue
        if data["orchestrator"] != "module":
            continue
        m = out["orchestrator"]
        if not declared_orchestrator:
            m.path, m.artifact = posix(data["module"]["path"]), str(data["module"]["artifact"])
            declared_orchestrator = True
        if feature not in m.features:
            m.features.append(feature)
        m.operations += [f"{o['method']} ({feature})" for o in data.get("operations") or []]
        m.ports += [f"{p['port']} ({p['contract']}, {p['property']})" for p in data.get("ports") or []
                    if f"{p['port']} ({p['contract']}, {p['property']})" not in m.ports]
        m.base_package = m.base_package or data.get("base_package")
        m.uses += [f"{feature} → {u}" for u in data.get("uses_features") or [] if f"{feature} → {u}" not in m.uses]
    for m in out.values():
        _scan(m, ws)
    return out


def problems(project: str, domain: str, run_id: str | None, root: Path, kind: str, declared: str) -> list[str]:
    """The one-module-per-domain rules for a step declaring its `kind` module at `declared`: it is the domain's
    existing module (or, for the domain's first feature, where the settings put it), that module is in the checkout,
    and the earlier features' toggles are still routed."""
    from app.guardrail_problems import blocker

    m = existing(project, domain, run_id, root)[kind]
    declared = posix(declared)
    label = "domain module" if kind == "domain" else "orchestrator module"
    out = []
    if m.features:
        if declared != m.path:
            out.append(f"The domain already has its {label} at {m.path} (built by {', '.join(m.features)}): extend it "
                       f"instead of creating {declared}. A domain has one {label}, shared by its features")
        if not m.present:
            out.append(blocker(f"The domain's {label} {m.path} (built by {', '.join(m.features)}) is not in this "
                               "checkout: their work is not on the domain's branch yet. Stop and ask the user to "
                               "commit and push it first"))
    elif declared != m.path:
        setting = "domain_module_dir_pattern" if kind == "domain" else "orchestrator_module_dir_pattern"
        out.append(f"The domain's first {label} goes at {m.path} (Project settings, {setting}), not {declared}")
    for r in m.routes:  # earlier features keep their parallel run
        router = root / r["router"]
        if router.is_file() and r["property"] not in router.read_text(encoding="utf-8", errors="replace"):
            out.append(f"The router {r['router']} no longer reads {r['property']}, the toggle of {r['feature']}: keep "
                       "the earlier features' methods and toggles as they are")
    return out


MAIN_CLASS = re.compile(r"public\s+static\s+void\s+main\s*\(|@SpringBootApplication\b")
SERVICE_FILES = {"service": cs.MICROSERVICE_EXTRACTION_FILE, "orchestrator_service": cs.ORCHESTRATOR_SERVICE_FILE}


def layout_problems(root: Path, base: str, prefix: str, base_package: str, feature: str,
                    segments: dict[str, str], declared_uses: list | None, *, app_root_ok: bool = False) -> list[str]:
    """Every Java file this step added or changed under `prefix` (main and test) sits in `<base_package>.<a feature
    of the domain>`, `.shared` or `.util` (sub-packages too); the base package itself holds only the application's
    main class, where `app_root_ok`. With `declared_uses`, this feature's package may use another feature's only when
    that feature is listed there."""
    from app.module_guardrails import changes

    own = feature_segment(feature)
    prefix = posix(prefix)
    under = (lambda path: path.startswith(prefix + "/")) if prefix else (lambda path: True)  # noqa: E731
    out = []
    for c in changes(root, base):
        if c.status == "D" or not c.path.endswith(".java") or not under(c.path):
            continue
        text = (root / c.path).read_text(encoding="utf-8", errors="replace") if (root / c.path).is_file() else ""
        m = PACKAGE.search(text)
        pkg = m.group(1) if m else ""
        if pkg == base_package and app_root_ok and MAIN_CLASS.search(text):
            continue  # the application's main class: Spring scans the packages below it
        if pkg == base_package or not pkg.startswith(base_package + "."):
            out.append(f"{c.path} is in package {pkg or '(none)'}: put it in its feature's package "
                       f"{base_package}.<feature> (this feature's: {base_package}.{own}), {base_package}.{SHARED} for "
                       f"what several features use, or {base_package}.{UTIL} for stateless helpers"
                       + (" (only the main class sits in the base package)" if app_root_ok else ""))
            continue
        seg = pkg[len(base_package) + 1:].split(".")[0]
        if seg not in COMMON and seg not in segments:
            out.append(f"{c.path} is in package {pkg}, which is no feature of the domain ({', '.join(sorted(segments))}"
                       f") nor {SHARED} or {UTIL}. If it belongs to a feature not listed yet, stop and ask")
    if declared_uses is None:
        return out
    used = set()
    folder = root / prefix if prefix else root
    for f in folder.rglob("*.java") if folder.is_dir() else []:
        parts = f.relative_to(folder).parts
        if ".git" in parts or ".studio" in parts:  # inside this repository: a run's other checkouts live in .studio
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = PACKAGE.search(text)
        if not m or not m.group(1).startswith(f"{base_package}.") or \
                m.group(1)[len(base_package) + 1:].split(".")[0] != own:
            continue
        for imp in IMPORT.findall(text):
            if imp.startswith(base_package + "."):
                seg = imp[len(base_package) + 1:].split(".")[0]
                if seg != own and seg not in COMMON and seg in segments:
                    used.add(segments[seg])
    missing = sorted(used - {str(u) for u in declared_uses})
    if missing:
        out.append(f"This feature's package uses {', '.join(missing)}: list {'it' if len(missing) == 1 else 'them'} "
                   "in uses_features, so the features' interactions are known")
    return out


def _segments(project: str, domain: str, feature: str) -> dict[str, str]:
    segments = {feature_segment(k): k for k, _ in domain_features(project, domain)}
    segments.setdefault(feature_segment(feature), feature)
    return segments


def package_problems(project: str, domain: str, feature: str, run_id: str | None, root: Path, base: str,
                     kind: str, module_path: str, declaration: dict) -> list[str]:
    """One package per feature in the domain's `kind` module (layout_problems), with the base package earlier
    features declared."""
    base_package = str(declaration.get("base_package") or "").strip()
    if not base_package:
        return ["Add base_package to the declaration: the module's package, e.g. com.acme.broker.domain; every "
                "feature's classes go in <base_package>.<feature>, shared ones in <base_package>.shared"]
    earlier = existing(project, domain, run_id, root)[kind].base_package
    out = []
    if earlier and earlier != base_package:
        out.append(f"base_package is {base_package}, but the module's is {earlier} (earlier features): keep it")
        base_package = earlier
    return out + layout_problems(root, base, module_path, base_package, feature, _segments(project, domain, feature),
                                 declaration.get("uses_features") or [])


def services(project: str, domain: str, run_id: str | None) -> dict[str, dict]:
    """What earlier features declared about the domain's two services: {"service" | "orchestrator_service":
    {"base_package", "features", "uses"}}."""
    out = {}
    for kind, name in SERVICE_FILES.items():
        info = {"base_package": None, "features": [], "uses": [], "module_commit": None}
        for feature, text in _declarations(project, domain, run_id, name):
            try:
                data = (cs.parse_microservice_extraction if kind == "service" else cs.parse_orchestrator_service)(text)
            except cs.InvalidDocument:
                continue
            if data.get("orchestrator") == "none":
                continue
            info["base_package"] = info["base_package"] or data.get("base_package")
            info["module_commit"] = data.get("module_commit") or info["module_commit"]  # the latest lift's
            if feature not in info["features"]:
                info["features"].append(feature)
            info["uses"] += [f"{feature} → {u}" for u in data.get("uses_features") or []
                             if f"{feature} → {u}" not in info["uses"]]
        out[kind] = info
    return out


def service_problems(project: str, domain: str, feature: str, run_id: str | None, root: Path, base: str,
                     kind: str, declaration: dict | None, *, one_project: bool = True,
                     base_hint: str | None = None, uses: list | None = None) -> list[str]:
    """The domain's one `kind` service ("service" or "orchestrator_service") in the checked-out repository `root`:
    one project (no second POM), and one package per feature under the service's base package. `declaration` is the
    step's (Microservice, Orchestrator microservice); the steps after it (Modernise, Service tests) pass None and keep
    the service's base package (`base_hint`: this run's Microservice declaration's) and the features it declared this
    feature uses (`uses`): this feature's package may use another feature's only when that feature is listed."""
    from app.module_guardrails import changes

    earlier = services(project, domain, run_id)[kind]["base_package"]
    out = []
    base_package = str((declaration or {}).get("base_package") or "").strip() or None
    if declaration is not None and not base_package:
        out.append("Add base_package to the declaration: the service's package, e.g. com.acme.broker.service; every "
                   "feature's classes go in <base_package>.<feature>, shared ones in <base_package>.shared")
    if base_package and earlier and earlier != base_package:
        out.append(f"base_package is {base_package}, but the service's is {earlier} (earlier features): keep it")
    base_package = earlier or base_package or base_hint
    if one_project:
        out += [f"{c.path}: a second project in the service's repository. The domain has one project: add this "
                "feature to it, in its package" for c in changes(root, base)
                if c.status == "A" and Path(c.path).name == "pom.xml" and c.path != "pom.xml"]
    if base_package:
        out += layout_problems(root, base, "", base_package, feature, _segments(project, domain, feature),
                               uses if declaration is None else declaration.get("uses_features") or [],
                               app_root_ok=True)
    return out


def feature_of(run_id: str | None) -> str | None:
    if not run_id:
        return None
    with session_scope() as s:
        run = s.get(Run, run_id)
        return run.operation if run is not None else None


def project_of(run_id: str | None) -> str | None:
    if not run_id:
        return None
    try:
        with session_scope() as s:
            run = s.get(Run, run_id)
            return run.project if run is not None else None
    except Exception:  # noqa: BLE001 - without the database there are no earlier features to know
        return None


def for_service(ctx, kind: str, declaration: dict | None, *, one_project: bool = True) -> list[str]:
    """service_problems for an agent's run: its project, feature and checked-out service repository."""
    project = project_of(ctx.run_id)
    if project is None or not ctx.base_sha:
        return []
    hint, uses = None, None
    if declaration is None:  # Modernise, Service tests: the package and feature uses this run's Microservice declared
        f = ctx.output_dir / cs.MICROSERVICE_EXTRACTION_FILE
        text = f.read_text(encoding="utf-8") if f.is_file() else _own_document(ctx.run_id, cs.MICROSERVICE_EXTRACTION_FILE)
        try:
            lifted_decl = cs.parse_microservice_extraction(text) if text else {}
        except cs.InvalidDocument:
            lifted_decl = {}
        hint, uses = lifted_decl.get("base_package"), list(lifted_decl.get("uses_features") or [])
    return service_problems(project, ctx.domain, ctx.operation, ctx.run_id, (ctx.workspace / ctx.code_dir).resolve(),
                            ctx.base_sha, kind, declaration, one_project=one_project, base_hint=hint, uses=uses)


def missing_from_checkout(project: str, domain: str, run_id: str | None, ws: Path) -> list[Module]:
    """Modules earlier features declared that this checkout doesn't have: their work isn't on the branch yet."""
    return [m for m in existing(project, domain, run_id, ws).values() if m.features and not m.present]


def _listing(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")


# Two repositories per domain service: the lifted service (the module's stack, every feature, built and tested by the
# Microservice step; the acceptance suite's baseline is taken against it) and the modern service the Modernise step
# builds from it. Each domain sets both in its Agent inputs.
LIFT_AGENT = "microservice_scaffold"
REPOSITORY_HINTS = {
    "microservice_scaffold": "The lifted service: the module's stack, every feature. Not the modern service's repository.",
    "independent_tests": "The lifted service's repository (Spring Boot microservice's): the suite's baseline is taken "
                         "against it.",
    "modernise": "The modern service: its own repository, built from the lifted service (Spring Boot microservice's).",
    "modernise_and_test": "The modern service: its own repository, built from the lifted service (Spring Boot "
                          "microservice's).",
    "service_tests": "The modern service's repository (Modernise's).",
}
MODERN_AGENTS = ("modernise", "modernise_and_test", "service_tests")
SUITE = "acceptance-tests/"


def lifted(project: str, domain: str, run_id: str | None, ws: Path | None) -> dict | None:
    """The lift repository for this run: {"url", "key", "path" (its checkout, None when the run hasn't checked it out),
    "base" (where this run started there), "files" (what this run changed there, the acceptance suite aside)}.
    None when the domain sets no repository for the Microservice step."""
    from app import delivery
    from app.module_guardrails import changes

    url = (delivery.domain_inputs(project, domain).get(LIFT_AGENT) or {}).get("repository")
    if not url:
        return None
    entry = None
    if run_id:
        with session_scope() as s:
            run = s.get(Run, run_id)
            entry = next((dict(r) for r in (run.repositories or []) if run and delivery._same(r["url"], url)
                          and r.get("base_sha")), None) if run else None
    if entry is None or ws is None or not (ws / entry["path"] / ".git").exists():
        return {"url": url, "key": entry["key"] if entry else None, "path": None, "base": None, "files": []}
    recorded = _run_changes(run_id, entry["key"])
    if recorded is None:  # a run from before steps recorded their changes: what changed since it started there
        recorded = [c.path for c in changes(ws / entry["path"], entry["base_sha"]) if c.status != "D"]
    files = sorted(f for f in recorded if not f.startswith(SUITE))
    return {"url": url, "key": entry["key"], "path": entry["path"], "base": entry["base_sha"], "files": files}


DIFF_TARGET = re.compile(r"^\+\+\+ b/(.+?)\r?$", re.MULTILINE)  # CRLF too (git on Windows)


def _run_changes(run_id: str | None, key: str) -> list[str] | None:
    """The files this run's own steps changed (and kept) in its repository `key`, from each step's latest attempt's
    recorded change. Work other runs pushed to the branch and merged into this run's checkout is not in them. None when
    no step recorded a change there."""
    if not run_id:
        return None
    with session_scope() as s:
        rows = s.scalars(select(StageExecution).where(StageExecution.run_id == run_id)
                         .order_by(StageExecution.id)).all()
        latest = {}
        for ex in rows:  # each step's latest attempt that recorded this repository
            if ex.status != "failed" and key in (ex.repos or {}):
                latest[ex.stage] = (ex.repos or {})[key]
    diffs = [r.get("diff") for r in latest.values() if r.get("diff")]
    if not latest:
        return None
    return list(dict.fromkeys(f for d in diffs for f in DIFF_TARGET.findall(d)))


def _own_document(run_id: str, name: str) -> str | None:
    """This run's latest stored document `name` (the documents folder is only on disk while a step runs)."""
    with session_scope() as s:
        row = s.scalars(select(Artifact).where(Artifact.run_id == run_id, Artifact.name == name)
                        .order_by(Artifact.id.desc())).first()
        return row.content if row else None


def lift_brief(info: dict | None, feature: str, base_package: str | None = None) -> str:
    """For the Modernise step: where the lifted feature is, and exactly which of its files to convert (the wording is
    the shared text "lift_rules", editable on the Agents page; the run's data is filled in here)."""
    from app import shared_texts

    if info is None:
        return ""
    if info["path"] is None:
        return ("## The lifted feature\nThe domain's lifted service is " + f"`{info['url']}`" + ", but this run has not "
                "checked it out (the Spring Boot microservice step didn't run in it). Stop and ask (ask_user).")
    listing = "\n".join(f"- `{info['path']}/{f}`" for f in info["files"][:200]) or "- (no file changed)"
    return shared_texts.fill("lift_rules", {
        "lift_path": info["path"], "lift_url": info["url"], "feature": feature, "lift_commit": info["base"][:10],
        "lifted_files": listing,
        "base_package": f"`{base_package}`" if base_package else "the one in its microservice-extraction.yaml"})


def modern_problems(ctx) -> list[str]:
    """The Modernise step, two repositories: the lift is unchanged by this step, every package this run lifted has
    converted code in the modern service, and the modern service changes nothing outside this feature's packages,
    `shared`, `util` and those of `uses_features` (and the main class)."""
    from app.module_guardrails import changes

    project = project_of(ctx.run_id)
    if project is None or not ctx.base_sha:
        return []
    info = lifted(project, ctx.domain, ctx.run_id, ctx.workspace)
    if info is None or info["path"] is None or info["path"] == ctx.code_dir:
        return []  # one repository (or none): nothing to compare
    out = []
    with session_scope() as s:
        ex = s.scalars(select(StageExecution).where(StageExecution.run_id == ctx.run_id,
                                                    StageExecution.stage == ctx.stage)
                       .order_by(StageExecution.id.desc())).first()
        start = ((ex.repos or {}).get(info["key"]) or {}).get("start") if ex else None
    lift_root = ctx.workspace / info["path"]
    if start:
        touched = changes(lift_root, start)
        out += [f"{c.path}: the lifted service is read only for this step; convert into the modern service instead"
                for c in touched[:5]]
    decl_file = ctx.output_dir / cs.MICROSERVICE_EXTRACTION_FILE
    text = decl_file.read_text(encoding="utf-8") if decl_file.is_file() else _own_document(ctx.run_id,
                                                                                         cs.MICROSERVICE_EXTRACTION_FILE)
    try:
        decl = cs.parse_microservice_extraction(text) if text else {}
    except cs.InvalidDocument:
        decl = {}
    base_package = decl.get("base_package") or services(project, ctx.domain, ctx.run_id)["service"]["base_package"]
    if not base_package:
        return out
    allowed = {feature_segment(ctx.operation), *COMMON} | {feature_segment(u) for u in decl.get("uses_features") or []}

    def rel(root: Path, path: str) -> str | None:
        text = (root / path).read_text(encoding="utf-8", errors="replace") if (root / path).is_file() else ""
        m = PACKAGE.search(text)
        if not m or not m.group(1).startswith(base_package + "."):
            return None
        return m.group(1)[len(base_package) + 1:]

    lifted_pkgs = {r for f in info["files"] if f.endswith(".java") for r in [rel(lift_root, f)] if r}
    modern_root = (ctx.workspace / ctx.code_dir).resolve()
    every = changes(modern_root, ctx.base_sha)
    for c in every:  # a class of another feature deleted or moved away: that feature loses its code
        if c.status == "D" and c.path.endswith(".java"):
            try:
                old = git_ops.git(modern_root, "show", f"{ctx.base_sha}:{c.path}")
            except git_ops.GitError:
                continue
            m = PACKAGE.search(old)
            seg = m.group(1)[len(base_package) + 1:].split(".")[0] if m and m.group(1).startswith(base_package + ".") \
                else None
            if seg and seg not in allowed:
                out.append(f"{c.path} of another feature ({seg}) was deleted or moved: keep every feature's code in "
                           "its own package")
    modern = [c for c in every if c.status != "D" and c.path.endswith(".java")]
    modern_pkgs = set()
    for c in modern:
        r = rel(modern_root, c.path)
        if r is None:
            continue
        modern_pkgs.add(r)
        if r.split(".")[0] not in allowed:
            out.append(f"{c.path} is in another feature's package ({r.split('.')[0]}): convert only this feature's "
                       f"code ({', '.join(sorted(allowed))})")
    out += _unmapped_operations(ctx, modern_root)
    # Sub-packages may be renamed by the new stack (e.g. dao → repository): what must exist is the converted code under
    # each lifted package's feature, shared or util package.
    tops = {p.split(".")[0] for p in modern_pkgs}
    out += [f"Nothing converted for {p} (lifted this run): convert it into the modern service"
            for p in sorted({p.split(".")[0] for p in lifted_pkgs} - tops)]
    return out


MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b\s*(?:\(((?:[^()]|\([^()]*\))*)\))?")
VERB = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)")
TYPE = re.compile(r"\b(?:class|interface|enum|record)\s+(\w+)")
CONSTANT = re.compile(r"\bString\s+(\w+)\s*=\s*([^;]+);")
PATH_ATTR = re.compile(r"\b(?:value|path)\s*=\s*(\{[^}]*\}|[^,]+)")
# Where a service's controllers can be: its own sources, and API interfaces generated from its OpenAPI document
SOURCE_DIRS = ("src/main/", "target/generated-sources/", "build/generated/")


def _norm(path: str) -> str:
    path = re.sub(r"/+", "/", path).strip("/")
    return re.sub(r"\{[^}]*\}", "{}", "/" + path) if path else "/"


def _value(expr: str, constants: dict[str, str], depth: int = 0) -> str | None:
    """A Java string expression of literals and constants joined by +, or None when it can't be resolved."""
    out = ""
    for part in re.split(r"\+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", expr):
        part = part.strip()
        if part.startswith('"') and part.endswith('"') and len(part) >= 2:
            out += part[1:-1]
        elif re.fullmatch(r"[\w.]+", part) and depth < 5:
            ref = constants.get(part) or constants.get(part.rsplit(".", 1)[-1])
            resolved = _value(ref, constants, depth + 1) if ref is not None else None
            if resolved is None:
                return None
            out += resolved
        else:
            return None
    return out


def _paths(args: str, constants: dict[str, str]) -> list[str | None]:
    """The paths an annotation's arguments give: its value or path attribute (or its only argument); None for one
    that can't be resolved."""
    if not args.strip():
        return [""]
    m = PATH_ATTR.search(args)
    if m:
        expr = m.group(1)
    elif "=" in args:
        return [""]  # attributes, but no value or path (e.g. only produces): the class or method path is ""
    else:
        expr = args
    expr = expr.strip()
    items = [i for i in expr[1:-1].split(",") if i.strip()] if expr.startswith("{") else [expr]
    return [_value(i, constants) for i in items] or [""]


def controller_mappings(root: Path) -> set[tuple[str | None, str | None]]:
    """(verb, path) of every Spring MVC mapping in the service under `root`: its sources and generated API interfaces;
    paths given as constants are resolved from the service's String constants. A verb None: any method; a path None:
    one that couldn't be resolved (it is assumed to serve the operation rather than reporting it missing)."""
    files = [f for f in root.rglob("*.java") if (rel := f.relative_to(root).as_posix())
             and any(d in rel for d in SOURCE_DIRS) and not rel.startswith((".studio/", ".git/"))]
    texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    constants: dict[str, str] = {}
    for f, text in texts.items():
        owner = (TYPE.findall(text) or [f.stem])[0]
        for name, expr in CONSTANT.findall(text):
            constants.setdefault(name, expr.strip())
            constants[f"{owner}.{name}"] = expr.strip()
    out = set()
    for text in texts.values():
        m = TYPE.search(text)
        if not m:
            continue
        head, body = text[:m.start()], text[m.end():]
        prefixes = [""]
        for kind, args in MAPPING.findall(head):
            if kind == "Request":
                prefixes = _paths(args or "", constants)
        for kind, args in MAPPING.findall(body):
            verbs = [kind.upper()] if kind != "Request" else (VERB.findall(args or "") or [None])
            for prefix in prefixes:
                for path in _paths(args or "", constants):
                    full = None if prefix is None or path is None else _norm(f"{prefix}/{path}")
                    out |= {(v, full) for v in verbs}
    return out


def _unmapped_operations(ctx, modern_root: Path) -> list[str]:
    """Every operation of this feature's OpenAPI document is served by a controller of the modern service (a mapping
    for its method and path; a prefix such as the servlet's context path is allowed either side)."""
    from app.service_guardrails import openapi_paths

    f = ctx.output_dir / "openapi.yaml"
    text = f.read_text(encoding="utf-8") if f.is_file() else _own_document(ctx.run_id, "openapi.yaml")
    if not text:
        return []
    mapped = controller_mappings(modern_root)
    out = []
    for verb, path, op_id in openapi_paths(text):
        want = _norm(path)
        if not any((v is None or v == verb) and (p is None or p == want or p.endswith(want)
                                                  or (want.endswith(p) and p != "/"))
                   for v, p in mapped):
            out.append(f"No controller of the modern service maps {verb} {path} ({op_id or 'no operationId'}): convert "
                       "the lifted feature's REST layer too")
    return out


def brief(project: str, domain: str, run_id: str | None, ws: Path | None, stage: str | None = None) -> str:
    """What the module and service steps must know: the domain's modules and services, and that they extend them. The
    wording is the shared text "domain_architecture" (and "lift_rules" for Modernise), editable on the Agents page;
    the data (the domain's packages, modules and services as they are) is filled in here."""
    from app import shared_texts

    mods = existing(project, domain, run_id, ws)
    features = domain_features(project, domain)
    modules: list[str] = []
    for m in mods.values():
        label = "Domain module" if m.kind == "domain" else "Orchestrator module"
        if not m.features:
            modules.append(f"- {label}: none yet. The first feature creates it at `{m.path}/`.")
            continue
        modules.append(f"- {label}: `{m.path}/`" + (f" ({m.artifact})" if m.artifact else "")
                       + f", built by {', '.join(m.features)}.")
        if not m.present:
            modules.append(f"  NOT in this checkout: the earlier features' work isn't on this branch yet. Stop and ask "
                           f"(ask_user) to commit and push {', '.join(m.features)} to the domain's branch first.")
        if m.base_package:
            modules.append(f"  Base package: `{m.base_package}` (keep it).")
        if m.uses:
            modules.append(f"  Feature interactions so far: {', '.join(m.uses)}.")
        if m.bridge:
            modules.append(f"  Bridge package: `{m.bridge}/`.")
        for r in m.routes:
            modules.append(f"  Router route: bean `{r['bean']}` ({r['interface']}), toggle `{r['property']}` "
                           f"({r['feature']}).")
        if m.operations:
            modules.append(f"  Operations: {_listing(m.operations)}.")
        if m.ports:
            modules.append(f"  Ports with mode switches: {_listing(m.ports)}.")
        if m.events:
            modules.append(f"  Events: {', '.join(m.events)}.")
        if m.classes:
            modules.append(f"  Classes already there (reuse them): {_listing(m.classes)}.")
        if m.statements:
            modules.append(f"  SQL statements already there (reuse them): {_listing(m.statements)}.")
    repositories = {}
    try:
        from app import delivery
        inputs = delivery.domain_inputs(project, domain)
        modern = next((k for k in ("modernise", "modernise_and_test", "service_tests")
                       if (inputs.get(k) or {}).get("repository")), "modernise")
        repositories = {k: (inputs.get(k) or {}).get("repository") for k in (LIFT_AGENT, "orchestrator_service", modern)}
        repositories["modern"] = repositories.get(modern)
    except Exception:  # noqa: BLE001 - the brief goes on without them
        pass
    service_lines = []
    for kind, label, repo in (("service", "Lifted service (the module's stack; Spring Boot microservice, Independent "
                                          "tests)", LIFT_AGENT),
                              ("service", "Modern service (Modernise, Service tests; the one the monolith calls)",
                               "modern"),
                              ("orchestrator_service", "Orchestrator service", "orchestrator_service")):
        info = services(project, domain, run_id)[kind]
        where = repositories.get(repo)
        service_lines.append(f"- {label}: " + (f"`{where}`" if where else "repository not set in the domain's Agent "
                                                                          "inputs yet")
                             + (f", built by {', '.join(info['features'])}" if info["features"] else ", no feature yet")
                             + (f"; base package `{info['base_package']}` (keep it)" if info["base_package"] else "")
                             + (f"; interactions so far: {', '.join(info['uses'])}" if info["uses"] else "") + ".")
    text = shared_texts.fill("domain_architecture", {
        "domain": domain,
        "feature_packages": (", ".join(f"{k} → {feature_segment(k)}" for k, _ in features)
                             or "no features listed for the domain yet") + f"; shared → {SHARED}; util → {UTIL}",
        "modules": "\n".join(modules), "services": "\n".join(service_lines)})
    if stage in MODERN_AGENTS and stage != "service_tests":
        own = _own_document(run_id, cs.MICROSERVICE_EXTRACTION_FILE) if run_id else None
        try:
            base = cs.parse_microservice_extraction(own).get("base_package") if own else None
        except cs.InvalidDocument:
            base = None
        lift = lift_brief(lifted(project, domain, run_id, ws), feature_of(run_id) or "this feature",
                          base or services(project, domain, run_id)["service"]["base_package"])
        if lift:
            text += "\n\n" + lift
    return text
