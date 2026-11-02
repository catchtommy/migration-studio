"""Download the documents the agents generated: a run's, or every run's of a domain, as one zip.

Each run's documents are those of the latest attempt of each stage (reviewers' edits included), as the run and the
domain's Documents tab show them. In the zip they are grouped by stage, in the order the stages ran:
`<NN>-<stage>/<file>`, and per feature (`<feature>/<NN>-<stage>/<file>`) for a domain. Each run's documents come
with its `approvals.md` (`<feature>/approvals.md` for a domain): its gate decisions and checklist sign-offs.
"""

import io
import re
import zipfile
from collections.abc import Iterable
from posixpath import normpath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base_agent import DOCS_ROOT
from app.db.models import Approval, Artifact, Run, StageExecution


def latest_artifacts(db: Session, run_id: str) -> list[Artifact]:
    """The run's documents from the latest completed attempt of each stage (the latest attempt, when none completed:
    what a stopped step kept), in the order they were written."""
    artifacts = db.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id)).all()
    done = {(stage, attempt) for stage, attempt in db.execute(select(StageExecution.stage, StageExecution.attempt)
                                                             .where(StageExecution.run_id == run_id,
                                                                    StageExecution.status == "completed"))}

    def rank(a: Artifact) -> tuple[bool, int]:
        return (a.stage, a.attempt) in done, a.attempt

    best: dict[str, tuple[bool, int]] = {}
    for a in artifacts:
        best[a.stage] = max(best.get(a.stage, (False, 0)), rank(a))
    return [a for a in artifacts if rank(a) == best[a.stage]]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "document"


def _entries(run: Run, artifacts: Iterable[Artifact], prefix: str = "") -> list[tuple[str, str]]:
    """(path in the zip, content) for a run's documents: under the stage's folder, relative to the run's documents."""
    own = f"{DOCS_ROOT}/{run.domain}/{run.operation}/"
    order: dict[str, int] = {}
    out = []
    for a in artifacts:
        n = order.setdefault(a.stage, len(order) + 1)
        relative = a.path[len(own):] if a.path.startswith(own) else a.path.rsplit("/", 1)[-1]
        relative = "/".join(_safe(p) for p in normpath(relative).split("/") if p not in ("", ".", ".."))
        out.append((f"{prefix}{n:02d}-{_safe(a.stage)}/{relative}", a.content or ""))
    return out


def _zip(entries: list[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in entries:
            z.writestr(path, content)
    return buffer.getvalue()


def approvals(db: Session, run: Run) -> str | None:
    """The run's gate decisions as a document (`approvals.md`): who approved or rejected what, when, with the comments
    and the checklist items they ticked. The functional specification points here for its approval."""
    rows = db.scalars(select(Approval).where(Approval.run_id == run.id).order_by(Approval.created_at, Approval.id)).all()
    if not rows:
        return None
    titles = {d["key"]: d.get("title") or d["key"] for d in run.stage_defs or []}
    ticks: dict[str, list[list[dict]]] = {}
    for ex in db.scalars(select(StageExecution).where(StageExecution.run_id == run.id,
                                                      StageExecution.status.in_(("approved", "rejected")))
                         .order_by(StageExecution.attempt)):
        ticks.setdefault(ex.stage, []).append(ex.checklist or [])
    lines = [f"# Approvals: {run.domain} / {run.operation}", "",
             "| Gate | Decision | By | When (UTC) | Comments |", "|---|---|---|---|---|"]
    for a in rows:
        decision = "Approved" if a.decision == "approve" else f"Rejected ({titles.get(a.target_stage, a.target_stage)})" \
            if a.target_stage else "Rejected"
        comments = (a.comments or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {titles.get(a.gate, a.gate)} | {decision} | {a.reviewer} | {a.created_at:%Y-%m-%d %H:%M} | "
                     f"{comments} |")
    ticked = [(gate, t) for gate, attempts in ticks.items() for items in attempts for t in items]
    if ticked:
        lines += ["", "## Checklist sign-offs", "", "| Gate | Item | By | When (UTC) |", "|---|---|---|---|"]
        lines += [f"| {titles.get(gate, gate)} | {t['item']} | {t.get('by') or ''} | {(t.get('at') or '')[:16]} |"
                  for gate, t in ticked]
    return "\n".join(lines) + "\n"


def run_zip(db: Session, run: Run) -> bytes:
    entries = _entries(run, latest_artifacts(db, run.id))
    record = approvals(db, run)
    return _zip([*entries, ("approvals.md", record)] if record else entries)


def domain_zip(db: Session, runs: Iterable[Run]) -> bytes:
    """Every run's documents, one folder per feature (a feature migrated more than once: its latest run first, then
    the earlier ones with their start date)."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for run in sorted(runs, key=lambda r: (r.operation, r.created_at), reverse=True):
        folder = _safe(run.operation)
        if folder in seen:
            folder = f"{folder}-{run.created_at:%Y%m%d-%H%M%S}"
        seen.add(folder)
        entries += _entries(run, latest_artifacts(db, run.id), f"{folder}/")
        record = approvals(db, run)
        if record:
            entries.append((f"{folder}/approvals.md", record))
    return _zip(sorted(entries))


def filename(*parts: str) -> str:
    return "-".join(_safe(p) for p in parts) + "-documents.zip"
