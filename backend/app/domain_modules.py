"""Each domain has one domain module and one orchestrator module in the monolith (two Maven modules), one lifted and
one modern service and one orchestrator service, which every feature of the domain extends.

Every feature runs on its own: on its own branch cut from the base branch, knowing only what its checkout holds (the
base branch with its own work) and the domain's feature list, never what another feature's run does. So the names
can't come from other features: they come from the project's settings alone (names(), base_package()), the same for
every feature, so the features' branches meet in one module (or service) when they are merged into the base branch:
- the module folders: `domain_module_dir_pattern`, `orchestrator_module_dir_pattern`; their artifacts
  `<base_package_prefix>:<folder name>`;
- the packages: `<prefix>.<domain>` for the domain module and the services, `<prefix>.orchestrator.<domain>` for the
  orchestrator module and service.
When a module isn't in a feature's checkout (no feature merged it yet), the feature creates it there with these names,
holding only what it needs.

Inside each, every feature has its own package, `<base_package>.<feature>` (the feature's key without dashes, e.g.
`com.acme.broker.createbroker`); the domain's types several features use, owned by none (entities, DAOs, DTOs), go in
`<base_package>.shared`, and stateless helpers several features use (formatters, mappers, validators) in
`<base_package>.util`. Helpers of the monolith's utility jars are not copied: the module depends on the jar. Code one
feature uses from another goes in that other feature's package, on this feature's branch, created even when that
feature's migration hasn't started (or is in progress on its own branch), so the module shows which feature uses
which, and each package can later move to a service as it is. `package_problems()` checks it.
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
# The design steps get the same brief: they design the feature into the domain's one module, in its own package.
DESIGN_STEPS = ("domain_design", "modulith_design", "orchestrator_design")
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
    artifact: str | None = None  # groupId:artifactId (None while the project has no base package prefix)
    base_package: str | None = None  # the module's package; each feature's is <base_package>.<feature>
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


def domain_segment(domain: str) -> str:
    """A domain's package name: its key in lower case without dashes or other separators (customer-comm →
    customercomm)."""
    seg = re.sub(r"[^a-z0-9]", "", domain.lower()) or "domain"
    return seg if seg[0].isalpha() else f"d{seg}"


def prefix(project: str) -> str | None:
    """The project's base package prefix (com.acme), None while it isn't set."""
    return str(settings_store.get_project(project).get("base_package_prefix") or "").strip() or None


PREFIX_MISSING = ("The project's base package prefix isn't set (Project → Settings → Base package prefix, e.g. "
                  "com.acme), so the domain's packages and artifacts can't be named")


def base_package(project: str, domain: str, kind: str) -> str | None:
    """The package of the domain's `kind` code: the domain module and the services' (kind "domain" or "service")
    `<prefix>.<domain>`, the orchestrator module's and service's (kind "orchestrator" or "orchestrator_service")
    `<prefix>.orchestrator.<domain>`. Each feature's classes go in `<base_package>.<feature>`."""
    p = prefix(project)
    if p is None:
        return None
    return f"{p}.orchestrator.{domain_segment(domain)}" if kind.startswith("orchestrator") else \
        f"{p}.{domain_segment(domain)}"


def names(project: str, domain: str) -> dict[str, Module]:
    """The domain's two modules in the monolith, named from the project's settings alone (never from what another
    feature's run did): the folder (`domain_module_dir_pattern`, `orchestrator_module_dir_pattern`), the artifact
    `<prefix>:<folder name>` and the base package (base_package). Every feature works them out the same way, so their
    branches meet in one module per kind when they are merged."""
    p = prefix(project)
    out = {}
    for kind in ("domain", "orchestrator"):
        path = pattern_path(project, kind, domain)
        out[kind] = Module(kind, path, artifact=f"{p}:{path.split('/')[-1]}" if p else None,
                           base_package=base_package(project, domain, kind))
    return out


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


def existing(project: str, domain: str, ws: Path | None = None) -> dict[str, Module]:
    """The domain's modules (names()) and what this checkout (`ws`) holds of them: the base branch it was cut from,
    with what this feature's branch added. Nothing of other features' runs: what they built is known once it is merged
    into the base branch."""
    out = names(project, domain)
    for m in out.values():
        _scan(m, ws)
    return out


