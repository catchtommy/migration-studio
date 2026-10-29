from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import (agent_store, contracts_store, discovery_store, document_export, domain_store, run_cleanup,
                 workflow_store)
from app.api.discovery_api import start_features
from app.api.schemas import (
    DependencyMap, DependencyOut, DomainCreate, DomainOut, DomainSummary, DomainUpdate, FeatureCreate, FeatureIn,
    DiscoveryStart, FeatureOut, OperationDocs, RunOut,
)
from app import projects
from app.auth import CurrentUser, require, require_admin
from app.db.models import Dependency, Domain, Feature, Run
from app.db.session import get_db
from app.domain_store import normalize_inputs

router = APIRouter(prefix="/api/domains")


def _domain_or_404(db: Session, key: str) -> Domain:
    domain = db.get(Domain, (projects.current(), key))
    if domain is None:
        raise HTTPException(404, "Domain not found")
    return domain


@router.get("", response_model=list[DomainSummary])
def list_domains(db: Session = Depends(get_db)):
    project = projects.current()
    counts = {(d, st): n for d, st, n in db.execute(
        select(Run.domain, Run.status, func.count()).where(Run.project == project, discovery_store.MIGRATION_RUN)
        .group_by(Run.domain, Run.status))}
    features = dict(db.execute(select(Feature.domain, func.count()).where(Feature.project == project)
                               .group_by(Feature.domain)).all())
    out = []
    for d in db.scalars(select(Domain).where(Domain.project == project).order_by(Domain.created_at, Domain.key)):
        summary = DomainSummary.model_validate(d)
        summary.runs = sum(n for (dom, _), n in counts.items() if dom == d.key)
        summary.completed = counts.get((d.key, "completed"), 0)
        summary.features = features.get(d.key, 0)
        out.append(summary)
    return out


@router.get("/dependency-map", response_model=DependencyMap)
def dependency_map():
    """Which domains depend on which (from approved integration designs), and a suggested migration order."""
    return contracts_store.dependency_map()


@router.post("", response_model=DomainOut, status_code=201, dependencies=[Depends(require("domains.manage"))])
def create_domain(body: DomainCreate, db: Session = Depends(get_db)):
    if db.get(Domain, (projects.current(), body.key)):
        raise HTTPException(409, f"Domain {body.key} already exists")
    domain = Domain(project=projects.current(), key=body.key, name=body.name, description=body.description, context="", agent_inputs={})
    db.add(domain)
    db.commit()
    return domain


def _out(domain: Domain) -> DomainOut:
    out = DomainOut.model_validate(domain)
    out.workflows = workflow_store.domain_workflows(domain)
    return out


@router.get("/{key}", response_model=DomainOut)
def get_domain(key: str, db: Session = Depends(get_db)):
    return _out(_domain_or_404(db, key))


@router.put("/{key}", response_model=DomainOut, dependencies=[Depends(require("domains.manage"))])
def update_domain(key: str, body: DomainUpdate, db: Session = Depends(get_db)):
    domain = _domain_or_404(db, key)
    for field in ("name", "description", "context"):
        if getattr(body, field) is not None:
            setattr(domain, field, getattr(body, field))
    if body.default_workflow is not None:
        scope = workflow_store.scope_of(body.default_workflow) if body.default_workflow else "operation"
        if scope is None:
            raise HTTPException(422, f"Unknown workflow {body.default_workflow}")
        if scope != "operation":
            raise HTTPException(422, "A domain's default workflow must be a migration workflow")
        domain.default_workflow = body.default_workflow or None
    for field in ("owned_tables", "owned_packages"):
        values = getattr(body, field)
        if values is not None:
            setattr(domain, field, list(dict.fromkeys(v.strip() for v in values if v.strip())))
    if body.agent_inputs is not None:
        unknown = set(body.agent_inputs) - set(agent_store.titles())
        if unknown:
            raise HTTPException(422, f"Unknown agents: {', '.join(sorted(unknown))}")
        inputs = {stage: normalize_inputs(i.model_dump()) for stage, i in body.agent_inputs.items()}
        problem = _repository_problem(inputs, domain)
        if problem:
            raise HTTPException(422, problem)
        domain.agent_inputs = inputs
    db.commit()
    return _out(domain)


