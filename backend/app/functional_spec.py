"""The functional specification's ids, and how the migration covers them.

Analysis writes three documents from one trace: `as-is.md` (architects and developers), `functional-spec.md` (business
users and analysts) and `test-scenarios.md` (the test team). Ids join them, so nothing is written twice: functional
requirements FR-001, business rules BR-001, use cases UC-01, messages MSG-01, scenarios TS-001. A rule's code location
is only in `as-is.md` ("Rule enforcement", "Messages"), its business wording only in the specification.

- analysis_problems: what Analysis must fix before it may finish (the specification's sections, the ids the three
  documents share, every requirement and rule covered by a scenario). Required.
- allocation_problems: Modulith design's "Functional allocation" table lists every id once, with its owner. Required.
- wording_problems: technical words in the specification. Reported only.
- matrix: the feature's coverage, merged from the "Functional coverage" tables the later steps write, each listing only
  the ids it covers (a step can't cover the whole specification: the orchestrator's ids aren't the domain service's).
  Reported only, at the gates: a person judges the gaps.
"""

import re
from pathlib import Path

SPEC_FILE = "functional-spec.md"
AS_IS_FILE = "as-is.md"
SCENARIOS_FILE = "test-scenarios.md"
DESIGN_FILE = "domain-design.md"

ID = re.compile(r"\b(FR-\d{3}|BR-\d{3}|UC-\d{2}|MSG-\d{2}|TS-\d{3})\b")
_LOOSE_ID = re.compile(r"^(FR|BR|UC|MSG|TS)-\w+$")
KINDS = ("FR", "BR", "MSG")  # the ids the later steps cover

# The specification's numbered sections (its format: shared text functional_spec_format).
SPEC_SECTIONS = {1: "Purpose and business context", 2: "Scope", 3: "Users and roles", 4: "Business process",
                 5: "Functional requirements", 6: "Business rules", 7: "Use cases",
                 8: "Information captured and shown", 9: "Messages and outcomes",
                 10: "Notifications, reports and records", 11: "Business expectations",
                 12: "Assumptions and open questions", 13: "Glossary"}
SCENARIO_SECTIONS = ("Summary", "Scenarios", "Not covered")

# The steps that report coverage: the document each writes it in, the owners of the ids it covers, and which kinds of
# id (Module in monolith pins rules and messages, not requirements; Monolith integration maps the messages back).
COVERAGE_STEPS = {
    "modular_monolith": ("modular-monolith.md", ("domain",), ("BR", "MSG")),
    "microservice_scaffold": ("microservice.md", ("domain",), ("BR", "MSG")),
    "monolith_orchestrator": ("orchestrator-module.md", ("orchestrator",), ("FR", "BR", "MSG")),
    "orchestrator_service": ("orchestrator-service.md", ("orchestrator",), ("FR", "BR", "MSG")),
    "independent_tests": ("test-report.md", ("domain",), ("FR", "BR", "MSG")),
    "orchestrator_tests": ("orchestrator-test-report.md", ("orchestrator",), ("FR", "BR", "MSG")),
    "modernise_and_test": ("modernisation.md", ("domain",), ("FR", "BR", "MSG")),
    "monolith_integration": ("integration.md", ("domain", "orchestrator"), ("MSG",)),
}
# The steps that lift an earlier step's code, with its tests, into a service: once the service exists, the evidence
# runs there. Its own table replaces the earlier step's; without one (a run from before it wrote one), the earlier
# step's tests are shown as carried into the service.
LIFTED_BY = {"modular_monolith": "microservice_scaffold", "monolith_orchestrator": "orchestrator_service"}
# Each owner's black-box acceptance suite: a requirement (FR) counts as covered only when its owner's suite proves it
# (rules and messages: any step of their owner). Reported, never blocking.
ACCEPTANCE = {"domain": "independent_tests", "orchestrator": "orchestrator_tests"}
# as-is.md's section of the messages (each MSG id with where it is produced); "Error paths" in earlier runs.
MESSAGES_SECTIONS = ("messages", "error paths")
OWNERS = ("domain", "orchestrator", "other domain", "ui unchanged")
_NOT_GAPS = ("other domain", "ui unchanged")


