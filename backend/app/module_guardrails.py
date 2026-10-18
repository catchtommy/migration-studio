"""Guardrails of the Module in monolith step, checked from git rather than trusted from the agent's word.

The step extracts one feature into new Maven modules (a domain module and, for cross-domain features, an orchestrator
module) and re-points the callers. Compared with the commit the step started from, only these changes may exist:

- inside the new modules: anything, except what the checks below forbid (other domains' tables and packages, God
  modules, dependencies beyond the allow-list, SQL that is not copied from the monolith);
- in the legacy bridge package (cross-domain features only): temporary adapters and bindings;
- elsewhere: call-site edits (imports, declarations and bean references of moved classes), the wiring lines that plug
  the modules in, and tests moved with their code;
- nowhere: UI files, DDL, build or version changes beyond the `<module>` and dependency lines for the new modules,
  changed or removed assertions of existing tests.

`check()` returns one line per violation, empty when the step is within its limits.
"""

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app import boundary_check

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
CALL = re.compile(r"[\w)\]]\s*\.\s*\w+\s*\(")
CONTROL = re.compile(r"\b(?:return|if|else|for|while|switch|case|throw|try|catch|assert|synchronized)\b")
IMPORT = re.compile(r"^\s*(?:import|package)\s+(?:static\s+)?[\w.]+(?:\.\*)?\s*;\s*$")
INJECTION = re.compile(r"^\s*@(?:Autowired|Resource|Qualifier|Inject|Named)(?:\s*\([^)]*\))?\s*$")
WIRING = re.compile(r"<\s*(?:import\s+resource|sqlMap\s+resource|alias\s+name)", re.IGNORECASE)
BEAN_LINE = re.compile(r"<\s*/?\s*(?:bean|property|constructor-arg|ref|value)\b", re.IGNORECASE)
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
        _git(root, "add", "-A", "--", ".", ":!.migration", ":!.studio", env=env)
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

