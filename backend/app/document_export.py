"""Download the documents the agents generated: a run's, or every run's of a domain, as one zip.

Each run's documents are those of the latest attempt of each stage (reviewers' edits included), as the run and the
domain's Documents tab show them. In the zip they are grouped by stage, in the order the stages ran:
`<NN>-<stage>/<file>`, and per feature (`<feature>/<NN>-<stage>/<file>`) for a domain.
"""

import io
import re
import zipfile
from collections.abc import Iterable
from posixpath import normpath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base_agent import DOCS_ROOT
from app.db.models import Artifact, Run, StageExecution


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


def run_zip(db: Session, run: Run) -> bytes:
    return _zip(_entries(run, latest_artifacts(db, run.id)))


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
    return _zip(sorted(entries))


def filename(*parts: str) -> str:
    return "-".join(_safe(p) for p in parts) + "-documents.zip"
