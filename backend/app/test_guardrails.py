"""Guardrails of the test steps, checked from git rather than trusted from the agent's word: they only add tests,
and only where they may, in the domain service's repository.

- `acceptance_test_guardrails` (Independent tests): changes only in `acceptance-tests/`, the black-box acceptance
  suite (a Maven project of its own): it never imports the service's packages or depends on the service's build, so
  it runs unchanged before and after modernisation.
- `acceptance_suite_unchanged` (Modernisation): nothing under `acceptance-tests/` changes; the suite is the proof
  that the rewrite kept behaviour.
- `service_test_guardrails` (Service tests): changes only in test sources and resources (`src/test/`) and, in POMs,
  added dependencies with `<scope>test</scope>`; no production code, configuration or acceptance suite.

All of them: the monolith repository is untouched, the step works in another repository, and no test is disabled.
`for_context()` runs the ones the agent is configured with; each problem is one line.
"""

import re
from pathlib import Path

from app import module_guardrails as mg
from app.guardrail_problems import blocker

ACCEPTANCE_DIR = "acceptance-tests"
DISABLED = re.compile(r"@Ignore|@Disabled|\bassume(?:True|False)\(")
DEPENDENCY = re.compile(r"<dependency>.*?</dependency>", re.DOTALL)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*([^<\s]+)\s*</{tag}>", block)
    return m.group(1) if m else ""


def _changes(service: Path, base: str) -> list[mg.Change]:
    return mg.changes(service, base)


def _paths(c: mg.Change) -> set[str]:
    return {c.path, c.old_path or c.path}


def _common(service: Path, diff: list[mg.Change], monolith: Path | None, monolith_base: str | None) -> list[str]:
    problems = []
    if monolith is not None and monolith_base:
        try:
            touched = mg.changes(monolith, monolith_base)
        except (RuntimeError, OSError):
            touched = []
        problems += [f"The monolith repository changed: {c.path}. This step changes only the domain service's "
                     "repository" for c in touched[:5]]
    for c in diff:
        if c.status != "D" and c.path.endswith(".java"):
            hit = next((line for line in c.added if DISABLED.search(line)), None)
            if hit:
                problems.append(f"{c.path}: tests must not be disabled or ignored (`{hit.strip()[:60]}`)")
    return problems


def acceptance(service: Path, base: str, monolith: Path | None, monolith_base: str | None,
               service_packages: list[str]) -> list[str]:
    """The Independent tests step: only the black-box acceptance suite, in its own folder and build."""
    diff = _changes(service, base)
    problems = _common(service, diff, monolith, monolith_base)
    for c in diff:
        outside = sorted(p for p in _paths(c) if not mg._under(p, [ACCEPTANCE_DIR]))
        if outside:
            problems.append(f"{outside[0]} is outside {ACCEPTANCE_DIR}/. This step only adds the acceptance suite; "
                            "the service's code, build and tests are not changed")
    pom = service / ACCEPTANCE_DIR / "pom.xml"
    if pom.is_file():
        text = pom.read_text(encoding="utf-8", errors="replace")
        root = service / "pom.xml"
        own = (_field(re.sub(r"<parent>.*?</parent>|<dependencies>.*", "", root.read_text(encoding="utf-8",
                                                                                         errors="replace"),
                             flags=re.DOTALL), "artifactId") if root.is_file() else "")
        parent = re.search(r"<parent>(.*?)</parent>", text, re.DOTALL)
        if parent and (own and _field(parent.group(1), "artifactId") == own or "<relativePath>.." in parent.group(1)):
            problems.append(f"{ACCEPTANCE_DIR}/pom.xml inherits from the service's POM. The suite is a project of its "
                            "own, so it builds the same before and after modernisation")
        for block in DEPENDENCY.findall(text):
            if own and _field(block, "artifactId") == own:
                problems.append(f"{ACCEPTANCE_DIR}/pom.xml depends on the service ({own}). The suite is black-box: it "
                                "calls the running service over HTTP")
    else:
        problems.append(f"Write the acceptance suite as a Maven project in {ACCEPTANCE_DIR}/ (its own pom.xml)")
    for c in diff:
        if c.status == "D" or not c.path.endswith(".java") or not mg._under(c.path, [ACCEPTANCE_DIR]):
            continue
        text = mg._read(service, c.path)
        hit = next((p for p in service_packages if p and re.search(rf"^\s*import\s+(?:static\s+)?{re.escape(p)}\b",
                                                                  text, re.M)), None)
        if hit:
            problems.append(f"{c.path} imports {hit}, the service's code. The suite is black-box: it reaches the "
                            "service only over HTTP and the database over JDBC")
    return problems[:mg.MAX_REPORTED]


