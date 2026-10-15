from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import settings_store
from app.auth import require_global
from app.copilot_service import get_service, normalize_host

router = APIRouter(prefix="/api/settings")


@router.get("")
def get_settings() -> dict[str, Any]:
    """Global settings (AI provider, GitHub host, network). Project settings are under /api/projects/{key}."""
    return settings_store.get_global()


@router.put("", dependencies=[Depends(require_global("settings.manage"))])
def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    if "github_host" in values:
        values = {**values, "github_host": normalize_host(values["github_host"])}
    before = settings_store.get_global()
    try:
        saved = settings_store.update_global(values)
    except KeyError as e:
        raise HTTPException(422, str(e))
    if any(before[k] != saved[k] for k in settings_store.NETWORK_KEYS):
        get_service().restart()  # the Copilot runtime picks up network settings when it starts
    return saved