def toggles_at(root: Path, rev: str, domain: str) -> dict[str, set[str]]:
    """The domain's feature toggles (`migration.<domain>.<feature>.route`) in the Java files of `root` at `rev`:
    {path: toggles}. Read from git, so only what is on that commit (the base branch, or this feature's branch)."""
    toggle = re.compile(re.escape(f"migration.{domain}.") + r"[A-Za-z0-9_-]+\.route")
    try:
        listed = git_ops.git(root, "grep", "-l", "-F", f"migration.{domain}.", rev, "--", "*.java")
    except git_ops.GitError:  # nothing found (git grep exits 1), or no such commit
        return {}
    out = {}
    for line in listed.splitlines():
        path = line.split(":", 1)[1] if line.startswith(f"{rev}:") else line
        try:
            found = set(toggle.findall(git_ops.git(root, "show", f"{rev}:{path}")))
        except git_ops.GitError:
            continue
        if found:
            out[path] = found
    return out


def problems(project: str, domain: str, root: Path, kind: str, declared: str, artifact: str | None = None,
             base: str | None = None) -> list[str]:
    """The one-module-per-domain rules for a step declaring its `kind` module at `declared` (Maven `artifact`,
    groupId:artifactId): the domain's module has the folder and artifact names() gives, whether it is in this checkout
    (from the base branch) or not yet (this feature creates it, so the branches merge into one module); and every
    toggle a router read at `base` (where the step started) is still read, so the features merged into the base branch
    keep their parallel run."""
    m = names(project, domain)[kind]
    declared = posix(declared)
    label = "domain module" if kind == "domain" else "orchestrator module"
    setting = "domain_module_dir_pattern" if kind == "domain" else "orchestrator_module_dir_pattern"
    out = [] if m.artifact else [PREFIX_MISSING + ": ask the user (ask_user)"]
    if declared != m.path:
        out.append(f"The domain's {label} is {m.path} (Project settings, {setting}): build this feature into it, not "
                   f"{declared}. A domain has one {label}, shared by its features (create it there when it isn't in "
                   "this checkout yet)")
    if artifact and m.artifact and str(artifact).strip() != m.artifact:
        out.append(f"The domain's {label} is the artifact {m.artifact} (the project's base package prefix and the "
                   f"module's folder): not {artifact}")
    if base:
        for path, toggles in toggles_at(root, base, domain).items():
            f = root / path
            now = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
            for t in sorted(toggles):
                if t not in now:
                    out.append(f"{path} no longer reads {t}, a toggle on the base branch: keep the methods and "
                               "toggles of the features already merged exactly as they are")
    return out


def domain_bridges(root: Path, base: str | None, domain: str) -> set[str]:
    """The folders of the domain's routers on the base branch (a Java file reading one of its toggles): the bridge
    packages features merged before built, which later features extend."""
    if not base:
        return set()
    return {posix(Path(p).parent) for p in toggles_at(root, base, domain)}