def _repository_problem(inputs: dict[str, dict], domain: Domain) -> str | None:
    """Every agent whose step changes code in one of the domain's workflows has a valid repository, and agents sharing
    a repository use the same base branch (they share its checkout)."""
    from app.tools.git_ops import valid_branch
    titles = {k: t for k, (t, _d) in agent_store.titles().items()}
    from app import settings_store
    from app.modulith_scope import SKIPPABLE
    used = [w["key"] for w in workflow_store.domain_workflows(domain)]
    required = workflow_store.code_agents(workflows=used) if settings_store.get("monolith_repo") else {}  # blank: none
    required = {k: v for k, v in required.items() if k not in SKIPPABLE}  # asked for when a cross-domain feature runs it
    for key, workflows in required.items():
        repository = (inputs.get(key) or {}).get("repository", "")
        if not repository:
            return (f"Set the repository of {titles.get(key, key)}: it changes code in "
                    f"{', '.join(dict.fromkeys(workflows))}")
    from app import delivery, domain_modules
    lift = (inputs.get(domain_modules.LIFT_AGENT) or {}).get("repository", "")
    same = [k for k in domain_modules.MODERN_AGENTS if delivery._same((inputs.get(k) or {}).get("repository"), lift)]
    if same:  # the old stack and the new one can't share a project
        return (f"{titles.get(same[0], same[0])} builds the modern service from the lifted one "
                f"({titles.get(domain_modules.LIFT_AGENT)}), so it needs a repository of its own, not {lift}")
    suite = (inputs.get("independent_tests") or {}).get("repository", "")
    if suite and lift and not delivery._same(suite, lift):  # its baseline is taken against the lifted service
        return (f"{titles.get('independent_tests', 'Independent tests')} writes the acceptance suite in the lifted "
                f"service's repository ({lift}), where its baseline is taken")
    bases: dict[str, tuple[str, str]] = {}
    for key, i in inputs.items():
        repository, base = i.get("repository", ""), i.get("base_branch", "")
        if not repository:
            continue
        error = workflow_store.repository_error(repository)
        if error:
            return f"{titles.get(key, key)}: {error}"
        if base and not valid_branch(base):
            return f"{titles.get(key, key)}: {base!r} isn't a valid branch name"
        other = bases.setdefault(repository, (key, base))
        if other[1] != base:
            return (f"{titles.get(key, key)} and {titles.get(other[0], other[0])} work in {repository}, so they need "
                    "the same base branch")
    return None


@router.delete("/{key}", status_code=204, dependencies=[Depends(require("domains.manage"))])
def delete_domain(key: str, with_runs: bool = False, db: Session = Depends(get_db)):
    """Delete a domain. One with runs (migrations or feature listings) is only deleted with_runs=true, which also
    deletes the runs, their documents and workspaces, its features, dependencies and the contracts it provides."""
    domain = _domain_or_404(db, key)
    runs = db.scalar(select(func.count()).where(Run.project == projects.current(), Run.domain == key))
    if runs and not with_runs:
        raise HTTPException(409, f"{domain.name} has {runs} run(s); delete it with its runs to remove them too")
    db.close()
    try:
        domain_store.delete_with_runs(key)
    except domain_store.DomainBusy as e:
        raise HTTPException(409, str(e))


@router.get("/{key}/docs", response_model=list[OperationDocs])
def domain_docs(key: str, db: Session = Depends(get_db)):
    """Every operation in the domain with the documents from the latest attempt of each stage."""
    _domain_or_404(db, key)
    return [{"run": run, "artifacts": document_export.latest_artifacts(db, run.id)}
            for run in db.scalars(select(Run).where(Run.project == projects.current(), Run.domain == key)
                                  .order_by(Run.operation, Run.created_at))]


