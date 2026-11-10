"""The temporary modules that stand in for domains not migrated yet.

While a feature migrates, what it needs from a domain still in the monolith (a call, or that domain's reaction to an
event this feature publishes) goes through two temporary Maven modules of the monolith, one package per domain, so the
owner is plain and a domain's packages are deleted once that domain is migrated:

- the **integration module** (`integration_module_dir`): one *entry point* per linked-domain contract with a temporary
  binding. The entry point implements the contract's interface and only maps the contract's types and errors to and
  from what its *gateway* gives (an anti-corruption layer); the gateway is an interface the integration module declares
  (`<prefix>.integration.<domain>.spi`), implemented in the monolith by one thin *legacy adapter* that calls exactly the
  binding's `delegates_to` (`<adapter_dir>/<domain>/`, package `<prefix>.integration.legacy.<domain>`). The adapters are
  the only new code that touches legacy classes;
- the **event-handler module** (`event_handler_module_dir`): one *handler* per event a domain still in the monolith
  must react to (`<prefix>.eventhandler.<domain>`). A handler reaches every domain it affects only through integration
  entry-point interfaces, in a new transaction (it runs after the publisher's commit), and never calls monolith code,
  so moving it to a service later only swaps those calls for API calls.

The interfaces both modules implement (the entry points' `<prefix>.integration.<domain>.api`, the handlers'
`<prefix>.eventhandler.<domain>.api`, with their DTOs) live in the monolith's existing interfaces module
(`interfaces_module`): they outlive the temporary modules, as what a REST client implements later. The domain and
orchestrator modules depend on the interfaces module only, and call those interfaces; the event port's adapter in the
bridge calls the handlers after the commit. Nothing depends on the two temporary modules: Spring wires their beans.

Names are fixed by the contract key, or by the event key and the handling domain (entry_point_names,
handler_names), and packages by the project's settings,
so features migrated on parallel branches build the same classes, and a later feature reuses (and only extends) them.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.guardrail_problems import blocker

INTEGRATION = "integration"
EVENT_HANDLER = "event-handler"
INTERFACES_MISSING = ("The project's interfaces module isn't set (Project → Settings → Interfaces module): the "
                      "entry-point and handler interfaces of the domains not migrated yet go there")
JAVA_ROOT = "src/main/java"
# What the two temporary modules and the published interfaces may import besides their own packages.
LOGGING = ("org.slf4j.", "org.apache.commons.logging.", "org.apache.log4j.", "java.util.logging.")
PLAIN = ("java.", "javax.annotation.", *LOGGING)
SPRING = ("org.springframework.beans.", "org.springframework.stereotype.", "org.springframework.context.",
          "org.springframework.util.")
STEREOTYPE = re.compile(r"@(?:Component|Service|Repository|Controller|Autowired|Transactional)\b")
IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)
CATCH = re.compile(r"\bcatch\s*\(")


def pascal(text: str) -> str:
    """`get-kyc-status` → `GetKycStatus`."""
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", text) if p)


def _key_base(key: str) -> str:
    """A key in PascalCase, its owner named once: `customer.get-kyc-status` → CustomerGetKycStatus,
    `loan.loan-approved` → LoanApproved (not LoanLoanApproved)."""
    owner, _, name = str(key).partition(".")
    name = name or owner
    if name == owner or name.startswith(owner + "-"):
        return pascal(name)
    return pascal(owner) + pascal(name)


def entry_point_names(contract_key: str) -> dict[str, str]:
    """The classes of a contract's entry point, from its key alone (`customer.get-kyc-status`): the interface in the
    interfaces module, its implementation in the integration module, the gateway interface it calls and the legacy
    adapter that implements the gateway. Unique in the project, as contract keys are."""
    base = _key_base(contract_key)
    return {"api": f"{base}Api", "implementation": f"{base}EntryPoint", "gateway": f"{base}Gateway",
            "adapter": f"{base}GatewayLegacy"}


def handler_names(event_key: str, domain: str) -> dict[str, str]:
    """A handler's interface and implementation, from the handling domain and the event's key (customer handling
    `loan.loan-approved` → CustomerLoanApprovedHandler): unique in the project, so two domains handling one event, or
    one domain handling two publishers' events of the same name, never share a class name."""
    base = pascal(domain) + _key_base(event_key)
    return {"interface": f"{base}Handler", "implementation": f"{base}HandlerImpl"}


