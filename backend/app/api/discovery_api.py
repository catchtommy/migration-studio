from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import discovery_store, workflow_store
from app.api.runs import start_run
from app.api.schemas import DiscoveryOverview, DiscoveryStart, FeaturesStart, RunOut
from app.auth import require
from app.db.models import Domain, Run
from app.db.session import get_db
from app.domain_store import normalize_inputs

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


def start_features(db: Session, domain: str, body: DiscoveryStart) -> Run:
    if db.get(Domain, domain) is None:
        raise HTTPException(404, "Domain not found")
    return start_run(db, domain, discovery_store.FEATURES_OP, "domain",
                     body.workflow or workflow_store.default_for(None, "domain"), _inputs(body))


@router.post("/features", response_model=list[RunOut], status_code=201, dependencies=[Depends(require("domains.manage"))])
def discover_all_features(body: FeaturesStart | None = None, db: Session = Depends(get_db)):
    """Start listing features for every domain that has no unfinished listing run (and, by default, no feature list
    yet)."""
    body = body or FeaturesStart()
    busy = set(db.scalars(select(Run.domain).where(Run.operation == discovery_store.FEATURES_OP,
                                                   Run.status != "completed")))
    listed = {d["key"] for d in discovery_store.overview()["domains"] if d["features"]}
    runs = []
    for key in db.scalars(select(Domain.key).order_by(Domain.created_at, Domain.key)).all():
        if key in busy or (body.only_missing and key in listed):
            continue
        runs.append(start_features(db, key, body))
    return runs
