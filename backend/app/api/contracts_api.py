from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import contracts_store
from app.api.schemas import ContractOut, ContractUpdate
from app.auth import require
from app.db.models import Contract
from app.db.session import get_db

router = APIRouter(prefix="/api/contracts")


@router.get("", response_model=list[ContractOut])
def list_contracts(domain: str | None = None):
    """The company-wide contracts catalogue; with `domain`, those it provides or consumes."""
    return contracts_store.list_contracts(domain)


@router.get("/{key}", response_model=ContractOut)
def get_contract(key: str, db: Session = Depends(get_db)):
    contract = db.get(Contract, key)
    if contract is None:
        raise HTTPException(404, "Contract not found")
    return contracts_store.as_dict(contract)


@router.put("/{key}", response_model=ContractOut, dependencies=[Depends(require("domains.manage"))])
def update_contract(key: str, body: ContractUpdate, db: Session = Depends(get_db)):
    """Record progress, e.g. when the monolith facade or the new service starts providing the contract."""
    contract = db.get(Contract, key)
    if contract is None:
        raise HTTPException(404, "Contract not found")
    contract.status = body.status
    db.commit()
    return contracts_store.as_dict(contract)