def _segment(domain: str) -> str:
    from app.domain_modules import domain_segment

    return domain_segment(domain)


@dataclass
class Layout:
    """Where the temporary modules and the published interfaces go in the monolith, from the project's settings."""
    prefix: str
    interfaces_dir: str  # "" when the project hasn't set it
    integration_dir: str
    event_handler_dir: str
    adapter_dir: str = ""  # declared by the Module in monolith step (a package of the monolith's web application)
    interfaces_artifact: str | None = None  # groupId:artifactId, read from its pom.xml
    artifacts: set[str] = field(default_factory=set)  # groupId:artifactId of the three modules

    @property
    def prefix_path(self) -> str:
        return self.prefix.replace(".", "/")

    def api_dir(self, kind: str) -> str:
        """The folder of the published interfaces of `kind` (integration or event-handler) in the interfaces module."""
        sub = "integration" if kind == INTEGRATION else "eventhandler"
        return f"{self.interfaces_dir}/{JAVA_ROOT}/{self.prefix_path}/{sub}" if self.interfaces_dir else ""

    def package(self, kind: str, domain: str) -> str:
        sub = "integration" if kind == INTEGRATION else "eventhandler"
        return f"{self.prefix}.{sub}.{_segment(domain)}"

    def entry_point_files(self, key: str, provider: str) -> dict[str, str]:
        names, seg, p = entry_point_names(key), _segment(provider), self.prefix_path
        return {"api": f"{self.api_dir(INTEGRATION)}/{seg}/api/{names['api']}.java",
                "implementation": f"{self.integration_dir}/{JAVA_ROOT}/{p}/integration/{seg}/{names['implementation']}.java",
                "gateway": f"{self.integration_dir}/{JAVA_ROOT}/{p}/integration/{seg}/spi/{names['gateway']}.java",
                "adapter": f"{self.adapter_dir}/{seg}/{names['adapter']}.java" if self.adapter_dir else ""}

    def handler_files(self, event: str, domain: str) -> dict[str, str]:
        names, seg, p = handler_names(event, domain), _segment(domain), self.prefix_path
        return {"interface": f"{self.api_dir(EVENT_HANDLER)}/{seg}/api/{names['interface']}.java",
                "implementation": f"{self.event_handler_dir}/{JAVA_ROOT}/{p}/eventhandler/{seg}/"
                                  f"{names['implementation']}.java"}

    def folders(self) -> list[str]:
        return [f for f in (self.api_dir(INTEGRATION), self.api_dir(EVENT_HANDLER), self.integration_dir,
                            self.event_handler_dir, self.adapter_dir) if f]


def _pom_artifact(text: str | None) -> str | None:
    if not text:
        return None
    outside_parent = re.sub(r"<parent>.*?</parent>", "", text, flags=re.DOTALL)
    outside_parent = re.sub(r"<dependencies>.*?</dependencies>", "", outside_parent, flags=re.DOTALL)
    artifact = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", outside_parent)
    group = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", outside_parent) or \
        re.search(r"<parent>.*?<groupId>\s*([^<\s]+)\s*</groupId>", text, flags=re.DOTALL)
    return f"{group.group(1)}:{artifact.group(1)}" if artifact and group else None


