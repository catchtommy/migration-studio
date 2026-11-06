from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import data_folder
from app.api.database_api import busy
from app.auth import require_global

router = APIRouter(prefix="/api/data-folder", dependencies=[Depends(require_global("settings.manage"))])


class DataFolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copy_data: bool = True  # copy the current data there; otherwise the folder must already hold it


@router.get("")
def get_data_folder() -> dict:
    return data_folder.status()


@router.put("")
def change_data_folder(body: DataFolderIn) -> dict:
    if reason := busy("changing the data folder"):
        raise HTTPException(409, reason)
    try:
        return data_folder.change(body.path, body.copy_data)
    except data_folder.DataFolderError as e:
        raise HTTPException(422, str(e))