def kind(id_: str) -> str:
    return id_.split("-")[0]


def _sections(text: str, level: int = 2) -> list[tuple[str, list[str]]]:
    """(heading, lines) of each section at this heading level (and the text before the first, under "")."""
    out: list[tuple[str, list[str]]] = [("", [])]
    mark = "#" * level + " "
    for line in text.splitlines():
        if line.startswith(mark):
            out.append((line[len(mark):].strip(), []))
        else:
            out[-1][1].append(line)
    return out


def _section(text: str, title: str) -> list[str] | None:
    """The lines under the heading (any level) that contains `title` (case-insensitive), up to the next heading of the
    same or a higher level; None when there is no such heading."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and title.lower() in m.group(2).lower():
            level = len(m.group(1))
            body = []
            for nxt in lines[i + 1:]:
                h = re.match(r"^(#{1,6})\s", nxt)
                if h and len(h.group(1)) <= level:
                    break
                body.append(nxt)
            return body
    return None


def _cells(line: str) -> list[str] | None:
    """A Markdown table row's cells; None for a line that isn't one (or is its |---| separator)."""
    line = line.strip()
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip("|").split("|")]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return None
    return cells


def _row_ids(lines: list[str]) -> list[tuple[str, list[str]]]:
    """(id, cells) of the table rows whose first cell is an id (the rows that define or list one)."""
    out = []
    for line in lines:
        cells = _cells(line)
        if cells and cells[0]:
            first = cells[0].strip("`* ")
            if ID.fullmatch(first) or _LOOSE_ID.match(first):
                out.append((first, cells))
    return out


def _spec_section(text: str, number: int) -> list[str]:
    for heading, lines in _sections(text):
        if re.match(rf"^{number}\.\s", heading) or re.match(rf"^{number}\s", heading):
            return lines
    return []


def spec_ids(spec: str) -> dict[str, list[str]]:
    """The ids the specification defines: FR in section 5, BR in 6, UC in 7 (its headings), MSG in 9, in order."""
    found = {"FR": [i for i, _ in _row_ids(_spec_section(spec, 5)) if kind(i) == "FR"],
             "BR": [i for i, _ in _row_ids(_spec_section(spec, 6)) if kind(i) == "BR"],
             "MSG": [i for i, _ in _row_ids(_spec_section(spec, 9)) if kind(i) == "MSG"],
             "UC": [m.group(1) for line in _spec_section(spec, 7)
                    if (m := re.match(r"^#{3,4}\s+(UC-\d{2})\b", line))]}
    return found


def scenarios(text: str) -> dict[str, dict]:
    """The scenarios of test-scenarios.md: id -> {"covers": [ids], "expected": bool}, in order."""
    out: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^#{3,4}\s+(TS-\d{3})\b", line)
        if m:
            current = m.group(1)
            out.setdefault(current, {"covers": [], "expected": False, "count": 0})
            out[current]["count"] += 1
            continue
        if current is None:
            continue
        stripped = line.strip().lstrip("-* ").strip()
        if re.match(r"^covers\s*:", stripped, re.I):
            out[current]["covers"] += [i for i in ID.findall(stripped) if i not in out[current]["covers"]]
        elif re.match(r"^expected result\s*:", stripped, re.I) and stripped.split(":", 1)[1].strip():
            out[current]["expected"] = True
        elif re.match(r"^expected result\s*:", stripped, re.I):
            out[current]["expected"] = "next"
        elif out[current]["expected"] == "next" and stripped:
            out[current]["expected"] = True
    return out


def _messages_section(as_is: str) -> list[str] | None:
    return next((s for title in MESSAGES_SECTIONS if (s := _section(as_is, title)) is not None), None)


def _enforcement(as_is: str) -> dict[str, dict]:
    """as-is.md's rule enforcement and message rows: id -> {"where": "ui" | "server" | ""}. Only a rule's row says
    where it is enforced (its second column); a message's second column is something else (its kind)."""
    out = {}
    for id_, cells in _row_ids(_section(as_is, "rule enforcement") or []):
        where = cells[1].lower() if len(cells) > 1 else ""
        out[id_] = {"where": "ui" if where.startswith("ui") else "server" if "server" in where else ""}
    for id_, _cells in _row_ids(_messages_section(as_is) or []):
        out.setdefault(id_, {"where": ""})
    return out