def layout(project: str | None, root: Path, base: str | None, adapter_dir: str = "") -> Layout | None:
    """The layout of `project` in the checkout at `root`; None without a project or a base package prefix."""
    from app import settings_store
    from app.domain_modules import posix, prefix
    from app.module_guardrails import _read, _show

    if project is None:
        return None
    p = prefix(project)
    if p is None:
        return None
    settings = settings_store.get_project(project)
    interfaces = posix(settings.get("interfaces_module") or "")
    out = Layout(prefix=p, interfaces_dir=interfaces,
                 integration_dir=posix(settings.get("integration_module_dir") or "legacy-integration"),
                 event_handler_dir=posix(settings.get("event_handler_module_dir") or "legacy-event-handlers"),
                 adapter_dir=posix(adapter_dir))
    if interfaces:
        pom = (_show(root, base, f"{interfaces}/pom.xml") if base else None) or _read(root, f"{interfaces}/pom.xml")
        out.interfaces_artifact = _pom_artifact(pom)
    out.artifacts = {a for a in (out.interfaces_artifact, f"{p}:{out.integration_dir.split('/')[-1]}",
                                 f"{p}:{out.event_handler_dir.split('/')[-1]}") if a}
    return out


def for_step(ctx, root: Path, declaration: dict | None, side: str, adapter_dir: str = "") -> dict | None:
    """The `legacy` part of a build step's guardrail check (module_guardrails.check): the layout and what the run's
    design gives this step. None for a design without entry points or handlers (an older run: nothing to check) or a
    project without a base package prefix (reported by the module checks)."""
    from app import contracts_store as cs
    from app.domain_modules import project_of

    entry_points = cs.expected_entry_points(ctx.output_dir)
    handlers = cs.expected_event_handlers(ctx.output_dir, side)
    declared = (declaration or {}).get("legacy_modules")
    if entry_points is None and handlers is None and not declared:
        return None
    lay = layout(project_of(ctx.run_id), root, ctx.base_sha,
                 adapter_dir or str((declared or {}).get("adapter_dir") or "") if side == "domain" else "")
    if lay is None:
        return None
    return {"layout": lay, "declared": declared, "entry_points": entry_points, "handlers": handlers, "side": side}


# ---- The check --------------------------------------------------------------------------------------------------

def _imports(code: str) -> list[str]:
    return IMPORT.findall(code)


def _allowed_import(name: str, allowed: tuple[str, ...]) -> bool:
    return name.startswith(allowed)


def _implements(code: str, name: str) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", m)
               for m in re.findall(r"\bimplements\s+([\w.,\s<>]+?)\s*\{", code))