def suite_unchanged(service: Path, base: str) -> list[str]:
    """The Modernisation step: the acceptance suite stays exactly as it was."""
    changed = sorted({p for c in _changes(service, base) for p in _paths(c) if mg._under(p, [ACCEPTANCE_DIR])})
    return [f"{p} changed. The acceptance suite is the proof that behaviour is unchanged: run it as it is, and never "
            "change it here" for p in changed][:mg.MAX_REPORTED]


def _pom_problems(service: Path, base: str, c: mg.Change) -> list[str]:
    """A POM may only gain dependencies with <scope>test</scope>; nothing else in it changes."""
    if c.status in ("D", "R"):
        return [f"{c.path} was {'deleted' if c.status == 'D' else 'moved'}. Only test-scoped dependencies may be added"]
    old, new = mg._show(service, base, c.path) or "", mg._read(service, c.path)
    old_deps, new_deps = DEPENDENCY.findall(old), DEPENDENCY.findall(new)
    problems = []
    removed = [b for b in old_deps if _norm(b) not in {_norm(n) for n in new_deps}]
    if removed:
        problems.append(f"{c.path}: dependency {_field(removed[0], 'groupId')}:{_field(removed[0], 'artifactId')} "
                        "was removed or changed. Only test-scoped dependencies may be added")
    for block in (b for b in new_deps if _norm(b) not in {_norm(o) for o in old_deps}):
        if _field(block, "scope") != "test":
            problems.append(f"{c.path}: dependency {_field(block, 'groupId')}:{_field(block, 'artifactId')} is not "
                            "<scope>test</scope>. Tests may not add what the service ships with")
    if _norm(DEPENDENCY.sub("", old)) != _norm(DEPENDENCY.sub("", new)):
        problems.append(f"{c.path}: more than dependencies changed. Only test-scoped dependencies may be added")
    return problems


def service_tests(service: Path, base: str, monolith: Path | None, monolith_base: str | None) -> list[str]:
    """The Service tests step: test sources and resources, and test-scoped dependencies, only."""
    diff = _changes(service, base)
    problems = _common(service, diff, monolith, monolith_base)
    for c in diff:
        paths = _paths(c)
        if any(mg._under(p, [ACCEPTANCE_DIR]) for p in paths):
            problems.append(f"{c.path} is part of the acceptance suite, which this step does not change")
        elif all("/src/test/" in "/" + p for p in paths):
            continue
        elif Path(c.path).name == "pom.xml":
            problems += _pom_problems(service, base, c)
        else:
            problems.append(f"{c.path} is production code or configuration. This step only adds tests (under "
                            "src/test/) and test-scoped dependencies")
    return problems[:mg.MAX_REPORTED]


def for_context(ctx, checks: list[str] | None = None) -> list[str]:
    """Run the test checks the agent is configured with (or `checks`) for its run."""
    from app.agents.defaults import TEST_CHECKS

    wanted = [c for c in (checks if checks is not None else ctx.config.get("checks") or []) if c in TEST_CHECKS]
    if not wanted:
        return []
    if ctx.code_dir in (".", ""):
        return [blocker("This step must change code in the domain service's repository (the step's Changes code setting: In "
                "another repository), never in the monolith")]
    if not ctx.base_sha:
        return [blocker("The service repository's starting commit is unknown, so the changes cannot be checked")]
    service = (ctx.workspace / ctx.code_dir).resolve()
    monolith = ctx.workspace if mg.head(ctx.workspace) is not None else None
    monolith_base = getattr(ctx, "monolith_sha", None)
    try:
        problems = []
        if "acceptance_test_guardrails" in wanted:
            packages = list((ctx.ownership.get(ctx.domain) or {}).get("packages") or [])
            problems += acceptance(service, ctx.base_sha, monolith, monolith_base, packages)
        if "acceptance_suite_unchanged" in wanted:
            problems += suite_unchanged(service, ctx.base_sha)
        if "service_test_guardrails" in wanted:
            from app import domain_modules
            problems += service_tests(service, ctx.base_sha, monolith, monolith_base)
            problems += domain_modules.for_service(ctx, "service", None, one_project=False)
    except (RuntimeError, OSError) as e:
        return [blocker(f"Could not compare with the starting commit: {e}")]
    return problems


def report(problems: list[str]) -> str:
    if not problems:
        return "Test guardrails: PASS. Only tests changed, where this step may change them."
    return ("Test guardrails: FAIL\n" + "\n".join(f"- {p}" for p in problems) +
            "\nRevert the offending change instead of working around it, or ask the user if the rule blocks you.")
