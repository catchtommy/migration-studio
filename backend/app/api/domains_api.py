from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import agent_store
from app.api.schemas import DomainCreate, DomainOut, DomainSummary, DomainUpdate, OperationDocs
from app.db.models import Artifact, Domain, Run, WorkflowDef
from app.db.session import get_db
from app.domain_store import normalize_inputs

router = APIRouter(prefix="/api/domains")


def _domain_or_404(db: Session, key: str) -> Domain:
    domain = db.get(Domain, key)
    if domain is None:
        raise HTTPException(404, "Domain not found")
    return domain


@router.get("", response_model=list[DomainSummary])
def list_domains(db: Session = Depends(get_db)):
    counts = {(d, st): n for d, st, n in db.execute(
        select(Run.domain, Run.status, func.count()).group_by(Run.domain, Run.status))}
    out = []
    for d in db.scalars(select(Domain).order_by(Domain.created_at, Domain.key)):
        summary = DomainSummary.model_validate(d)
        summary.runs = sum(n for (dom, _), n in counts.items() if dom == d.key)
        summary.completed = counts.get((d.key, "completed"), 0)
        out.append(summary)
    return out


@router.post("", response_model=DomainOut, status_code=201)
def create_domain(body: DomainCreate, db: Session = Depends(get_db)):
    if db.get(Domain, body.key):
        raise HTTPException(409, f"Domain {body.key} already exists")
    domain = Domain(key=body.key, name=body.name, description=body.description, context="", agent_inputs={})
    db.add(domain)
    db.commit()
    return domain


@router.get("/{key}", response_model=DomainOut)
def get_domain(key: str, db: Session = Depends(get_db)):
    return _domain_or_404(db, key)


@router.put("/{key}", response_model=DomainOut)
def update_domain(key: str, body: DomainUpdate, db: Session = Depends(get_db)):
    domain = _domain_or_404(db, key)
    for field in ("name", "description", "context"):
        if getattr(body, field) is not None:
            setattr(domain, field, getattr(body, field))
    if body.default_workflow is not None:
        if body.default_workflow and db.get(WorkflowDef, body.default_workflow) is None:
            raise HTTPException(422, f"Unknown workflow {body.default_workflow}")
        domain.default_workflow = body.default_workflow or None
    if body.agent_inputs is not None:
        unknown = set(body.agent_inputs) - set(agent_store.titles())
        if unknown:
            raise HTTPException(422, f"Unknown agents: {', '.join(sorted(unknown))}")
        domain.agent_inputs = {stage: normalize_inputs(i.model_dump()) for stage, i in body.agent_inputs.items()}
    db.commit()
    return domain


@router.delete("/{key}", status_code=204)
def delete_domain(key: str, db: Session = Depends(get_db)):
    domain = _domain_or_404(db, key)
    if db.scalar(select(func.count()).where(Run.domain == key)):
        raise HTTPException(409, "Domains with migration runs cannot be deleted")
    db.delete(domain)
    db.commit()


@router.get("/{key}/docs", response_model=list[OperationDocs])
def domain_docs(key: str, db: Session = Depends(get_db)):
    """Every operation in the domain with the documents from the latest attempt of each stage."""
    _domain_or_404(db, key)
    out = []
    for run in db.scalars(select(Run).where(Run.domain == key).order_by(Run.operation, Run.created_at)):
        artifacts = db.scalars(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.id)).all()
        latest = {}
        for a in artifacts:
            latest[a.stage] = max(latest.get(a.stage, 0), a.attempt)
        out.append({"run": run, "artifacts": [a for a in artifacts if a.attempt == latest[a.stage]]})
    return out