def analysis_problems(as_is: str | None, spec: str | None, scenario_text: str | None) -> list[str]:
    """What Analysis must fix before it may finish: the three documents agree, and each is complete."""
    if spec is None:
        return []  # the required-documents check reports a missing document
    problems = []
    numbers = {int(m.group(1)) for heading, _ in _sections(spec) if (m := re.match(r"^(\d{1,2})[.\s]", heading))}
    missing = [f"{n}. {name}" for n, name in SPEC_SECTIONS.items() if n not in numbers]
    if missing:
        problems.append(f"{SPEC_FILE}: missing sections (the format's numbered headings, `## <n>. <title>`): "
                        + "; ".join(missing))
    defined = spec_ids(spec)
    for k, ids in defined.items():
        bad = [i for i in ids if not ID.fullmatch(i)]
        if bad:
            problems.append(f"{SPEC_FILE}: malformed ids {', '.join(bad)} (use FR-001, BR-001, UC-01, MSG-01)")
        twice = sorted({i for i in ids if ids.count(i) > 1})
        if twice:
            problems.append(f"{SPEC_FILE}: {', '.join(twice)} defined more than once: give each its own id")
    if not defined["FR"]:
        problems.append(f"{SPEC_FILE}: section 5 defines no functional requirement (rows `| FR-001 | The system "
                        "shall … |`)")
    if as_is is not None:
        if _section(as_is, "Rule enforcement") is None:
            problems.append(f"{AS_IS_FILE}: no \"Rule enforcement\" section (a table of every BR id with its code "
                            "location; write \"None\" under the heading when there is none)")
        if _messages_section(as_is) is None:
            problems.append(f"{AS_IS_FILE}: no \"Messages\" section (a table of every MSG id, errors, warnings and "
                            "confirmations, with where it is produced; write \"None\" when there is none)")
        enforced = _enforcement(as_is)
        for k, section in (("BR", 6), ("MSG", 9)):
            in_spec, in_as_is = set(defined[k]), {i for i in enforced if kind(i) == k}
            if in_as_is - in_spec:
                problems.append(f"{', '.join(sorted(in_as_is - in_spec))} in {AS_IS_FILE} but not in {SPEC_FILE} "
                                f"section {section}: the same ids in both")
            if in_spec - in_as_is:
                problems.append(f"{', '.join(sorted(in_spec - in_as_is))} in {SPEC_FILE} but not in {AS_IS_FILE}'s "
                                f"{'Rule enforcement' if k == 'BR' else 'Messages'}: the same ids in both")
    if scenario_text is not None:
        problems += _scenario_problems(scenario_text, defined)
    return problems


def _scenario_problems(text: str, defined: dict[str, list[str]]) -> list[str]:
    problems = []
    headings = [h.lower() for h, _ in _sections(text)]
    missing = [s for s in SCENARIO_SECTIONS if not any(h.startswith(s.lower()) for h in headings)]
    if missing:
        problems.append(f"{SCENARIOS_FILE}: missing sections: {', '.join(missing)} (the format's `## ` headings)")
    found = scenarios(text)
    if not found:
        problems.append(f"{SCENARIOS_FILE}: no scenario (`### TS-001 <title>`)")
    twice = [i for i, s in found.items() if s["count"] > 1]
    if twice:
        problems.append(f"{SCENARIOS_FILE}: {', '.join(twice)} used for more than one scenario")
    no_covers = [i for i, s in found.items() if not s["covers"]]
    if no_covers:
        problems.append(f"{SCENARIOS_FILE}: {', '.join(no_covers[:20])} without a `Covers:` line naming the FR, BR "
                        "and MSG ids it proves")
    no_expected = [i for i, s in found.items() if s["expected"] is not True]
    if no_expected:
        problems.append(f"{SCENARIOS_FILE}: {', '.join(no_expected[:20])} without an expected result")
    known = {i for ids in defined.values() for i in ids}
    unknown = sorted({i for s in found.values() for i in s["covers"] if i not in known and kind(i) != "TS"})
    if unknown:
        problems.append(f"{SCENARIOS_FILE}: covers {', '.join(unknown[:20])}, which {SPEC_FILE} doesn't define")
    covered = {i for s in found.values() for i in s["covers"]}
    uncovered = [i for k in ("FR", "BR") for i in defined[k] if i not in covered]
    if uncovered:
        problems.append(f"{', '.join(uncovered[:30])} of {SPEC_FILE} not covered by any scenario: add one, or add "
                        "the id to the `Covers:` line of a scenario that proves it")
    return problems


