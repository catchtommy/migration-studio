"""Guardrails of the Module in monolith step, checked from git rather than trusted from the agent's word.

The step builds one feature's domain module **beside** the as-is code (a parallel run): the module is a copy of the
feature's code, the as-is code stays the old flow, and a router behind the legacy service's bean chooses between them
by the feature's toggle (`legacy`, the default, or `monolith`). Compared with the commit the step started from, only
these changes may exist:

- inside the new module: anything, except what the checks below forbid (other domains' tables and packages, God
  modules, dependencies beyond the allow-list, SQL that is not copied from the monolith, ambient inputs);
- in the bridge package: the router and adapters (each class implements a port of the domain module or the legacy
  interface the router takes over), and helpers (mappers) the router or an adapter uses, without database access;
- elsewhere: only Spring wiring lines (the legacy bean's new id, the router's registration, aliases, imports) and POM
  lines for the new module, in files listed in `outside_changes`; the toggle's default in a properties file;
- in a legacy caller listed in `toggle_branches` (a controller, job or service with no bean seam for the router): the
  toggle branch that asks the router, and whatever refactoring it needs (lines moved or rewritten), as long as a test
  of that class in this change pins its behaviour; no new database access there, and never in a DAO;
- new tests anywhere, and lines added to existing tests (neither changes behaviour);
- nowhere: a changed, moved or deleted legacy file otherwise (Java, JSP, SQL map, existing test), UI files, DDL.

The module and bridge folders are new, or this domain's own from an earlier feature (`earlier`). The Orchestrator in
monolith step reuses the same rules (see orchestrator_guardrails.py). `check()` returns one line per violation.
"""

import copy
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app import boundary_check
from app.guardrail_problems import blocker

UI_SUFFIXES = {".jsp", ".jspf", ".jspx", ".tag", ".tagx", ".tld", ".html", ".htm", ".xhtml", ".js", ".css", ".scss",
               ".less", ".png", ".gif", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot"}
DDL = re.compile(r"\b(?:create|alter|drop|truncate|rename)\s+(?:unique\s+|bitmap\s+|global\s+temporary\s+)?"
                 r"(?:table|index|view|sequence|trigger|synonym|constraint|procedure|function|package|column)\b"
                 r"|\badd\s+column\b", re.IGNORECASE)
DDL_SUFFIXES = {".xml", ".sql", ".ddl", ".java", ".properties", ".groovy", ".kt"}
STATEMENT_TAGS = {"select", "insert", "update", "delete", "statement", "procedure", "sql", "resultMap", "parameterMap"}
BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "build.xml", "ivy.xml"}
SKIP_PREFIXES = (".migration/", ".studio/", ".git/")
MAX_OUTSIDE_FILES = 10  # more changed files than this outside the modules: stop and ask
MAX_REPORTED = 60

AMBIENT = re.compile(r"\b(?:HttpSession|HttpServletRequest|HttpServletResponse|ServletContext|RequestContextHolder|"
                     r"ServletRequestAttributes|SecurityContextHolder|InheritableThreadLocal|ThreadLocal)\b|javax\.servlet")
WIRING = re.compile(r"<\s*(?:import\s+resource|sqlMap\s+resource|alias\s+name)", re.IGNORECASE)
BEAN_LINE = re.compile(r"<\s*/?\s*(?:bean|property|constructor-arg|ref|value)\b", re.IGNORECASE)
CLOSING = re.compile(r"^\s*</\s*(?:bean|property|constructor-arg|value)\s*>\s*$", re.IGNORECASE)
NEW_BEAN = re.compile(r"<\s*bean\b[^>]*\bid\s*=\s*\"([\w.-]+)\"[^>]*\bclass\s*=\s*\"[\w.$]*?(\w+)\"", re.IGNORECASE)
DB_ACCESS = re.compile(r"\b(?:java\.sql|javax\.sql|DataSource|JdbcTemplate|NamedParameterJdbcTemplate|SimpleJdbc\w+|"
                       r"SqlMapClient\w*|SqlMapSession|SqlSession\w*|com\.ibatis|org\.apache\.ibatis|org\.mybatis|"
                       r"EntityManager\w*|javax\.persistence|org\.hibernate|\w*(?:Dao|DAO))\b")
TRANSACTION = re.compile(r"@Transactional\b|\b(?:TransactionTemplate|PlatformTransactionManager|TransactionStatus|"
                         r"TransactionSynchronization\w*)\b|org\.springframework\.transaction")
COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
DATA_LAYER = re.compile(r"\bclass\s+\w*(?:Dao|DAO)(?:Impl)?\b|\b(?:SqlMapClientDaoSupport|JdbcDaoSupport)\b")
POM_LINE = re.compile(r"^\s*(?:<module>[^<]+</module>|</?dependency>|<groupId>[^<]+</groupId>|"
                      r"<artifactId>[^<]+</artifactId>|<version>[^<]+</version>|<scope>[^<]+</scope>|"
                      r"<type>[^<]+</type>|<classifier>[^<]+</classifier>|<!--.*-->)\s*$")


@dataclass
class Change:
    path: str
    status: str  # A, M, D, R
    old_path: str | None = None
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _git(root: Path, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"git {' '.join(args)} failed")
    return proc.stdout


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # what a repository without commits is compared with


def head(root: Path) -> str | None:
    """The commit the repository is at; for a repository without commits, the empty tree; None if not a repository."""
    try:
        return _git(root, "rev-parse", "HEAD").strip()
    except (RuntimeError, OSError):
        pass
    try:
        _git(root, "rev-parse", "--git-dir")
        return EMPTY_TREE
    except (RuntimeError, OSError):
        return None


def changes(root: Path, base: str) -> list[Change]:
    """Everything that differs from `base`: commits, staged, unstaged and new files, via a throw-away index."""
    if base == EMPTY_TREE:  # git only knows the empty tree once something has written it
        subprocess.run(["git", "hash-object", "-t", "tree", "-w", "--stdin"], cwd=root, input="", text=True,
                       capture_output=True, check=False)
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        real = root / _git(root, "rev-parse", "--git-path", "index").strip()
        if real.is_file():
            shutil.copyfile(real, index)  # keeps git's file-stat cache, so unchanged files are not hashed again
        else:
            _git(root, "read-tree", base, env=env)
        # No pathspecs: the studio's folders are ignored already (.git/info/exclude) and left out below, and naming an
        # ignored path makes some versions of git fail ("paths are ignored"). Nor its CRLF or ignored-file notices.
        _git(root, "-c", "core.safecrlf=false", "-c", "advice.addIgnoredFile=false", "add", "-A", env=env)
        names = _git(root, "diff", "--cached", "-M", "--name-status", "-z", base, env=env).split("\0")
        patch = _git(root, "diff", "--cached", "-M", "-U0", "--no-color", base, env=env)
    result, i = [], 0
    while i < len(names) and names[i]:
        status = names[i][0]
        if status == "R":
            result.append(Change(names[i + 2], "R", names[i + 1]))
            i += 3
        else:
            result.append(Change(names[i + 1], status))
            i += 2
    by_path = {c.path: c for c in result}
    current = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current = by_path.get(line[6:]) if line.startswith("+++ b/") else None
        elif current is not None and line[:1] in "+-" and not line.startswith(("+++", "---")):
            (current.added if line[0] == "+" else current.removed).append(line[1:])
    return [c for c in result if not c.path.startswith(SKIP_PREFIXES)]


def _show(root: Path, rev: str, path: str) -> str | None:
    try:
        return _git(root, "show", f"{rev}:{path}")
    except RuntimeError:
        return None


def _read(root: Path, path: str) -> str:
    f = root / path
    return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""


def _under(path: str, folders: list[str]) -> bool:
    return any(path == f or path.startswith(f + "/") for f in folders if f)


def _is_test(path: str) -> bool:
    return "/src/test/" in "/" + path or path.endswith(("Test.java", "Tests.java", "TestCase.java"))


def _simple(path: str) -> str:
    return Path(path).stem


# ---- SQL maps ---------------------------------------------------------------------------------------------------

# Attributes that name another part of the SQL maps (a statement, result map, parameter map, SQL fragment, cache):
# a copy running beside the original in the same iBatis configuration must rename them to keep them unique.
NAME_ATTRS = {"id", "resultMap", "parameterMap", "extends", "refid", "select", "cacheModel"}
# Attributes that name a Java type: by class name (the copied classes move to the module's package) or by alias
# (renamed with the copy, like the names above). The built-in aliases are the same everywhere.
TYPE_ATTRS = {"resultClass", "parameterClass", "class", "javaType", "typeHandler", "type"}
BUILTIN_ALIASES = {"string", "int", "integer", "long", "short", "byte", "double", "float", "boolean", "date", "decimal",
                   "bigdecimal", "biginteger", "object", "map", "hashmap", "list", "arraylist", "collection",
                   "iterator", "cursor", "xml", "char", "character"}