def statements(xml_text: str | None) -> list[str]:
    """Canonical text of every statement in an iBatis SQL map: whitespace collapsed and its id left out, so a statement
    moved to another file or namespace compares equal, and any change to its SQL does not."""
    if not xml_text or "<sqlMap" not in xml_text:
        return []
    try:
        tree = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xml_text, flags=re.DOTALL))
    except ET.ParseError:
        return []
    out = []
    for el in tree.iter():
        if el.tag in STATEMENT_TAGS:
            text = re.sub(r'\sid="[^"]*"', "", ET.tostring(el, encoding="unicode"), count=1)
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
          domain: str, god_modules: list[str] | None = None) -> list[str]:
    """Violations of the step's limits since `base`. `declaration` is module-extraction.yaml (parsed) and `design` is
    domain-design.yaml (parsed); either may be None while the agent has not written it yet."""
    try:
        diff = changes(root, base)
    except (RuntimeError, OSError) as e:
        return [f"Could not compare with the starting commit: {e}"]
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

    # Names the step moved: classes now in the modules and classes that no longer exist.
    moved = {_simple(c.path) for c in inside if c.path.endswith(".java") and c.status in "AR"}
    moved |= {_simple(c.path) for c in diff if c.path.endswith(".java") and c.status == "D"}
    moved |= {_simple(c.old_path) for c in diff if c.status == "R" and (c.old_path or "").endswith(".java")}
    moved.discard("package-info")
    word = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(moved))) + r")\b") if moved else None

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

    # 4. Call sites and wiring: outside the modules only mechanical edits.
    sources = [c for c in outside if Path(c.path).name not in BUILD_FILES]
    for c in sources:
        if c.status == "D":
            if _is_test(c.path):
                problems.append(f"Existing test deleted: {c.path}. Move it into the module with its code instead")
            continue
        if c.status == "A":
            problems.append(f"New file outside the modules: {c.path}. New code belongs in a module or in the legacy "
                            "bridge package")
            continue
        if c.path.endswith(".xml") and statements(_show(root, base, c.old_path or c.path)):
            continue  # an SQL map: its statements are compared by text in step 5
        bad = _non_mechanical(c, word)
        if bad:
            what = "Existing test changed" if _is_test(c.path) else "Edit outside the modules"
            problems.append(f"{what}: {c.path}: `{bad.strip()[:90]}` is more than an import, declaration or bean "
                            "reference of a moved class")
    for c in inside:  # a test moved with its code may change only package and import lines
        if _is_test(c.path) and c.status == "R":
            bad = next((line for line in [*c.removed, *c.added] if line.strip() and not IMPORT.match(line)), None)
            if bad:
                problems.append(f"Moved test changed beyond package/imports: {c.path}: `{bad.strip()[:90]}`")
    changed_outside = {c.path for c in sources if c.status == "M"}
    if declaration is not None:
        listed = set(declaration.get("outside_changes") or [])
        problems += [f"{p} changed outside the modules but is not in outside_changes of module-extraction.yaml"
                     for p in sorted(changed_outside - listed)]
        problems += [f"outside_changes lists {p}, which did not change" for p in sorted(listed - changed_outside)]
    if len(changed_outside) > MAX_OUTSIDE_FILES:
        problems.append(f"{len(changed_outside)} files outside the modules need changing (the limit is "
                        f"{MAX_OUTSIDE_FILES}). Stop and ask the user before going on")

    # 5. SQL: every statement in the modules exists, text for text, in the monolith; every statement taken out of a
    # legacy SQL map is now in a module; no SQL map outside the modules gains or changes a statement.
    module_stmts: list[str] = []
    for c in inside:
        if c.path.endswith(".xml") and c.status != "D":
            module_stmts += statements(_read(root, c.path))
    pool = _base_statements(root, base)
    for s in module_stmts:
        if s in pool:
            pool.remove(s)
        else:
            problems.append(f"SQL in a module is new or differs from the monolith's: {s[:100]}…")
            break
    in_modules = list(module_stmts)
    for c in diff:
        before = statements(_show(root, base, c.old_path or c.path)) if c.status != "A" else []
        if not before or c in inside:
            continue
        after = statements(_read(root, c.path)) if c.status in "M" else []
        for s in after:
            if s in before:
                before.remove(s)
            else:
                problems.append(f"SQL changed in {c.path}: `{s[:100]}…`. SQL text must stay identical")
                break
        for s in before:  # taken out of this file
            if s in in_modules:
                in_modules.remove(s)
            else:
                problems.append(f"A statement was removed from {c.path} without being copied into a module: {s[:90]}…")
                break

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


def _non_mechanical(c: Change, word: re.Pattern | None) -> str | None:
    """The first changed line that is not an import, a declaration, an injection annotation or a wiring line that
    mentions a moved class (None when every line is mechanical)."""
    xml = c.path.endswith(".xml")
    for line in [*c.removed, *c.added]:
        if not line.strip():
            continue
        if xml:
            if WIRING.search(line) or (word is not None and word.search(line) and BEAN_LINE.search(line)):
                continue
        else:
            if IMPORT.match(line) or INJECTION.match(line):
                continue
            if word is not None and word.search(line) and not CALL.search(line) and not CONTROL.search(line):
                continue
        return line
    return None


def for_context(ctx) -> list[str]:
    """Run the check for the agent's run: it compares the code with the commit the step started from, using the
    declaration and the designs in the step's documents."""
    from app import contracts_store as cs

    if not ctx.base_sha:
        return ["The step's starting commit is unknown, so the changes cannot be checked: is the code a git checkout?"]
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
    return check((ctx.workspace / ctx.code_dir).resolve(), ctx.base_sha, declaration, design, ctx.ownership,
                 ctx.domain, (finder or {}).get("god_modules"))


def report(problems: list[str]) -> str:
    if not problems:
        return "Module guardrails: PASS. Nothing outside the allowed changes."
    return ("Module guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending step instead of working around it, or ask the user if the rule blocks you.")