def check(root: Path, lay: Layout, diff: list, inside: list, in_bridge: list, bridge: str, declared: dict | None,
          entry_points: list[dict] | None, handlers: list[dict] | None, side: str) -> list[str]:
    """Violations of the temporary modules' rules in this step's change (`diff`, module_guardrails.Change):
    `entry_points` and `handlers` are what the design gives this step (contracts_store.expected_entry_points,
    expected_event_handlers; None: an older design without them, nothing to check), `declared` the step's
    `legacy_modules` declaration, `inside` and `in_bridge` the changes in the domain (or orchestrator) module and the
    bridge, `side` domain or orchestrator."""
    from app.module_guardrails import (AFTER_COMMIT, COLLABORATOR, COMMENTS, DB_ACCESS, OTHER_PROPAGATION, REGISTERS,
                                       REQUIRES_NEW, TRANSACTION, _is_test, _read, _under)

    entry_points = entry_points or []
    handlers = handlers or []
    folders = lay.folders()
    mine = [c for c in diff if _under(c.path, folders)]
    if not entry_points and not handlers and not mine:
        return []
    if not lay.interfaces_dir:
        return [blocker(INTERFACES_MISSING + ", then retry")]
    where = "orchestrator-extraction.yaml" if side == "orchestrator" else "module-extraction.yaml"
    problems: list[str] = []
    if side == "domain" and entry_points and not lay.adapter_dir:
        problems.append(f"{where}: add legacy_modules.adapter_dir, the new package of the monolith's web application "
                        f"for the legacy adapters ({lay.prefix}.integration.legacy; one sub-package per domain)")
    p = lay.prefix

    # 1. Exactly what the design gives this step, declared.
    built_here = [e for e in entry_points if not e.get("reused")] if side == "domain" else []
    if declared is None and (built_here or handlers):
        problems.append(f"{where}: add legacy_modules: the entry points and event handlers this step builds for the "
                        "domains not migrated yet (the guardrail compares them with the design)")
        declared = {}
    if declared is not None:
        want_keys = {e["key"] for e in built_here}
        allowed = {e["key"] for e in entry_points} if side == "domain" else set()  # reused ones may be listed
        have_keys = set(declared.get("entry_points") or [])
        problems += [f"{where}: legacy_modules.entry_points misses {k}: the design gives it an integration entry "
                     "point, which this step builds" for k in sorted(want_keys - have_keys)]
        problems += [f"{where}: legacy_modules.entry_points lists {k}, which the design gives no entry point (or not "
                     "to this step)" for k in sorted(have_keys - allowed)]
        want_h = {(h["event"], h["domain"]) for h in handlers}
        have_h = {(str(h.get("event")), str(h.get("domain"))) for h in declared.get("handlers") or []}
        problems += [f"{where}: legacy_modules.handlers misses {e} handled by {d}: the design gives it an event "
                     "handler that this step builds" for e, d in sorted(want_h - have_h)]
        problems += [f"{where}: legacy_modules.handlers lists {e} handled by {d}, which the design doesn't give this "
                     "step" for e, d in sorted(have_h - want_h)]

    def source(path: str) -> str | None:
        f = root / path
        return COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace")) if f.is_file() else None

    # 2. Each entry point: its interface (with the operations), its implementation calling only its gateway, the
    # gateway, and the legacy adapter calling exactly the binding's delegates_to.
    for e in entry_points if side == "domain" else []:
        files, names = lay.entry_point_files(e["key"], e["provider"]), entry_point_names(e["key"])
        api, impl, gateway = source(files["api"]), source(files["implementation"]), source(files["gateway"])
        if api is None:
            problems.append(f"{e['key']}: the entry-point interface {files['api']} doesn't exist. It is the contract's "
                            "interface in the interfaces module, which the domain and the orchestrator call")
        else:
            problems += [f"{files['api']}: no operation {o['method']}(…) of {e['key']}" for o in e["operations"]
                         if not re.search(rf"\b{re.escape(o['method'])}\s*\(", api)]
        if gateway is None:
            problems.append(f"{e['key']}: the gateway interface {files['gateway']} doesn't exist. The entry point "
                            "reaches the legacy code only through it")
        if impl is None:
            problems.append(f"{e['key']}: the entry point {files['implementation']} doesn't exist")
        else:
            if not _implements(impl, names["api"]):
                problems.append(f"{files['implementation']} does not implement {names['api']}")
            if not re.search(rf"\b{re.escape(names['gateway'])}\b", impl):
                problems.append(f"{files['implementation']} does not call its gateway {names['gateway']}: an entry "
                                "point maps the contract's types and errors and calls its gateway, nothing else")
            others = sorted(set(re.findall(r"\b([A-Z]\w*Gateway)\b", impl)) - {names["gateway"]})
            if others:
                problems.append(f"{files['implementation']} uses {', '.join(others)}: an entry point calls only its own "
                                f"gateway {names['gateway']}")
        if lay.adapter_dir:
            adapter = source(files["adapter"])
            if adapter is None:
                problems.append(f"{e['key']}: the legacy adapter {files['adapter']} doesn't exist. It implements "
                                f"{names['gateway']} by calling {', '.join(e['delegates_to'])}")
            else:
                if not _implements(adapter, names["gateway"]):
                    problems.append(f"{files['adapter']} does not implement {names['gateway']}")
                problems += _calls_exactly(files["adapter"], adapter, e["delegates_to"], COLLABORATOR,
                                           {names["gateway"], names["adapter"]})
                hit = DB_ACCESS.search(adapter) or TRANSACTION.search(adapter)
                if hit:
                    problems.append(f"{files['adapter']} uses {hit.group(0).strip()}: a legacy adapter only calls "
                                    "its delegates_to, with no database access or transaction of its own")

    # 3. Each handler: its interface, and its implementation calling only the integration interfaces of its calls, in a
    # new transaction, catching what fails (the publisher's caller never sees it).
    for h in handlers:
        files, names = lay.handler_files(h["event"], h["domain"]), handler_names(h["event"], h["domain"])
        api, impl = source(files["interface"]), source(files["implementation"])
        if api is None:
            problems.append(f"{h['event']} ({h['domain']}): the handler interface {files['interface']} doesn't exist. "
                            "The event port's adapter calls it after the commit")
        if impl is None:
            problems.append(f"{h['event']} ({h['domain']}): the handler {files['implementation']} doesn't exist")
            continue
        if not _implements(impl, names["interface"]):
            problems.append(f"{files['implementation']} does not implement {names['interface']}")
        for key in h["calls"]:
            api_name = entry_point_names(key)["api"]
            if not re.search(rf"\b{re.escape(api_name)}\b", impl):
                problems.append(f"{files['implementation']} does not call {api_name} ({key}): a handler does its work "
                                "through the integration entry points its design names")
        if not REQUIRES_NEW.search(impl):
            problems.append(f"{files['implementation']} does not run its calls in a new transaction. It runs after the "
                            "publisher's transaction has committed, while its connection is still bound: without "
                            "PROPAGATION_REQUIRES_NEW (a TransactionTemplate) the writes join the committed "
                            "transaction and are lost")
        elif OTHER_PROPAGATION.search(impl):
            problems.append(f"{files['implementation']} uses {OTHER_PROPAGATION.search(impl).group(0)}: a handler "
                            "runs with PROPAGATION_REQUIRES_NEW only")
        if not CATCH.search(impl):
            problems.append(f"{files['implementation']} lets a failure escape: the feature's change is already "
                            "committed, so a handler catches and logs what fails (with the event and its key)")
        # Who calls it: the event port's adapter in the bridge, after the commit.
        word = re.compile(rf"\b{re.escape(names['interface'])}\b")
        folder = root / bridge if bridge else None
        callers = {f.relative_to(root).as_posix(): COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace"))
                   for f in (folder.rglob("*.java") if folder is not None and folder.is_dir() else [])}
        callers = {path: text for path, text in callers.items() if word.search(text) and not _is_test(path)}
        if not callers:
            problems.append(f"Nothing in the bridge package calls {names['interface']}: the event port's adapter calls "
                            "it after the commit")
        for path, text in callers.items():
            if not (AFTER_COMMIT.search(text) and REGISTERS.search(text)):
                problems.append(f"{path} calls {names['interface']} without deferring it to after the commit: "
                                "register a TransactionSynchronization (afterCommit) when a transaction is active, and "
                                "call it at once only when none is")

    # 4. What every changed file in these folders may hold.
    api_prefixes = (f"{p}.integration.", f"{p}.eventhandler.")
    for c in mine:
        if c.status == "D":
            problems.append(f"{c.path} deleted. A domain's entry points and handlers go only when that domain is "
                            "migrated, not in a feature's run")
            continue
        if c.status == "M" and any(x.strip() not in ("", "}") for x in c.removed) and not c.path.endswith(".xml"):
            problems.append(f"{c.path}: lines removed or changed (`{next(x for x in c.removed if x.strip() not in ('', '}')).strip()[:80]}`). "
                            "An entry point, handler or adapter another feature built is reused and only extended")
        if not c.path.endswith(".java") or _is_test(c.path):
            continue
        code = source(c.path) or ""
        imports = _imports(code)
        if _under(c.path, [lay.api_dir(INTEGRATION), lay.api_dir(EVENT_HANDLER)]):
            if "/api/" not in c.path:
                problems.append(f"{c.path}: the interfaces module holds only the published interfaces and DTOs, in "
                                f"{p}.integration.<domain>.api and {p}.eventhandler.<domain>.api")
            bad = [i for i in imports if not _allowed_import(i, (*PLAIN, "javax.", *api_prefixes))]
            hit = DB_ACCESS.search(code) or TRANSACTION.search(code) or STEREOTYPE.search(code)
            if bad or hit:
                problems.append(f"{c.path} uses {(bad or [hit.group(0)])[0]}: a published interface is interfaces and "
                                "DTOs only, with no legacy type, no database access and no Spring bean")
        elif _under(c.path, [lay.integration_dir]):
            bad = [i for i in imports if not _allowed_import(i, (*PLAIN, *SPRING, f"{p}.integration."))
                   or i.startswith(f"{p}.integration.legacy.")]
            if bad:
                problems.append(f"{c.path} imports {bad[0]}: the integration module reaches the monolith only through "
                                "its gateway interfaces; their legacy adapters live in the web application")
            hit = DB_ACCESS.search(code) or TRANSACTION.search(code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0).strip()}: an entry point has no database access and no "
                                "transaction of its own; it calls its gateway")
        elif _under(c.path, [lay.event_handler_dir]):
            api_import = re.compile(rf"^{re.escape(p)}\.integration\.\w+\.api(\.|$)")
            bad = [i for i in imports if not (_allowed_import(i, (*PLAIN, *SPRING, "org.springframework.transaction.",
                                                                  f"{p}.eventhandler."))
                                              or api_import.match(i))]
            if bad:
                problems.append(f"{c.path} imports {bad[0]}: a handler reaches other domains only through the "
                                "integration entry-point interfaces (…integration.<domain>.api), never monolith code or "
                                "the integration module's own classes")
            hit = DB_ACCESS.search(code)
            if hit:
                problems.append(f"{c.path} uses {hit.group(0).strip()}: a handler has no database access; the entry "
                                "points it calls do the work")
    # 5. The domain (or orchestrator) module never names the legacy classes the bindings delegate to.
    delegates = {m.split("#")[0] for e in entry_points for m in e["delegates_to"]}
    delegates |= {m.split("#")[0] for h in handlers for m in h.get("delegates_to") or []}
    for c in inside:
        if c.status == "D" or not c.path.endswith(".java") or _is_test(c.path):
            continue
        code = _read(root, c.path)
        hit = next((d for d in sorted(delegates) if re.search(rf"^\s*import\s+{re.escape(d)}\s*;", code, re.MULTILINE)),
                   None)
        if hit:
            problems.append(f"{c.path} imports {hit}, a legacy class of a domain not migrated yet: call the entry-point "
                            f"interface ({p}.integration.<domain>.api) instead")
    return problems


def _calls_exactly(path: str, code: str, delegates: list[str], collaborator: re.Pattern, own: set[str]) -> list[str]:
    problems = []
    simple = {m.split("#")[0].split(".")[-1] for m in delegates}
    for m in delegates:
        cls, method = m.split("#")
        name = cls.split(".")[-1]
        if not re.search(rf"\b{re.escape(name)}\b", code) or not re.search(rf"\.\s*{re.escape(method)}\s*\(", code):
            problems.append(f"{path} does not call {m}: a legacy adapter calls exactly its delegates_to")
        imported = re.search(rf"^\s*import\s+([\w.]+\.{re.escape(name)})\s*;", code, re.MULTILINE)
        if imported and "." in cls and imported.group(1) != cls:
            problems.append(f"{path} imports {imported.group(1)}, not {cls} that delegates_to names")
    others = sorted(set(collaborator.findall(code)) - simple - own)
    if others:
        problems.append(f"{path} uses {', '.join(others)}: a legacy adapter calls only its delegates_to "
                        f"({', '.join(delegates)})")
    return problems