def _renamable(el: ET.Element) -> ET.Element:
    """The statement with what a copy renames set aside: names of SQL-map parts, aliases, and packages of classes."""
    el = copy.deepcopy(el)
    for node in el.iter():
        for attr, value in list(node.attrib.items()):
            if attr in NAME_ATTRS:
                node.set(attr, "#name")
            elif attr in TYPE_ATTRS:
                if "." in value:
                    node.set(attr, value.rsplit(".", 1)[-1].split("$")[-1])  # the class, whatever its package
                elif value.lower() not in BUILTIN_ALIASES:
                    node.set(attr, "#alias")
    return el


def statements(xml_text: str | None, strict: bool = False) -> list[str]:
    """Canonical text of every statement in an iBatis SQL map, whitespace collapsed. By default for comparing a copy
    with its original: what the copy may rename to run beside it in the same iBatis configuration is set aside (ids of
    statements, result maps, parameter maps and fragments, and references to them; type aliases; packages of classes),
    while any change to its SQL, its mappings or its dynamic tags still shows. `strict`: exactly as written, ids
    included, for maps that must not change at all."""
    if not xml_text or "<sqlMap" not in xml_text:
        return []
    try:
        tree = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xml_text, flags=re.DOTALL))
    except ET.ParseError:
        return []
    out = []
    for el in tree.iter():
        if el.tag in STATEMENT_TAGS:
            text = ET.tostring(el if strict else _renamable(el), encoding="unicode")
            out.append(re.sub(r"\s+", " ", text).strip())
    return out


def _base_statements(root: Path, base: str) -> list[str]:
    try:
        files = _git(root, "grep", "-l", "-F", "<sqlMap", base, "--", "*.xml").splitlines()
    except RuntimeError:
        return []
    out = []
    for f in files:
        out += statements(_show(root, base, f.split(":", 1)[1] if ":" in f else f))
    return out


# ---- Maven ------------------------------------------------------------------------------------------------------

def _pom_deps(xml_text: str) -> list[tuple[str, str, str | None]]:
    try:
        tree = ET.fromstring(re.sub(r'\sxmlns="[^"]*"', "", xml_text, count=1))
    except ET.ParseError:
        return []
    out = []
    for d in tree.iter("dependency"):
        g, a, v = (d.findtext(t) for t in ("groupId", "artifactId", "version"))
        if g and a:
            out.append((g.strip(), a.strip(), v.strip() if v else None))
    return out


def _monolith_group(text: str) -> str | None:
    outside_parent = re.sub(r"<parent>.*?</parent>", "", text, flags=re.DOTALL)
    m = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", outside_parent) or \
        re.search(r"<parent>.*?<groupId>\s*([^<\s]+)\s*</groupId>", text, flags=re.DOTALL)
    return m.group(1) if m else None


def base_pom_info(root: Path, base: str) -> tuple[set[tuple[str, str]], dict[tuple[str, str], set[str]], str | None]:
    """What the monolith's own POMs already use: artifacts, their versions, and the monolith's groupId."""
    used: set[tuple[str, str]] = set()
    versions: dict[tuple[str, str], set[str]] = {}
    group = None
    try:
        files = [f for f in _git(root, "ls-tree", "-r", "--name-only", base).splitlines()
                 if Path(f).name == "pom.xml"]
    except RuntimeError:
        return used, versions, group
    for f in files:
        text = _show(root, base, f) or ""
        if f == "pom.xml":
            group = _monolith_group(text)
        for g, a, v in _pom_deps(text):
            used.add((g, a))
            if v:
                versions.setdefault((g, a), set()).add(v)
    return used, versions, group


# ---- The check --------------------------------------------------------------------------------------------------

