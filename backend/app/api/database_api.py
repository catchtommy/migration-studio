"""Choosing the studio's database: first-time setup, and switching later from Settings. Available in setup mode."""

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import auth
from app.db import config as db_config
from app.db import migrations, session, transfer
from app.db.models import Run
from app.orchestrator.runner import get_runner
from app.startup import open_database

router = APIRouter(prefix="/api/database")
_switching = threading.Lock()


class DatabaseIn(BaseModel):
    dialect: Literal["postgresql", "oracle", "sqlite"]
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = ""
    username: str = ""
    password: str = ""  # blank keeps the saved password for the same server and user
    options: dict[str, str] = {}


class ConfigureIn(BaseModel):
    config: DatabaseIn
    copy_data: bool = False  # copy everything from the database in use now
    replace: bool = False  # allow replacing data already in the target database


def _config(body: DatabaseIn) -> db_config.DatabaseConfig:
    cfg = db_config.DatabaseConfig(**body.model_dump())
    if cfg.dialect != "sqlite":
        missing = [label for label, value in (("host", cfg.host), ("database" if cfg.dialect == "postgresql" else
                                              "service name", cfg.database), ("username", cfg.username)) if not value]
        if missing:
            raise HTTPException(422, f"Fill in the {', '.join(missing)}")
    saved = db_config.load()
    if not cfg.password and saved and (saved.dialect, saved.host, saved.port, saved.username) == \
            (cfg.dialect, cfg.host, cfg.port, cfg.username):
        cfg.password = saved.password
    return cfg


@router.get("")
def status() -> dict:
    url = db_config.current_url()
    saved = db_config.load()
    source = "env" if db_config.from_env() else "file" if saved else "default" if url else None
    out = {"configured": session.is_configured(), "source": source, "dialect": url.get_backend_name() if url else None,
           "config": None, "version": None}
    if saved and source == "file":
        out["config"] = saved.public()
    elif url:
        out["config"] = {"dialect": url.get_backend_name(), "host": url.host or "", "port": url.port,
                         "database": url.query.get("service_name") or url.database or "",
                         "username": url.username or "", "options": {}, "has_password": bool(url.password)}
    if out["config"] and out["dialect"] == "sqlite" and not out["config"]["database"]:
        out["config"]["database"] = str(db_config.default_sqlite_path())
    if session.is_configured():
        info = session.get_engine().dialect.server_version_info
        if info:
            out["version"] = ".".join(str(v) for v in info)
    return out


def setup_or_settings(request: Request) -> None:
    """In setup mode (no database, or no users yet) anyone may choose the database; afterwards it is a setting."""
    if not session.is_configured() or auth.needs_bootstrap():
        return
    auth.require_global("settings.manage")(auth.current_user(request))


@router.post("/test", dependencies=[Depends(setup_or_settings)])
def test(body: DatabaseIn) -> dict:
    try:
        return {"ok": True, "version": db_config.test_connection(_config(body).url())}
    except db_config.ConnectionFailed as e:
        raise HTTPException(422, f"Could not connect: {e}")


def _busy() -> str | None:
    if not session.is_configured():
        return None
    with session.session_scope() as s:
        working = s.scalars(select(Run.id).where(Run.status.in_(["running", "awaiting_input"]))).all()
    if working or get_runner().has_active():
        return f"{len(working)} run(s) are working; wait for them to stop before switching the database"
    return None


@router.post("", dependencies=[Depends(setup_or_settings)])
def configure(body: ConfigureIn) -> dict:
    if db_config.from_env():
        raise HTTPException(409, "The database is set by the STUDIO_DATABASE_URL environment variable; change it there")
    if not _switching.acquire(blocking=False):
        raise HTTPException(409, "The database is already being switched")
    try:
        if reason := _busy():
            raise HTTPException(409, reason)
        cfg = _config(body.config)
        url = cfg.url()
        try:
            db_config.test_connection(url)
        except db_config.ConnectionFailed as e:
            raise HTTPException(422, f"Could not connect: {e}")
        target = session.make_engine(url)
        copied = None
        try:
            migrations.upgrade(target)
            if body.copy_data and session.is_configured():
                source = session.get_engine()
                if source.url.render_as_string(hide_password=False) != url.render_as_string(hide_password=False):
                    copied = sum(transfer.copy_all(source, target, replace=body.replace).values())
        except transfer.TargetNotEmpty as e:
            raise HTTPException(409, str(e))
        finally:
            target.dispose()
        db_config.save(cfg)
        open_database(url)
    finally:
        _switching.release()
    return {**status(), "copied_rows": copied}
