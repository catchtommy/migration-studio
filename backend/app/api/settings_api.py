from typing import Any

from fastapi import APIRouter, HTTPException

from app import settings_store

router = APIRouter(prefix="/api/settings")


@router.get("")
def get_settings() -> dict[str, Any]:
    return settings_store.get_all()


@router.put("")
def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    try:
        return settings_store.update(values)
    except KeyError as e:
        raise HTTPException(422, str(e))