def check(root: Path, base: str, declaration: dict | None, design: dict | None, ownership: dict[str, dict],
          domain: str, god_modules: list[str] | None = None, extra_artifacts: list[str] | None = None,
          earlier: dict | None = None, wiring: set[str] | None = None) -> list[str]:
    """Violations of the step's limits since `base`. `declaration` is module-extraction.yaml (parsed) and `design` is
    domain-design.yaml (parsed); either may be None while the agent has not written it yet. `extra_artifacts` are
    modules built by an earlier step (groupId:artifactId) that the new modules may depend on. `earlier` holds the
    module folders and bridge packages earlier runs of the domain declared ({"modules": set, "bridges": set}): a
    feature may extend those; any other folder must be new. `wiring` are more class names whose beans the Spring
    wiring may name (the domain module's, for the Orchestrator in monolith step). A declaration without `routing` may
    name the feature's `router` and its legacy `routes` (the Orchestrator in monolith step's view of the bridge)."""
    try:
        diff = changes(root, base)
    except (RuntimeError, OSError) as e:
        return [blocker(f"Could not compare with the starting commit: {e}")]
    if not diff:
        return []
    problems: list[str] = []
    decl_modules = (declaration or {}).get("modules") or []
    modules = [m["path"].strip("/") for m in decl_modules]
    if declaration is None:  # not written yet: the new modules are the folders that gained a pom.xml
        modules = [Path(c.path).parent.as_posix() for c in diff
                   if c.status == "A" and Path(c.path).name == "pom.xml" and Path(c.path).parent.as_posix() != "."]
    bridge = [(declaration or {}).get("bridge_dir", "").strip("/")] if (declaration or {}).get("bridge_dir") else []
    inside = [c for c in diff if _under(c.path, modules)]
    in_bridge = [c for c in diff if _under(c.path, bridge)]
    outside = [c for c in diff if c not in inside and c not in in_bridge]

    # What Spring wiring outside may name: this step's classes, and the beans the router takes over.
    routing = (declaration or {}).get("routing") or {}
    names = {_simple(c.path) for c in [*inside, *in_bridge] if c.path.endswith(".java") and c.status in "AMR"}
    names |= {str(r.get(k)) for r in routing.get("routes") or [] for k in ("bean", "legacy_bean") if r.get(k)}
    names |= set(wiring or ())
    names.discard("package-info")
    names |= {m.group(1) for c in outside if c.path.endswith(".xml") and c.status != "D"  # those classes' bean ids
              for m in NEW_BEAN.finditer(_read(root, c.path) + "\n" + "\n".join(c.removed)) if m.group(2) in names}
    word = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\b") if names else None

    # 1. UI and DDL, anywhere.
    for c in diff:
        for path in {c.path, c.old_path or c.path}:
            if Path(path).suffix.lower() in UI_SUFFIXES and not _is_test(path):
                problems.append(f"UI file changed: {path}. The UI is frozen: JSPs, HTML, JavaScript, CSS and images "
                                "must not change")
        if Path(c.path).suffix.lower() in DDL_SUFFIXES and not _is_test(c.path):
            hit = next((line for line in c.added if DDL.search(line)), None)
            if hit:
                problems.append(f"DDL in {c.path}: `{hit.strip()[:100]}`. The schema is frozen")
        if c.status == "A" and Path(c.path).suffix.lower() in (".sql", ".ddl") and not _is_test(c.path):
            problems.append(f"New SQL script {c.path}. The schema is frozen: no new scripts")

    # 2. Build files outside the new modules: only <module> entries and dependencies on the new modules.
    new_artifacts = {m["artifact"].split(":")[-1] for m in decl_modules if m.get("artifact")}
    for c in outside:
        name = Path(c.path).name
        if name not in BUILD_FILES:
            continue
        if name != "pom.xml":
            problems.append(f"Build file changed: {c.path}. Only pom.xml `<module>` and dependency lines for the new "
                            "modules may change; ask the user for anything else")
            continue
        if c.removed:
            problems.append(f"{c.path}: lines removed or changed (`{c.removed[0].strip()[:80]}`). Versions, "
                            "dependencies and plugins must not change")
        bad = next((line for line in c.added if not POM_LINE.match(line)), None)
        if bad:
            problems.append(f"{c.path}: `{bad.strip()[:80]}` is not a `<module>` entry or a dependency on a new module")
        for g, a, _v in _pom_deps("<project>" + "\n".join(c.added) + "</project>"):
            if new_artifacts and a not in new_artifacts:
                problems.append(f"{c.path}: dependency {g}:{a} is not one of the new modules")

    # 3. The new modules' POMs: allowed dependencies only, no plugins, no Java level.
    used, versions, monolith_group = base_pom_info(root, base)
    utility = {str(u) for u in (design or {}).get("utility_modules", [])}
    own = {tuple(m["artifact"].split(":", 1)) for m in decl_modules if ":" in (m.get("artifact") or "")}
    own |= {tuple(a.split(":", 1)) for a in extra_artifacts or [] if ":" in a}
    for c in inside:
        if Path(c.path).name != "pom.xml" or c.status == "D":
            continue
        text = _read(root, c.path)
        if "<plugin>" in text:
            problems.append(f"{c.path}: declares a plugin. New modules inherit the parent's plugins; ask the user if "
                            "one is really needed")
        if re.search(r"<(?:source|target|release)>|maven\.compiler\.|java\.version", text):
            problems.append(f"{c.path}: sets the Java level. It is inherited from the parent and must not change")
        for g, a, v in _pom_deps(text):
            if (g, a) in own or f"{g}:{a}" in utility or a in new_artifacts:
                continue
            if (g, a) not in used or g == monolith_group:
                why = ("a module of the monolith itself, which a new module must not depend on"
                       if g == monolith_group else "not used by the monolith's POMs")
                problems.append(f"{c.path}: dependency {g}:{a} is {why}. Allowed: the utility modules in "
                                "domain-design.yaml, the new modules, and third-party libraries the monolith uses")
            elif v and not v.startswith("${") and v not in versions.get((g, a), set()):
                problems.append(f"{c.path}: dependency {g}:{a} declares version {v}, which the monolith does not use")

    # 4. The as-is code is unchanged: outside the module and the bridge, only wiring, build and the toggle's default,
    # and the toggle branch in the controllers listed in toggle_branches.
    router = str(routing.get("router") or (declaration or {}).get("router") or "").strip("/")
    branched = {str(b.get("file") or b.get("controller") or "").strip("/")
                for b in (declaration or {}).get("toggle_branches") or []}
    sources = [c for c in outside if Path(c.path).name not in BUILD_FILES and c.path not in branched]
    problems += _toggle_branches(root, base, diff, outside, branched, router)
    prop = routing.get("property")
    for c in [*inside, *in_bridge]:  # git sees a legacy file deleted beside its copy as moved into the module
        if c.status == "R" and not _under(c.old_path or "", [*modules, *bridge]):
            problems.append(f"Legacy {'test' if _is_test(c.old_path) else 'file'} moved: {c.old_path} → {c.path}. The "
                            "as-is code stays where it is (the old flow): copy it into the module, don't move it")
    for c in sources:
        what = "test" if _is_test(c.path) else "file"
        if c.status == "D":
            problems.append(f"Legacy {what} deleted: {c.path}. The as-is code stays as it is (the old flow): the new "
                            "domain is a copy beside it")
            continue
        if c.status == "R":
            problems.append(f"Legacy {what} moved: {c.old_path} → {c.path}. The as-is code stays where it is; copy it "
                            "into the module instead")
            continue
        if _is_test(c.path) and (c.status == "A" or (c.status == "M" and not any(x.strip() for x in c.removed))):
            continue  # a new or extended test changes no behaviour: the legacy callers' tests live beside them
        if c.status == "A":
            problems.append(f"New file outside the module and the bridge: {c.path}. New code belongs in the module, or "
                            "(the router and adapters) in the bridge package")
            continue
        if c.path.endswith(".xml") and statements(_show(root, base, c.path)):
            # Exactly as it was, ids included: the legacy flow runs these, and the copies need their own names.
            if sorted(statements(_read(root, c.path), strict=True)) != \
                    sorted(statements(_show(root, base, c.path), strict=True)):
                problems.append(f"Legacy SQL map changed: {c.path}. Its statements stay as they are; the module has its "
                                "own copies")
            continue
        bad = _not_wiring(c, word, prop)
        if bad:
            problems.append(f"Legacy {what} changed: {c.path}: `{bad.strip()[:90]}`. Outside the module and the bridge "
                            "only Spring wiring and POM lines change (and the toggle's default)")
    changed_outside = {c.path for c in outside  # wiring and the POMs; branched callers and tests are listed elsewhere
                       if c.status == "M" and c.path not in branched and not _is_test(c.path)}
    if declaration is not None:
        listed = set(declaration.get("outside_changes") or [])
        problems += [f"{p} changed outside the modules but is not in outside_changes of module-extraction.yaml"
                     for p in sorted(changed_outside - listed)]
        problems += [f"outside_changes lists {p}, which did not change" for p in sorted(listed - changed_outside)]
    if len(changed_outside | branched) > MAX_OUTSIDE_FILES:
        problems.append(blocker(f"{len(changed_outside | branched)} files outside the modules need changing (the limit is "
                        f"{MAX_OUTSIDE_FILES}). Stop and ask the user before going on"))

    # 5. SQL: every statement in the modules is a copy, text for text, of one in the monolith.
    module_stmts: list[str] = []
    for c in inside:
        if c.path.endswith(".xml") and c.status != "D":
            module_stmts += statements(_read(root, c.path))
    pool = _base_statements(root, base)
    for s in module_stmts:
        if s in pool:
            pool.remove(s)
        else:
            problems.append(f"SQL in a module is new or differs from the monolith's: {s[:100]}… A copied statement "
                            "keeps its SQL, mappings and dynamic tags exactly as they are; only names may change to "
                            "keep them unique beside the original (statement, resultMap, parameterMap and sql ids and "
                            "the references to them, type aliases, and the package of copied classes)")
            break

    # 5b. The parallel run (Module in monolith): own folders, the bridge holds the router and adapters, the toggle.
    routes = routing.get("routes") or (declaration or {}).get("routes") or []
    if bridge and router:
        problems += _bridge_classes(root, bridge[0], inside, in_bridge, routes, router)
    if routing:
        problems += _own_folders(root, base, diff, modules, bridge, earlier or {})
        if bridge and not _under(router, bridge):
            problems.append(f"The router {router} is not in the bridge package {bridge[0]}")
        if not any(c.path == router and c.status != "D" for c in diff) and not (root / router).is_file():
            problems.append(f"The router {router} (routing in module-extraction.yaml) doesn't exist: it takes over the "
                            "legacy service's bean and reads the toggle")
        if not any(prop in line for c in diff if c.status != "D" for line in c.added):
            problems.append(f"The toggle {prop} is not read or configured anywhere in this step's change")
        default = re.compile(rf"^\s*{re.escape(prop)}\s*[:=]\s*[\"']?(\S+?)[\"']?\s*$")
        for c in diff:
            if c.path.endswith((".properties", ".yml", ".yaml")) and c.status != "D":
                for line in c.added:
                    m = default.match(line)
                    if m and m.group(1) != "legacy":
                        problems.append(f"{c.path} sets {prop} to {m.group(1)}. It defaults to legacy (the as-is code)")

    # 6. The modules touch only this domain's tables and packages, and no God module.
    for m in modules:
        if (root / m).is_dir():
            problems += [f"Boundary: {v}" for v in boundary_check.check(root, domain, m, ownership)]
    gods = [g for g in (god_modules or []) if g]
    for c in [*inside, *in_bridge]:
        if not gods or c.status == "D" or not c.path.endswith((".java", ".xml")):
            continue
        text = _read(root, c.path)
        hit = next((g for g in gods if g in text or re.search(rf"\b{re.escape(g.split('.')[-1])}\b", text)), None)
        if hit:
            problems.append(f"{c.path} uses the God module {hit}. New modules and the bridge must not depend on one: "
                            "split what this feature needs, or ask the user")

    # 6b. No ambient inputs in the modules: the session, request, user and thread-locals come through context ports.
    for c in inside:
        if c.path.endswith(".java") and c.status != "D" and not _is_test(c.path):
            hit = AMBIENT.search(_read(root, c.path))
            if hit:
                problems.append(f"{c.path} uses {hit.group(0)}. The modules never read the session, request, user or a "
                                "thread-local directly: use a context port from domain-design.yaml, with its adapter "
                                "in the bridge package")
    # 7. Scope: a domain-only feature has no orchestrator.
    if declaration and declaration.get("scope") == "domain-only":
        problems += [f"Module {m['path']}: the feature is domain-only, so there is no orchestrator"
                     for m in decl_modules if m.get("kind") == "orchestrator"]
    # 8. Tests are never ignored.
    for c in diff:
        if _is_test(c.path) and c.status != "D":
            hit = next((line for line in c.added if re.search(r"@Ignore|@Disabled|\bassume(?:True|False)\(", line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be ignored (`{hit.strip()[:60]}`)")
    return problems[:MAX_REPORTED]


def _not_wiring(c: Change, word: re.Pattern | None, prop: str | None) -> str | None:
    """The first changed line of a legacy file that is not Spring wiring for this step (an import, alias or SQL map
    registration, or a bean line naming this step's classes or the beans the router takes over), nor the toggle's
    default in a properties file. None when every changed line is one of those."""
    config = c.path.endswith((".properties", ".yml", ".yaml"))
    for line in [*c.removed, *c.added]:
        if not line.strip():
            continue
        if c.path.endswith(".xml") and (WIRING.search(line) or (word is not None and word.search(line)
                                                               and BEAN_LINE.search(line))
                                        or (line in c.added and CLOSING.match(line))):
            continue
        if config and prop and prop in line and line in c.added:
            continue
        return line
    return None


def _own_folders(root: Path, base: str, diff: list[Change], modules: list[str], bridge: list[str],
                 earlier: dict) -> list[str]:
    """The module folders and the bridge package are new in this step, or this domain's own from an earlier feature:
    an existing folder can't be declared as one (everything in it would count as the step's)."""
    def existed(folder: str) -> bool:
        try:
            return bool(_git(root, "ls-tree", "-r", "--name-only", base, "--", folder).strip())
        except RuntimeError:
            return False

    problems = []
    for m in modules:
        if m not in (earlier.get("modules") or set()) and existed(m):
            problems.append(f"The module folder {m} already existed. The domain module is a new folder (or this "
                            "domain's own, from an earlier feature)")
    for b in bridge:
        if b not in (earlier.get("bridges") or set()) and existed(b):
            problems.append(f"The bridge package {b} already existed. It is a new package for the router and adapters "
                            "(or this domain's own, from an earlier feature)")
    return problems


def _bridge_classes(root: Path, bridge: str, inside: list[Change], in_bridge: list[Change], routes: list[dict],
                    router: str) -> list[str]:
    """Every class added to the bridge package is the router, an adapter (it implements the legacy interface the router
    takes over, or a port of the domain module), or a helper such as a mapper that the router or an adapter uses and
    that has no database access."""
    ports = set()
    for c in inside:
        if c.path.endswith(".java") and c.status != "D":
            ports |= set(re.findall(r"\binterface\s+(\w+)", _read(root, c.path)))
    ports |= {str(r["interface"]).split(".")[-1] for r in routes if r.get("interface")}

    def implemented(text: str) -> set[str]:
        return {n.split(".")[-1] for m in re.findall(r"\bimplements\s+([\w.,\s<>]+?)\s*\{", text)
                for n in re.split(r"[,\s<>]+", m) if n}

    folder = root / bridge
    classes = {f.relative_to(root).as_posix(): COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace"))
               for f in folder.rglob("*.java")} if folder.is_dir() else {}
    anchors = [text for path, text in classes.items()  # the router and the adapters: what helpers may serve
               if path == router or (not _is_test(path) and implemented(text) & ports)]
    problems = []
    for c in in_bridge:
        if c.status != "A" or not c.path.endswith(".java") or _is_test(c.path) or _simple(c.path) == "package-info":
            continue
        text = classes.get(c.path, "")
        if c.path == router or implemented(text) & ports:
            continue
        name = re.compile(rf"\b{re.escape(_simple(c.path))}\b")
        if not any(name.search(a) for a in anchors if a is not text):
            problems.append(f"{c.path} in the bridge package is not the router, implements no port of the domain "
                            "module nor the legacy interface the router takes over, and neither the router nor an "
                            "adapter uses it. The bridge holds only the router, adapters and their helpers (mappers)")
            continue
        hit = DB_ACCESS.search(text) or TRANSACTION.search(text)
        if hit:
            problems.append(f"{c.path} uses {hit.group(0).strip()}. A helper in the bridge package only converts "
                            "between the legacy and the module's types: no database access, no transactions")
    return problems


def _toggle_branches(root: Path, base: str, diff: list[Change], outside: list[Change], branched: set[str],
                     router: str) -> list[str]:
    """The legacy callers listed in toggle_branches (no bean seam for the router to sit behind) may change as much as
    the toggle branch needs (lines added, moved or rewritten) because a test pins their behaviour, not their text. What
    no test of the caller would show stays refused: no branch asking the router, new database access, a DAO."""
    problems = []
    by_path = {c.path: c for c in outside}
    router_class = re.compile(rf"\b{re.escape(_simple(router))}\b") if router else None
    tests = [_read(root, c.path) for c in diff if _is_test(c.path) and c.status in "AMR"]
    for path in sorted(branched):
        c = by_path.get(path)
        if c is None:
            problems.append(f"toggle_branches lists {path}, which did not change")
            continue
        if c.status != "M" or not path.endswith(".java") or _is_test(path):
            problems.append(f"toggle_branches lists {path}: only an existing Java class (not a test) gets a toggle "
                            "branch")
            continue
        before = _show(root, base, path) or ""
        if DATA_LAYER.search(before):
            problems.append(f"toggle_branches lists {path}, a DAO. The data access stays as it is: the module has its "
                            "own copy, and the router switches the callers")
            continue
        added = COMMENTS.sub("", "\n".join(c.added))
        if router_class is not None and not router_class.search(added):
            problems.append(f"{path}: the toggle branch does not ask the router {_simple(router)}. The router reads "
                            "the toggle and calls the new flow; the caller only asks it")
        old = COMMENTS.sub("", before)
        hit = next((m.group(0) for p in (DB_ACCESS, TRANSACTION) for m in p.finditer(added)
                    if m.group(0) not in old), None)
        if hit:
            problems.append(f"{path}: the change adds {hit.strip()}. The branch only asks the router: no new "
                            "database access or transactions in legacy code")
        name = re.compile(rf"\b{re.escape(_simple(path))}\b")
        if not any(name.search(t) for t in tests):
            problems.append(f"{path}: no test in this change covers {_simple(path)}. Add or extend one that pins its "
                            "behaviour with the toggle at legacy and at monolith (and service, once it exists); the "
                            "refactoring around the branch is then free")
    return problems


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run: it compares the code with the commit the step started from, using the
    declaration and the designs in the step's documents."""
    from app import contracts_store as cs

    if not ctx.base_sha:
        return [blocker("The step's starting commit is unknown, so the changes cannot be checked: is the code a git checkout?")]
    out = ctx.output_dir

    def load(name, parse):
        f = out / name
        return parse(f.read_text(encoding="utf-8")) if f.is_file() else None

    try:
        declaration = load(cs.MODULE_EXTRACTION_FILE, cs.parse_module_extraction)
        design = load(cs.DOMAIN_DESIGN_FILE, cs.parse_domain_design)
        finder = load(cs.DOMAIN_DEPENDENCIES_FILE, cs.load_domain_dependencies)
    except cs.InvalidDocument as e:
        return [str(e)]
    if declaration is None:
        return [f"Write {cs.MODULE_EXTRACTION_FILE} (in {ctx.rel(out)}/) first: it declares the new modules, which the "
                "check needs"]
    root = (ctx.workspace / ctx.code_dir).resolve()
    return check(root, ctx.base_sha, declaration, design, ctx.ownership, ctx.domain, (finder or {}).get("god_modules"),
                 earlier=earlier_folders(ctx.run_id, ctx.domain)) + one_per_domain(ctx, root, "domain", next(
                     (str(m["path"]) for m in declaration["modules"] if m["kind"] == "domain"), ""), declaration)


def one_per_domain(ctx, root: Path, kind: str, declared: str, declaration: dict) -> list[str]:
    """The domain's one module of `kind`, with one package per feature (app/domain_modules.py)."""
    from app import domain_modules
    from app.db.models import Run
    from app.db.session import session_scope

    try:
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            project = run.project if run is not None else None
    except Exception:  # noqa: BLE001 - without the database there are no earlier features to know
        project = None
    if project is None:
        return []
    return (domain_modules.problems(project, ctx.domain, ctx.run_id, root, kind, declared)
            + domain_modules.package_problems(project, ctx.domain, ctx.operation, ctx.run_id, root, ctx.base_sha, kind,
                                              declared, declaration))


def declarations(run_id: str, domain: str, name: str) -> list[str]:
    """The texts of a declaration (`name`) the runs of the domain stored, this run's earlier attempts included."""
    from sqlalchemy import select

    from app.db.models import Artifact, Run
    from app.db.session import session_scope

    try:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                return []
            return [a.content for a in s.scalars(select(Artifact).join(Run, Run.id == Artifact.run_id).where(
                Run.project == run.project, Run.domain == domain, Artifact.name == name))]
    except Exception:  # noqa: BLE001 - without the database there is nothing earlier to know
        return []


def earlier_folders(run_id: str, domain: str) -> dict:
    """The module folders and bridge packages declared by runs of the domain (this one's earlier attempts included):
    a later feature, or a re-run, extends them instead of creating new ones."""
    from app import contracts_store as cs

    out = {"modules": set(), "bridges": set()}
    for text in declarations(run_id, domain, cs.MODULE_EXTRACTION_FILE):
        try:
            data = cs.parse_module_extraction(text)
        except cs.InvalidDocument:
            continue
        out["modules"] |= {str(m["path"]).strip("/") for m in data["modules"]}
        if data.get("bridge_dir"):
            out["bridges"].add(str(data["bridge_dir"]).strip("/"))
    return out


def report(problems: list[str]) -> str:
    if not problems:
        return "Module guardrails: PASS. Nothing outside the allowed changes."
    return ("Module guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
