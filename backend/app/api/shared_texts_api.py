from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import shared_texts
from app.auth import CurrentUser, require, require_global

router = APIRouter(prefix="/api/shared-texts")


class SharedTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


@router.get("", dependencies=[Depends(require("agents.manage"))])
def list_texts():
    """The texts agents share, with the agents whose instructions include each."""
    return shared_texts.list_all()


@router.put("/{key}")
def update_text(key: str, body: SharedTextIn, user: CurrentUser = Depends(require_global("agents.manage"))):
    """Change a shared text. Every agent that includes it gets the new text from the next step that starts."""
    try:
        return shared_texts.update(key, body.text, user.display_name)
    except KeyError:
        raise HTTPException(404, "Shared text not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/{key}/reset")
def reset_text(key: str, user: CurrentUser = Depends(require_global("agents.manage"))):
    """Put a shared text back to the default it shipped with."""
    try:
        return shared_texts.reset(key, user.display_name)
    except KeyError:
        raise HTTPException(404, "Only shared texts that shipped with the studio have a default")
