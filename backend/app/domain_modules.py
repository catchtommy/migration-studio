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
from app.db.models import Artifact, Feature, Run
from app.db.session import session_scope

STEPS = ("modular_monolith", "monolith_orchestrator", "microservice_scaffold", "orchestrator_service")
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


def pattern_path(project: str, kind: str, domain: str) -> str:
    key = "domain_module_dir_pattern" if kind == "domain" else "orchestrator_module_dir_pattern"
    pattern = settings_store.get_project(project).get(key) or settings_store.PROJECT_DEFAULTS[key]
    return pattern.format(domain=domain).strip("/")


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
            m.path, m.artifact, declared_domain = str(dm["path"]).strip("/"), str(dm["artifact"]), True
        if feature not in m.features:
            m.features.append(feature)
        m.bridge = m.bridge or str(data["bridge_dir"]).strip("/")
        routing = data["routing"]
        m.routes += [{"bean": r["bean"], "interface": r["interface"], "property": routing["property"],
                      "router": str(routing["router"]).strip("/"), "feature": feature} for r in routing["routes"]]
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
            m.path, m.artifact = str(data["module"]["path"]).strip("/"), str(data["module"]["artifact"])
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
    declared = declared.strip("/")
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


def package_problems(project: str, domain: str, feature: str, run_id: str | None, root: Path, base: str,
                     kind: str, module_path: str, declaration: dict) -> list[str]:
    """One package per feature in the `kind` module: every Java file this step added or changed sits in
    `<base_package>.<a feature of the domain>` or `<base_package>.shared`, the base package is the one earlier
    features declared, and the features this feature's package uses are listed in `uses_features`."""
    from app.module_guardrails import changes

    base_package = str(declaration.get("base_package") or "").strip()
    if not base_package:
        return ["Add base_package to the declaration: the module's package, e.g. com.acme.broker.domain; every "
                "feature's classes go in <base_package>.<feature>, shared ones in <base_package>.shared"]
    earlier = existing(project, domain, run_id, root)[kind].base_package
    out = []
    if earlier and earlier != base_package:
        out.append(f"base_package is {base_package}, but the module's is {earlier} (earlier features): keep it")
        base_package = earlier
    segments = {feature_segment(k): k for k, _ in domain_features(project, domain)}
    segments.setdefault(feature_segment(feature), feature)
    own = feature_segment(feature)
    module_path = module_path.strip("/")
    for c in changes(root, base):
        if c.status == "D" or not c.path.endswith(".java") or not c.path.startswith(module_path + "/"):
            continue
        text = (root / c.path).read_text(encoding="utf-8", errors="replace") if (root / c.path).is_file() else ""
        m = PACKAGE.search(text)
        pkg = m.group(1) if m else ""
        if pkg == base_package or not pkg.startswith(base_package + "."):
            out.append(f"{c.path} is in package {pkg or '(none)'}: put it in its feature's package "
                       f"{base_package}.<feature> (this feature's: {base_package}.{own}), {base_package}.{SHARED} for "
                       f"the domain's types several features use, or {base_package}.{UTIL} for stateless helpers")
            continue
        seg = pkg[len(base_package) + 1:].split(".")[0]
        if seg not in COMMON and seg not in segments:
            out.append(f"{c.path} is in package {pkg}, which is no feature of the domain ({', '.join(sorted(segments))}"
                       f") nor {SHARED} or {UTIL}. If it belongs to a feature not listed yet, stop and ask")
    used = set()
    pkg_dir = root / module_path
    for f in pkg_dir.rglob("*.java") if pkg_dir.is_dir() else []:
        text = f.read_text(encoding="utf-8", errors="replace")
        m = PACKAGE.search(text)
        if not m or not m.group(1).startswith(f"{base_package}.{own}") or \
                m.group(1)[len(base_package) + 1:].split(".")[0] != own:
            continue
        for imp in IMPORT.findall(text):
            if imp.startswith(base_package + "."):
                seg = imp[len(base_package) + 1:].split(".")[0]
                if seg != own and seg not in COMMON and seg in segments:
                    used.add(segments[seg])
    declared = {str(u) for u in declaration.get("uses_features") or []}
    missing = sorted(used - declared)
    if missing:
        out.append(f"This feature's package uses {', '.join(missing)}: list {'it' if len(missing) == 1 else 'them'} "
                   "in uses_features, so the features' interactions are known")
    return out


