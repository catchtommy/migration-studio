"""Checks that a domain's microservice contains only its own domain: no references to other domains' Java packages
and no use of other domains' tables. Ownership comes from each domain's Boundary settings (owned packages/tables)."""

import re
from dataclasses import dataclass
from pathlib import Path

CODE_SUFFIXES = {".java", ".kt", ".xml", ".sql", ".yml", ".yaml", ".properties"}
SKIP_DIRS = {"target", "build", ".gradle", "node_modules", ".git", ".idea"}
MAX_VIOLATIONS = 200


@dataclass
class Violation:
    path: str  # relative to the workspace
    line: int
    rule: str  # package | table | missing-service
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}" if self.line else f"{self.path}: {self.detail}"


def _files(root: Path):
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix in CODE_SUFFIXES and not SKIP_DIRS.intersection(f.relative_to(root).parts):
            yield f


def check(workspace: Path, domain: str, service_dir: str, ownership: dict[str, dict]) -> list[Violation]:
    """ownership: domain key -> {"tables": [...], "packages": [...]}. Returns violations, most relevant first."""
    rules: list[tuple[re.Pattern, str, str]] = []
    for other, owned in ownership.items():
        if other == domain:
            continue
        for pkg in owned.get("packages") or []:
            rules.append((re.compile(rf"\b{re.escape(pkg)}(\.|\b)"), "package",
                          f"uses package {pkg}, which belongs to the {other} domain"))
        for table in owned.get("tables") or []:
            rules.append((re.compile(rf"(?<![\w$]){re.escape(table)}(?![\w$])", re.IGNORECASE), "table",
                          f"uses table {table}, which belongs to the {other} domain"))
    if not rules:
        return []  # no other domain has recorded ownership: nothing to check against
    root = workspace / service_dir
    if not root.is_dir():
        return [Violation(service_dir, 0, "missing-service", "service directory does not exist yet")]

    violations: list[Violation] = []
    for f in _files(root):
        rel = str(f.relative_to(workspace))
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            # Java import/package lines name packages, not tables (a package segment can share a table's name).
            declaration = line.lstrip().startswith(("import ", "package "))
            for pattern, rule, detail in rules:
                if (rule == "package" or not declaration) and pattern.search(line):
                    violations.append(Violation(rel, n, rule, detail))
                    if len(violations) >= MAX_VIOLATIONS:
                        return violations
                    break  # one finding per line is enough to point at it
    return violations


def report(violations: list[Violation]) -> str:
    if not violations:
        return "No boundary violations: the service only uses its own domain's packages and tables."
    capped = f" (first {MAX_VIOLATIONS})" if len(violations) >= MAX_VIOLATIONS else ""
    lines = [f"{len(violations)} boundary violation(s){capped}:"]
    lines += [f"- {v}" for v in violations]
    lines.append("Replace each with a port + adapter using the agreed contract (API, event or read model); never "
                 "reference another domain's classes or tables from the service.")
    return "\n".join(lines)
