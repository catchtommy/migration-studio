from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import discovery_store, projects, workflow_store
from app.api.runs import start_run
from app.api.schemas import DiscoveryOverview, DiscoveryStart, FeatureListStart, FeaturesStart, RunOut
from app.auth import require
from app.db.models import Domain, Run
from app.db.session import get_db
from app.domain_store import in_migration_order, normalize_inputs

router = APIRouter(prefix="/api/discovery")


@router.get("", response_model=DiscoveryOverview)
def overview():
    return discovery_store.overview()


@router.post("/bounded-contexts", response_model=RunOut, status_code=201, dependencies=[Depends(require("domains.manage"))])
def discover_bounded_contexts(body: DiscoveryStart | None = None, db: Session = Depends(get_db)):
    """Start the run that finds the monolith's bounded contexts."""
    body = body or DiscoveryStart()
    return start_run(db, discovery_store.MONOLITH, discovery_store.CONTEXTS_OP, "monolith",
                     body.workflow or workflow_store.default_for(None, "monolith"), _inputs(body))


def _inputs(body: DiscoveryStart) -> dict | None:
    inputs = normalize_inputs(body.inputs.model_dump()) if body.inputs else None
    return inputs if inputs and (inputs["paths"] or inputs["notes"]) else None


def start_features(db: Session, domain: str, body: FeatureListStart) -> Run:
    if db.get(Domain, (projects.current(), domain)) is None:
        raise HTTPException(404, "Domain not found")
    inputs = _inputs(body)
    if body.from_scratch:  # kept with the run's inputs, so a redo or rejection lists from scratch too
        inputs = {**(inputs or {"paths": [], "notes": ""}), discovery_store.FROM_SCRATCH: True}
    return start_run(db, domain, discovery_store.FEATURES_OP, "domain",
                     body.workflow or workflow_store.default_for(None, "domain"), inputs)


@router.post("/features", response_model=list[RunOut], status_code=201, dependencies=[Depends(require("domains.manage"))])
def discover_all_features(body: FeaturesStart | None = None, db: Session = Depends(get_db)):
    """Start listing features for every domain that has no unfinished listing run (and, by default, no feature list
    yet)."""
    body = body or FeaturesStart()
    project = projects.current()
    busy = set(db.scalars(select(Run.domain).where(Run.project == project, Run.operation == discovery_store.FEATURES_OP,
                                                   Run.status != "completed")))
    listed = {d["key"] for d in discovery_store.overview()["domains"] if d["features"]}
    runs = []
    for key in db.scalars(in_migration_order(select(Domain.key).where(Domain.project == project))).all():
        if key in busy or (body.only_missing and key in listed):
            continue
        runs.append(start_features(db, key, body))
    return runs