def missing_from_checkout(project: str, domain: str, run_id: str | None, ws: Path) -> list[Module]:
    """Modules earlier features declared that this checkout doesn't have: their work isn't on the branch yet."""
    return [m for m in existing(project, domain, run_id, ws).values() if m.features and not m.present]


def _listing(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")


def brief(project: str, domain: str, run_id: str | None, ws: Path | None) -> str:
    """What the module and service steps must know: the domain's modules, and that they extend them."""
    mods = existing(project, domain, run_id, ws)
    features = domain_features(project, domain)
    lines = ["## This domain's modules",
             f"Domain `{domain}` has ONE domain module and ONE orchestrator module in the monolith, shared by all its "
             "features: each feature extends them. Never create a second one, and never copy again what is already "
             "in them: reuse it.",
             "",
             "**One package per feature.** Inside each module, every class goes in the package of the feature it "
             "belongs to, `<base_package>.<feature package>` (sub-packages below it are fine), never directly in "
             f"`<base_package>`. The domain's types several features use and none owns (entities, DAOs, DTOs) go in "
             f"`<base_package>.{SHARED}`; stateless helpers several features use (formatters, mappers, validators: no "
             f"business rules, no database access, no state) go in `<base_package>.{UTIL}`. Helpers of the monolith's "
             "utility jars (`shared_code`, `utility_modules`) are not copied: the module depends on the jar. Code "
             "this feature uses from another feature of the domain goes in THAT feature's package, which you create "
             "if it doesn't exist yet, even when that feature's migration hasn't started; list those features in "
             "`uses_features`. Each feature's SQL statements go in a SQL map beside its package. Declare the module's "
             "`base_package` (the same one every feature of the domain uses).",
             "Feature packages (key → package): " + (", ".join(f"{k} → {feature_segment(k)}" for k, _ in features)
                                                     or "no features listed for the domain yet")
             + f"; shared → {SHARED}; util → {UTIL}."]
    for m in mods.values():
        label = "Domain module" if m.kind == "domain" else "Orchestrator module"
        if not m.features:
            lines.append(f"- {label}: none yet. The first feature creates it at `{m.path}/`.")
            continue
        lines.append(f"- {label}: `{m.path}/`" + (f" ({m.artifact})" if m.artifact else "")
                     + f", built by {', '.join(m.features)}.")
        if not m.present:
            lines.append(f"  NOT in this checkout: the earlier features' work isn't on this branch yet. Stop and ask "
                         f"(ask_user) to commit and push {', '.join(m.features)} to the domain's branch first.")
        if m.base_package:
            lines.append(f"  Base package: `{m.base_package}` (keep it).")
        if m.uses:
            lines.append(f"  Feature interactions so far: {', '.join(m.uses)}.")
        if m.bridge:
            lines.append(f"  Bridge package: `{m.bridge}/`.")
        for r in m.routes:
            lines.append(f"  Router route: bean `{r['bean']}` ({r['interface']}), toggle `{r['property']}` "
                         f"({r['feature']}).")
        if m.operations:
            lines.append(f"  Operations: {_listing(m.operations)}.")
        if m.ports:
            lines.append(f"  Ports with mode switches: {_listing(m.ports)}.")
        if m.events:
            lines.append(f"  Events: {', '.join(m.events)}.")
        if m.classes:
            lines.append(f"  Classes already there (reuse them): {_listing(m.classes)}.")
        if m.statements:
            lines.append(f"  SQL statements already there (reuse them): {_listing(m.statements)}.")
    return "\n".join(lines)
