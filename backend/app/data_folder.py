"""Choosing the studio's data folder from Settings. STUDIO_DATA_DIR overrides the choice (see app/config.py).

Paths under the folder are worked out from `settings.data_dir` when they are used, so a change applies at once. The old
folder is left in place, for the administrator to delete.
"""

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from app import config, legacy
from app.config import settings
from app.db import config as db_config
from app.db import session
from app.tools import git_ops


class DataFolderError(ValueError):
    pass


def status() -> dict:
    source = "env" if config.DATA_DIR_FROM_ENV else "settings" if config.LOCATION_FILE.is_file() else "default"
    return {"path": str(settings.data_dir), "source": source}


def _validated(value: str) -> Path:
    # Windows' "Copy as path" puts the path in quotes
    path = Path(value.strip().strip('"'))
    if not path.is_absolute():
        raise DataFolderError("Enter the full path of the folder, e.g. D:\\studio\\data or /var/lib/studio/data")
    path = Path(os.path.normpath(path))
    current = settings.data_dir.resolve()
    if path.resolve() != current and (path.resolve().is_relative_to(current) or current.is_relative_to(path.resolve())):
        raise DataFolderError("The new folder can't be inside the current data folder, or contain it")
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as e:
        raise DataFolderError(f"The server can't write to {path}: {e}")
    return path


def _check_holds_the_studios_files(old: Path, new: Path) -> None:
    """Without these the studio would lose its database connection, or the saved git credentials, at its next start."""
    for name in ("database.json", "secret.key"):
        if (old / name).is_file() and (not (new / name).is_file() or (old / name).read_bytes() != (new / name).read_bytes()):
            raise DataFolderError(f"{new} doesn't have the same {name} as the current data folder. Copy the data there "
                                  "first, or choose to copy it now")
    url = db_config.current_url()
    if url and url.get_backend_name() == "sqlite" and url.database == str(db_config.default_sqlite_path()) \
            and not (new / "studio.db").is_file():
        raise DataFolderError(f"{new} doesn't have the SQLite database (studio.db). Copy the data there first, or "
                              "choose to copy it now")


def _copy(old: Path, new: Path) -> None:
    try:
        shutil.copytree(old, new, symlinks=True, dirs_exist_ok=True)
    except OSError as e:  # shutil.Error too. The folder was empty: what is in it now is this copy's
        for dirpath, _dirs, _files in os.walk(new):
            os.chmod(dirpath, stat.S_IRWXU)  # the shared copies of the monolith have read-only folders
        for child in new.iterdir():
            if child.is_dir():
                git_ops.remove_tree(child)
            else:
                child.unlink()
        raise DataFolderError(f"Could not copy the data to {new}, so the data folder is unchanged: {e}")


def _save_location(path: Path) -> None:
    tmp = config.LOCATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"data_dir": str(path)}, indent=2), encoding="utf-8")
    os.replace(tmp, config.LOCATION_FILE)


def change(value: str, copy_data: bool) -> dict:
    """Use this folder from now on: an empty one the current data is copied to, or one that already holds it."""
    if config.DATA_DIR_FROM_ENV:
        raise DataFolderError("The data folder is set by STUDIO_DATA_DIR (the environment or backend/.env); change it "
                              "there, or remove it to choose the folder here")
    old, new = settings.data_dir, _validated(value)
    if new.resolve() == old.resolve():
        return status()
    if not copy_data:
        _check_holds_the_studios_files(old, new)
    elif any(new.iterdir()):
        raise DataFolderError(f"{new} isn't empty. Choose an empty folder, or one that already holds the data without "
                              "copying")
    engine = session.get_engine()
    if engine.dialect.name == "sqlite":
        engine.dispose()  # writes SQLite's journal into the database file, so the copy has it all
    if copy_data:
        _copy(old, new)
    _save_location(new)
    settings.data_dir = new
    url = db_config.current_url()
    if url is not None and url.render_as_string(hide_password=False) != \
            engine.url.render_as_string(hide_password=False):
        session.configure(url)  # SQLite in the data folder
    legacy.repair_moved()
    return status()
