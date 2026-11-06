from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import data_folder, maintenance, repos
from app.api.database_api import _switching, busy
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
    if not _switching.acquire(blocking=False):
        raise HTTPException(409, "The database is being switched")
    try:
        with maintenance.hold("The data folder is being changed; try again when that has finished"):
            # Checked once nothing new can start
            if reason := busy("changing the data folder"):
                raise HTTPException(409, reason)
            if repos.is_fetching():
                raise HTTPException(409, "A repository is being fetched; wait for it before changing the data folder")
            return data_folder.change(body.path, body.copy_data)
    except maintenance.Busy as e:
        raise HTTPException(409, str(e))
    except data_folder.DataFolderError as e:
        raise HTTPException(422, str(e))
    finally:
        _switching.release()