_TECHNICAL = [
    (re.compile(r"\b\w+\.(java|jsp|xml|do|properties|sql)\b"), "file names"),
    (re.compile(r"\b(SELECT\s+\S+.*\bFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.I), "SQL"),
    (re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)*(Service|Controller|Dao|DAO|Mapper|Impl|Repository|Bean|Action)\b"),
     "class names"),
    (re.compile(r"\b[a-z]+(\.[a-z0-9_]+){2,}\b"), "package names"),
    (re.compile(r"#\w+\("), "method names"),
    (re.compile(r"\b(iBatis|MyBatis|Spring|JDBC|Hibernate|HttpSession|ThreadLocal|servlet|JSP)\b", re.I),
     "technology names"),
    (re.compile(r"\b[A-Z][A-Z0-9]{1,}_[A-Z0-9_]{2,}\b"), "table or column names"),
    (re.compile(r"/[\w-]+(/[\w.-]+)+"), "URLs or paths"),
]


def wording_problems(spec: str | None) -> list[str]:
    """Likely technical content in the business users' document (reported, never blocking: a heuristic)."""
    if not spec:
        return []
    out = []
    for pattern, what in _TECHNICAL:
        hits = sorted({m.group(0).strip() for m in pattern.finditer(spec)})[:5]
        if hits:
            out.append(f"{SPEC_FILE} may contain {what}: {', '.join(hits)}")
    return out


def allocation(design: str | None) -> dict[str, dict]:
    """domain-design.md's "Functional allocation": id -> {"owner", "where", "count"}."""
    out: dict[str, dict] = {}
    for id_, cells in _row_ids(_section(design or "", "functional allocation") or []):
        owner = (cells[1] if len(cells) > 1 else "").strip("`* ").lower()
        entry = out.setdefault(id_, {"owner": owner, "where": cells[2] if len(cells) > 2 else "", "count": 0})
        entry["count"] += 1
    return out


def _owner(text: str) -> str | None:
    return next((o for o in OWNERS if text.startswith(o) or text.replace("-", " ").startswith(o)), None)


def allocation_problems(spec: str | None, design: str | None, scope: str | None = None) -> list[str]:
    """Modulith design's allocation: every FR, BR and MSG of the specification listed exactly once, with an owner; no
    orchestrator for a domain-only feature (its orchestrator steps are skipped: nothing would cover those ids)."""
    if spec is None or design is None:
        return []
    if _section(design, "functional allocation") is None:
        return [f"{DESIGN_FILE}: no \"Functional allocation\" section (a table `| Id | Owner | Where |` of every FR, "
                f"BR and MSG id of {SPEC_FILE})"]
    defined = spec_ids(spec)
    wanted = [i for k in KINDS for i in defined[k]]
    given = allocation(design)
    problems = []
    missing = [i for i in wanted if i not in given]
    if missing:
        problems.append(f"{DESIGN_FILE}: Functional allocation lacks {', '.join(missing[:30])}")
    unknown = sorted(i for i in given if i not in wanted)
    if unknown:
        problems.append(f"{DESIGN_FILE}: Functional allocation lists {', '.join(unknown[:20])}, which {SPEC_FILE} "
                        "doesn't define")
    twice = sorted(i for i, e in given.items() if e["count"] > 1)
    if twice:
        problems.append(f"{DESIGN_FILE}: Functional allocation lists {', '.join(twice)} more than once")
    bad = sorted(i for i, e in given.items() if _owner(e["owner"]) is None)
    if bad:
        problems.append(f"{DESIGN_FILE}: Functional allocation: owner of {', '.join(bad[:20])} must be domain, "
                        "orchestrator, other domain or UI unchanged")
    orchestrated = sorted(i for i, e in given.items() if _owner(e["owner"]) == "orchestrator")
    if scope == "domain-only" and orchestrated:
        problems.append(f"{DESIGN_FILE}: Functional allocation gives {', '.join(orchestrated[:20])} to the "
                        "orchestrator, but the feature is domain-only (no orchestrator is built): give them to the "
                        "domain")
    return problems


def coverage_rows(text: str | None) -> list[dict]:
    """A step's "Functional coverage" table: [{"id", "by", "covered", "reason"}]."""
    rows = []
    for id_, cells in _row_ids(_section(text or "", "functional coverage") or []):
        status = (cells[2] if len(cells) > 2 else "").strip("`* ")
        covered = status.lower().startswith("covered")
        reason = "" if covered else re.sub(r"^not covered\W*", "", status, flags=re.I).strip()
        rows.append({"id": id_, "by": cells[1] if len(cells) > 1 else "", "covered": covered, "reason": reason})
    return rows


def _by_name(documents: dict[str, str], folder: str) -> dict[str, str]:
    prefix = folder.rstrip("/") + "/"
    return {p[len(prefix):]: text for p, text in documents.items() if p.startswith(prefix)}


def report(documents: dict[str, str], folder: str, steps: list[dict] | None = None,
           completed: set[str] | None = None) -> dict:
    """The feature's functional coverage from its documents in effect (`folder`: the run's documents folder). `steps`:
    the run's workflow steps (titles, and which steps it has); `completed`: the steps done so far."""
    docs = _by_name(documents, folder)
    spec = docs.get(SPEC_FILE)
    if spec is None:
        return {"available": False, "message": "No functional specification for this run (it started before Analysis "
                                               "wrote one, or Analysis hasn't run yet)."}
    titles = {st["key"]: st.get("title") or st["key"] for st in steps or []}
    in_run = set(titles) if steps else set(COVERAGE_STEPS)
    defined = spec_ids(spec)
    as_is, scenario_text, design = docs.get(AS_IS_FILE), docs.get(SCENARIOS_FILE), docs.get(DESIGN_FILE)
    enforced = _enforcement(as_is or "")
    found = scenarios(scenario_text or "")
    allocated = allocation(design)
    ids: list[dict] = []
    reported = {}
    for step, (name, _owners, _kinds) in COVERAGE_STEPS.items():
        if step in in_run and name in docs and _section(docs[name], "functional coverage") is not None:
            reported[step] = coverage_rows(docs[name])
    lifted = {s: by for s, by in LIFTED_BY.items() if by in in_run and COVERAGE_STEPS[by][0] in docs}
    for k in KINDS:
        for id_ in defined[k]:
            owner = _owner(allocated[id_]["owner"]) if id_ in allocated else None
            if owner is None and enforced.get(id_, {}).get("where") == "ui":
                owner = "ui unchanged"
            covered_by, not_covered = [], []
            for step, rows in reported.items():
                for row in rows:
                    if row["id"] == id_:
                        # A step listing an id its owner isn't (the domain suite listing an orchestrator requirement)
                        # is shown, but doesn't count.
                        outside = owner in ("domain", "orchestrator") and owner not in COVERAGE_STEPS[step][1]
                        entry = {"step": step, "title": titles.get(step, step), "by": row["by"],
                                 "outside_owner": outside}
                        later = lifted.get(step)
                        if later in reported:  # the service's own table is the evidence now
                            entry["superseded_by"] = titles.get(later, later)
                        elif later:  # the service was built from it, its tests copied: shown where they run now
                            entry["title"] = (f"{titles.get(later, later)} (tests copied from "
                                              f"{titles.get(step, step)})")
                        (covered_by if row["covered"] else not_covered).append({**entry, "reason": row["reason"]})
            # The steps whose cover counts: a requirement's, its owner's acceptance suite (when the run has it);
            # otherwise every step that covers ids of its owner and kind (without an owner: of its kind).
            suite = ACCEPTANCE.get(owner) if k == "FR" else None
            relevant = ([suite] if suite in in_run else
                        [s for s, (_n, owners, kinds) in COVERAGE_STEPS.items()
                         if s in in_run and k in kinds and (owner is None or owner in owners)])
            counted = [c for c in covered_by if c["step"] in relevant and not c["outside_owner"]
                       and not c.get("superseded_by")]
            for c in covered_by:  # an owner's step that isn't the one a requirement is judged on: supporting only
                c["counts"] = c in counted
            # A gap: none of them covered it, though one has reported. Another domain's and UI-only ids are never gaps:
            # this migration doesn't change them.
            gap = owner not in _NOT_GAPS and any(s in reported for s in relevant) and not counted
            ids.append({"id": id_, "kind": k, "owner": owner, "scenarios": [t for t, s in found.items()
                                                                              if id_ in s["covers"]],
                        "covered_by": covered_by, "not_covered": not_covered, "gap": gap,
                        "counted": bool(counted), "proven_by_owner_suite": bool(suite and any(
                            c["step"] == suite for c in counted))})
    by_kind = {k: {"total": sum(1 for i in ids if i["kind"] == k),
                   "covered": sum(1 for i in ids if i["kind"] == k and i["counted"])} for k in KINDS}
    return {"available": True, "ids": ids, "summary": by_kind,
            "reported_by": [{"step": s, "title": titles.get(s, s)} for s in reported],
            "gaps": [i["id"] for i in ids if i["gap"]],
            "without_scenario": [i["id"] for i in ids if i["kind"] in ("FR", "BR") and not i["scenarios"]],
            "analysis_problems": analysis_problems(as_is, spec, scenario_text),
            "allocation_problems": allocation_problems(spec, design, _scope(docs.get("domain-dependencies.yaml")))
            if design is not None else [],
            "wording": wording_problems(spec)}


def _scope(text: str | None) -> str | None:
    """domain-dependencies.yaml's scope decision (domain-only or cross-domain); None when there is none."""
    import yaml

    try:
        return ((yaml.safe_load(text or "") or {}).get("decision") or {}).get("scope")
    except (yaml.YAMLError, AttributeError):
        return None


def step_line(text: str | None, report_: dict, step: str) -> str | None:
    """The run log's line after a step that wrote a coverage table."""
    rows = coverage_rows(text)
    if not rows or not report_.get("available"):
        return None
    covered = sorted({r["id"] for r in rows if r["covered"]})
    not_covered = [r for r in rows if not r["covered"]]
    line = f"Functional coverage: {len(covered)} id(s) covered by this step"
    if not_covered:
        line += "; not covered here: " + ", ".join(f"{r['id']} ({r['reason']})" if r["reason"] else r["id"]
                                                   for r in not_covered[:10])
    owner = next((o for o, s in ACCEPTANCE.items() if s == step), None)
    if owner:  # an acceptance suite: the requirements its owner has, proven by it or not (a report, never a block)
        mine = [i for i in report_["ids"] if i["kind"] == "FR" and i["owner"] == owner]
        missing = [i["id"] for i in mine if not i["proven_by_owner_suite"]]
        line += (f"; the {owner}'s requirements proven by this suite: {len(mine) - len(missing)} of {len(mine)}"
                 + (f" (not proven: {', '.join(missing[:15])})" if missing else ""))
    outside = sorted({i["id"] for i in report_["ids"] for c in i["covered_by"]
                      if c["step"] == step and c["outside_owner"]})
    if outside:
        line += f"; listed here but owned by the other side (not counted): {', '.join(outside[:15])}"
    gaps = report_.get("gaps") or []
    if gaps:
        line += f"; not covered by any step yet: {', '.join(gaps[:15])}{' …' if len(gaps) > 15 else ''}"
    return line


def documents_of(output_dir: Path) -> tuple[str | None, str | None, str | None]:
    """as-is.md, functional-spec.md and test-scenarios.md in a step's documents folder (None for one not there)."""
    read = lambda name: (output_dir / name).read_text(encoding="utf-8") if (output_dir / name).is_file() else None  # noqa: E731
    return read(AS_IS_FILE), read(SPEC_FILE), read(SCENARIOS_FILE)


def other_party(spec: str) -> list[str]:
    """The requirements the specification marks as involving another business area or system (section 5's "Other
    party" column)."""
    rows = _row_ids(_spec_section(spec, 5))
    header = next((_cells(line) for line in _spec_section(spec, 5) if (_cells(line) or [""])[0].lower() == "id"), None)
    column = next((i for i, c in enumerate(header or []) if "other party" in c.lower()), None)
    if column is None:
        return []
    return [i for i, cells in rows if kind(i) == "FR" and len(cells) > column
            and cells[column].strip("`* ").lower() not in ("", "-", "–", "none", "n/a")]


def trace_problems(output_dir: Path) -> list[str]:
    """Domain dependencies' links to the specification: the ids its interactions cite exist, and every requirement
    the specification says involves another party maps to an interaction or is ruled internal, with the reason."""
    import yaml

    spec = (output_dir / SPEC_FILE).read_text(encoding="utf-8") if (output_dir / SPEC_FILE).is_file() else None
    cross = output_dir / "cross-domain.yaml"
    if spec is None or not cross.is_file():
        return []
    try:
        interactions = (yaml.safe_load(cross.read_text(encoding="utf-8")) or {}).get("interactions") or []
        deps_file = output_dir / "domain-dependencies.yaml"
        deps = (yaml.safe_load(deps_file.read_text(encoding="utf-8")) or {}) if deps_file.is_file() else {}
    except (yaml.YAMLError, AttributeError):
        return []  # the format check reports it
    defined = spec_ids(spec)
    known = {i for k in KINDS for i in defined[k]}
    scenario_text = output_dir / SCENARIOS_FILE
    known_scenarios = set(scenarios(scenario_text.read_text(encoding="utf-8"))) if scenario_text.is_file() else None
    problems = []
    linked = set()
    for item in (i for i in interactions if isinstance(i, dict)):
        cited = [str(r) for r in item.get("requirements") or []]
        linked.update(cited)
        unknown = [r for r in cited if r not in known]
        if unknown:
            problems.append(f"cross-domain.yaml interaction {item.get('id')}: requirements {', '.join(unknown)} are "
                            f"not FR, BR or MSG ids of {SPEC_FILE}")
        if known_scenarios is not None:
            missing = [str(t) for t in item.get("scenarios") or [] if str(t) not in known_scenarios]
            if missing:
                problems.append(f"cross-domain.yaml interaction {item.get('id')}: scenarios {', '.join(missing)} are "
                                f"not in {SCENARIOS_FILE}")
    internal = {str(i.get("requirement")) for i in deps.get("considered_internal") or [] if isinstance(i, dict)}
    unknown = sorted(i for i in internal if i not in known)
    if unknown:
        problems.append(f"domain-dependencies.yaml considered_internal: {', '.join(unknown)} not in {SPEC_FILE}")
    unmapped = [i for i in other_party(spec) if i not in linked and i not in internal]
    if unmapped:
        problems.append(f"{', '.join(unmapped)} of {SPEC_FILE} involve another party (section 5) but no interaction "
                        "cites them: add them to the `requirements` of the interaction that serves them, or list them "
                        "under `considered_internal` in domain-dependencies.yaml with the reason")
    return problems


def follower_view(follower: str, documents: dict[str, str]) -> object:
    """The parts of a step's documents (file name: text) a later documents step builds on, to tell whether an amendment
    changed them. Domain dependencies reads as-is.md whole, the ids of the specification and the scenarios, and the
    specification's exception flows, messages and side-effects (sections 7, 9 and 10). Any other step: everything."""
    if follower != "dependency_analysis":
        return documents
    spec = documents.get(SPEC_FILE) or ""
    return {"as_is": documents.get(AS_IS_FILE), "ids": spec_ids(spec),
            "sections": [_spec_section(spec, n) for n in (7, 9, 10)],
            "other_party": other_party(spec),
            "scenarios": {k: v["covers"] for k, v in scenarios(documents.get(SCENARIOS_FILE) or "").items()}}
