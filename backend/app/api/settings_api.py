from typing import Any

from fastapi import APIRouter, HTTPException

from app import settings_store
from app.copilot_service import get_service, normalize_host

router = APIRouter(prefix="/api/settings")


@router.get("")
def get_settings() -> dict[str, Any]:
    return settings_store.get_all()


@router.put("")
def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    if "github_host" in values:
        values = {**values, "github_host": normalize_host(values["github_host"])}
    before = settings_store.get_all()
    try:
        saved = settings_store.update(values)
    except KeyError as e:
        raise HTTPException(422, str(e))
    if any(before[k] != saved[k] for k in settings_store.NETWORK_KEYS):
        get_service().restart()  # the Copilot runtime picks up network settings when it starts
    return saved