@router.get("/{key}/documents.zip")
def domain_documents_zip(key: str, db: Session = Depends(get_db)):
    """Every document the agents generated for the domain's features, as one zip (one folder per feature)."""
    _domain_or_404(db, key)
    runs = db.scalars(select(Run).where(Run.project == projects.current(), Run.domain == key)).all()
    return Response(document_export.domain_zip(db, runs), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{document_export.filename(key)}"'})


@router.get("/{key}/dependencies", response_model=list[DependencyOut])
def domain_dependencies(key: str, db: Session = Depends(get_db)):
    """Cross-domain interactions where this domain is either side, from approved integration designs."""
    _domain_or_404(db, key)
    rows = db.scalars(select(Dependency).where(Dependency.project == projects.current(),
                                               (Dependency.source == key) | (Dependency.target == key))
                      .order_by(Dependency.id)).all()
    operations = {r.id: r.operation for r in db.scalars(select(Run).where(Run.id.in_({d.run_id for d in rows})))}
    return [DependencyOut.model_validate(d).model_copy(update={"operation": operations.get(d.run_id)}) for d in rows]


@router.get("/{key}/features", response_model=list[FeatureOut])
def list_features(key: str, db: Session = Depends(get_db)):
    """The domain's features, each with its latest migration run. Migrations can only start for these."""
    _domain_or_404(db, key)
    return discovery_store.features_of(key)


@router.post("/{key}/features", response_model=FeatureOut, status_code=201, dependencies=[Depends(require("domains.manage"))])
def add_feature(key: str, body: FeatureCreate, db: Session = Depends(get_db)):
    _domain_or_404(db, key)
    try:
        return discovery_store.save_feature(key, body.key, body.model_dump(), create=True)
    except discovery_store.DiscoveryError as e:
        raise HTTPException(409, str(e))


@router.put("/{key}/features/{feature}", response_model=FeatureOut, dependencies=[Depends(require("domains.manage"))])
def update_feature(key: str, feature: str, body: FeatureIn, db: Session = Depends(get_db)):
    _domain_or_404(db, key)
    try:
        return discovery_store.save_feature(key, feature, body.model_dump(), create=False)
    except KeyError:
        raise HTTPException(404, "Feature not found")


@router.delete("/{key}/features/{feature}", status_code=204, dependencies=[Depends(require("domains.manage"))])
def delete_feature(key: str, feature: str, db: Session = Depends(get_db)):
    _domain_or_404(db, key)
    try:
        discovery_store.delete_feature(key, feature)
    except KeyError:
        raise HTTPException(404, "Feature not found")
    except discovery_store.DiscoveryError as e:
        raise HTTPException(409, str(e))


@router.post("/{key}/features/{feature}/reset")
def reset_feature(key: str, feature: str, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_admin("reset a feature"))) -> dict:
    """Set a feature back to not started, for clean-ups: every migration run of it (one that is working is stopped
    first) is deleted with everything recorded for it (app/run_cleanup.py). The feature stays in the list, ready to be
    migrated again. Code pushed and documents published to git stay there."""
    _domain_or_404(db, key)
    if discovery_store.feature(key, feature) is None:
        raise HTTPException(404, "Feature not found")
    project = projects.current()
    db.close()
    try:
        deleted = run_cleanup.stop_and_delete(project, run_cleanup.feature_runs(project, key, feature))
    except run_cleanup.StillWorking as e:
        raise HTTPException(409, str(e))
    return {"deleted": deleted}


@router.post("/{key}/discover-features", response_model=RunOut, status_code=201, dependencies=[Depends(require("domains.manage"))])
def discover_features(key: str, body: DiscoveryStart | None = None, db: Session = Depends(get_db)):
    """Start the run that lists this domain's features."""
    return start_features(db, key, body or DiscoveryStart())