def _design_scope(output_dir: Path) -> str | None:
    """The feature's Modulith scope (domain-dependencies.yaml decision.scope): domain-only or cross-domain."""
    f = output_dir / cs.DOMAIN_DEPENDENCIES_FILE
    try:
        data = cs.load_domain_dependencies(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except cs.InvalidDocument:
        return None
    return str((data.get("decision") or {}).get("scope") or "") or None


def _declared_module(project: str, domain: str, feature: str, where: str, block: dict, m: Module,
                     label: str) -> list[str]:
    """One module of the design (`block`: module, artifact, base_package, feature_package) against the names the
    project's settings give the domain's (`m`)."""
    missing = [k for k in ("module", "artifact", "base_package", "feature_package") if not str(block.get(k) or "").strip()]
    if missing:
        return [f"{where}: add {', '.join(missing)} of the domain's {label} (see \"This domain's modules\")"]
    module, artifact = posix(block["module"]), str(block["artifact"]).strip()
    declared_base, feature_package = str(block["base_package"]).strip(), str(block["feature_package"]).strip()
    setting = "domain_module_dir_pattern" if m.kind == "domain" else "orchestrator_module_dir_pattern"
    out = []
    if module != m.path:
        out.append(f"{where}: the {label} is {module}, but the domain's is {m.path} (Project settings, {setting}): "
                   "every feature of the domain goes into that one module")
    if m.artifact is None:
        return out + [f"{where}: {PREFIX_MISSING}; ask the user (ask_user)"]
    if artifact != m.artifact:
        out.append(f"{where}: the {label}'s artifact is {artifact}, but the domain's is {m.artifact} (the project's "
                   "base package prefix and the module's folder)")
    if declared_base != m.base_package:
        out.append(f"{where}: the {label}'s base_package is {declared_base}, but the domain's is {m.base_package} "
                   "(the project's base package prefix and the domain)")
    expected = f"{m.base_package}.{feature_segment(feature)}"
    if feature_package != expected:
        out.append(f"{where}: the {label}'s feature_package is {feature_package}: this feature's classes go in "
                   f"{expected} (<base_package>.<the feature's key without dashes>)")
    return out


def design_problems(project: str, domain: str, feature: str, output_dir: Path) -> list[str]:
    """The domain design's modules (domain-design.yaml): the domain's one domain module (`module`, `artifact`,
    `base_package`, `feature_package`) and, for a cross-domain feature, its one orchestrator module
    (`orchestrator_module`), a separate Maven module: each with the names the project's settings give it (names()), and
    this feature in its own package. Empty when the file is absent or invalid (reported elsewhere)."""
    f = output_dir / cs.DOMAIN_DESIGN_FILE
    if not f.is_file():
        return []
    try:
        data = cs.parse_domain_design(f.read_text(encoding="utf-8"))
    except cs.InvalidDocument:
        return []
    where = cs.DOMAIN_DESIGN_FILE
    mods = names(project, domain)
    out = _declared_module(project, domain, feature, where, data, mods["domain"], "domain module")
    orch = data.get("orchestrator_module")
    scope = _design_scope(output_dir)
    if isinstance(orch, dict):
        out += _declared_module(project, domain, feature, f"{where} orchestrator_module", orch, mods["orchestrator"],
                                "orchestrator module")
    elif scope == "cross-domain":
        out.append(f"{where}: add orchestrator_module (module, artifact, base_package, feature_package): a "
                   "cross-domain feature's orchestrator goes in the domain's one orchestrator module, a separate Maven "
                   "module")
    elif orch not in (None, "none", ""):
        out.append(f"{where}: orchestrator_module is a mapping (module, artifact, base_package, feature_package), or "
                   "none for a domain-only feature")
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
    # A feature package already on the branch where the step started stays valid, its feature listed or not (the
    # domain's feature list may have been listed again since this run created it)
    segments = {**{seg: seg for seg in _packages_at(root, base, prefix, base_package)}, **segments}
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
    declared = {feature_segment(str(u)) for u in declared_uses}
    missing = sorted(u for u in used if feature_segment(u) not in declared)
    if missing:
        out.append(f"This feature's package uses {', '.join(missing)}: list {'it' if len(missing) == 1 else 'them'} "
                   "in uses_features, so the features' interactions are known")
    return out


def _packages_at(root: Path, rev: str, prefix: str, base_package: str) -> set[str]:
    """The feature packages (the part after `base_package`) that Java files of `root` at `rev` sit in, under
    `prefix`: what is already on the branch."""
    try:
        listed = git_ops.git(root, "ls-tree", "-r", "--name-only", rev, "--", *([prefix] if prefix else []))
    except git_ops.GitError:
        return set()
    out = set()
    start = base_package.split(".")
    for path in listed.splitlines():
        if not path.endswith(".java"):
            continue
        parts = path.split("/")[:-1]
        java = max((i for i, p in enumerate(parts) if p == "java"), default=None)
        package = parts[java + 1:] if java is not None else []
        if package[:len(start)] == start and len(package) > len(start):
            out.add(package[len(start)])
    return out - set(COMMON)


def _segments(project: str, domain: str, feature: str) -> dict[str, str]:
    segments = {feature_segment(k): k for k, _ in domain_features(project, domain)}
    segments.setdefault(feature_segment(feature), feature)
    return segments


def _base_problems(project: str, domain: str, kind: str, declared: str | None, label: str) -> tuple[list[str], str | None]:
    """The declared base package against the domain's (base_package()): (problems, the base package to check the
    layout with)."""
    expected = base_package(project, domain, kind)
    if expected is None:
        return [PREFIX_MISSING + ": ask the user (ask_user)"], declared or None
    if declared and declared != expected:
        return [f"base_package is {declared}, but the {label}'s is {expected} (the project's base package prefix and "
                "the domain): use it"], expected
    return [], expected


def package_problems(project: str, domain: str, feature: str, root: Path, base: str, kind: str, module_path: str,
                     declaration: dict) -> list[str]:
    """One package per feature in the domain's `kind` module (layout_problems), under the domain's base package."""
    declared = str(declaration.get("base_package") or "").strip() or None
    label = "domain module" if kind == "domain" else "orchestrator module"
    if declared is None:
        return [f"Add base_package to the declaration: the {label}'s package, "
                f"{base_package(project, domain, kind) or '<prefix>.<domain>'}; every feature's classes go in "
                "<base_package>.<feature>, shared ones in <base_package>.shared"]
    out, checked = _base_problems(project, domain, kind, declared, label)
    if checked is None:
        return out
    return out + layout_problems(root, base, module_path, checked, feature, _segments(project, domain, feature),
                                 declaration.get("uses_features") or [])


def service_problems(project: str, domain: str, feature: str, root: Path, base: str, kind: str,
                     declaration: dict | None, *, one_project: bool = True, uses: list | None = None) -> list[str]:
    """The domain's one `kind` service ("service" or "orchestrator_service") in the checked-out repository `root`:
    one project (no second POM), and one package per feature under the service's base package (base_package()).
    `declaration` is the step's (Microservice, Orchestrator microservice); the steps after it (Modernise, Service
    tests) pass None, with the features this run's Microservice declared this feature uses (`uses`): this feature's
    package may use another feature's only when that feature is listed."""
    from app.module_guardrails import changes

    label = "service" if kind == "service" else "orchestrator service"
    declared = str((declaration or {}).get("base_package") or "").strip() or None
    out = []
    if declaration is not None and not declared:
        out.append(f"Add base_package to the declaration: the {label}'s package, "
                   f"{base_package(project, domain, kind) or '<prefix>.<domain>'}; every feature's classes go in "
                   "<base_package>.<feature>, shared ones in <base_package>.shared")
    problems, checked = _base_problems(project, domain, kind, declared, label)
    out += problems
    if one_project:
        out += [f"{c.path}: a second project in the service's repository. The domain has one project: add this "
                "feature to it, in its package" for c in changes(root, base)
                if c.status == "A" and Path(c.path).name == "pom.xml" and c.path != "pom.xml"]
    if checked:
        out += layout_problems(root, base, "", checked, feature, _segments(project, domain, feature),
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
    except Exception:  # noqa: BLE001 - without the database there is no project to read the settings of
        return None


def for_service(ctx, kind: str, declaration: dict | None, *, one_project: bool = True) -> list[str]:
    """service_problems for an agent's run: its project, feature and checked-out service repository."""
    project = project_of(ctx.run_id)
    if project is None or not ctx.base_sha:
        return []
    uses = None
    if declaration is None:  # Modernise, Service tests: the feature uses this run's Microservice declared
        f = ctx.output_dir / cs.MICROSERVICE_EXTRACTION_FILE
        text = f.read_text(encoding="utf-8") if f.is_file() else _own_document(ctx.run_id, cs.MICROSERVICE_EXTRACTION_FILE)
        try:
            lifted_decl = cs.parse_microservice_extraction(text) if text else {}
        except cs.InvalidDocument:
            lifted_decl = {}
        uses = list(lifted_decl.get("uses_features") or [])
    return service_problems(project, ctx.domain, ctx.operation, (ctx.workspace / ctx.code_dir).resolve(),
                            ctx.base_sha, kind, declaration, one_project=one_project, uses=uses)


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
    # The feature's own branch holds only its work: everything on it since it was cut from the base branch, changes
    # made by hand and pushed to it included (the run's steps recorded only their own)
    recorded = [*recorded, *(f for f in _branch_changes(project, ws / entry["path"], entry) if f not in recorded)]
    files = sorted(f for f in recorded if not f.startswith(SUITE))
    return {"url": url, "key": entry["key"], "path": entry["path"], "base": entry["base_sha"], "files": files}


def _branch_changes(project: str, checkout: Path, entry: dict) -> list[str]:
    """The files changed (not deleted) on the checkout's branch since it left its base branch: from where it meets the
    base branch as last fetched, else from where the run checked it out."""
    from app import repos
    from app.module_guardrails import changes

    start = entry.get("base_sha")
    try:
        mirror = repos.mirror_dir(project, entry["url"])
        base = entry.get("base_branch") or (repos.default_branch(mirror) if mirror.exists() else None)
        if base and mirror.exists() and git_ops.commit_info(mirror, f"refs/heads/{base}") is not None:
            git_ops.git(checkout, "fetch", "--quiet", str(mirror), f"+refs/heads/{base}:refs/studio/base")
            start = git_ops.git(checkout, "merge-base", "HEAD", "refs/studio/base") or start
    except (git_ops.GitError, OSError):
        pass
    if not start:
        return []
    try:
        return [c.path for c in changes(checkout, start) if c.status != "D"]
    except Exception:  # noqa: BLE001 - what the run recorded still lists the files
        return []


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
    service_base = base_package(project, ctx.domain, "service") or decl.get("base_package")
    if not service_base:
        return out
    allowed = {feature_segment(ctx.operation), *COMMON} | {feature_segment(u) for u in decl.get("uses_features") or []}

    def rel(root: Path, path: str) -> str | None:
        text = (root / path).read_text(encoding="utf-8", errors="replace") if (root / path).is_file() else ""
        m = PACKAGE.search(text)
        if not m or not m.group(1).startswith(service_base + "."):
            return None
        return m.group(1)[len(service_base) + 1:]

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
            seg = m.group(1)[len(service_base) + 1:].split(".")[0] if m and m.group(1).startswith(service_base + ".") \
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


BRANCHING = ("**Each feature on its own branch.** Every feature works on its own branch, cut from the base branch, and "
             "is merged into it by hand when it is done, in every repository (the monolith and each service's). Your "
             "checkout is the base branch as it was when your branch was cut, with your own work: what another feature "
             "built is in it only once that feature was merged into the base branch. Never wait for another feature, "
             "and never change what another feature's run is doing.")


def brief(project: str, domain: str, run_id: str | None, ws: Path | None, stage: str | None = None) -> str:
    """What the module and service steps must know: the domain's modules and services, named from the project's
    settings, and what the checkout (the base branch, with this feature's work) already has of them. Nothing of other
    features' runs. The wording is the shared text "domain_architecture" (and "lift_rules" for Modernise), editable on
    the Agents page; the data is filled in here."""
    from app import shared_texts

    mods = existing(project, domain, ws)
    features = domain_features(project, domain)
    modules: list[str] = [] if prefix(project) else [f"- {PREFIX_MISSING}: ask the user (ask_user) before writing code."]
    for m in mods.values():
        label = "Domain module" if m.kind == "domain" else "Orchestrator module"
        modules.append(f"- {label}: `{m.path}/`" + (f", artifact `{m.artifact}`" if m.artifact else "")
                       + (f", base package `{m.base_package}`" if m.base_package else "")
                       + " (the domain's one module, named from the project's settings; use exactly these names).")
        if ws is None:  # a design step: no checkout
            continue
        if not m.present:
            modules.append("  Not in your checkout (no feature merged it into the base branch yet): create it here, with "
                           "exactly these names, holding only what this feature needs (its package, and the "
                           "`shared`/`util` classes it uses), so the features' branches merge into one module.")
            continue
        modules.append("  In your checkout (from the base branch): extend it; keep what is there.")
        if m.classes:
            modules.append(f"  Classes already there (reuse them): {_listing(m.classes)}.")
        if m.statements:
            modules.append(f"  SQL statements already there (reuse them): {_listing(m.statements)}.")
    if ws is not None and (ws / ".git").exists():
        toggles = sorted({t for found in toggles_at(ws, "HEAD", domain).values() for t in found})
        if toggles:
            modules.append(f"- Feature toggles already in your checkout (keep each one, with its methods): "
                           f"{_listing(toggles)}.")
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
        where = repositories.get(repo)
        package = base_package(project, domain, kind)
        service_lines.append(f"- {label}: " + (f"`{where}`" if where else "repository not set in the domain's Agent "
                                                                          "inputs yet")
                             + (f"; base package `{package}`" if package else "") + ".")
    text = shared_texts.fill("domain_architecture", {
        "domain": domain, "branching": BRANCHING,
        "feature_packages": (", ".join(f"{k} → {feature_segment(k)}" for k, _ in features)
                             or "no features listed for the domain yet") + f"; shared → {SHARED}; util → {UTIL}",
        "modules": "\n".join(modules), "services": "\n".join(service_lines)})
    if stage in MODERN_AGENTS and stage != "service_tests":
        lift = lift_brief(lifted(project, domain, run_id, ws), feature_of(run_id) or "this feature",
                          base_package(project, domain, "service"))
        if lift:
            text += "\n\n" + lift
    return text
