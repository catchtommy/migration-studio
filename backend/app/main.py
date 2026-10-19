import logging
from contextlib import asynccontextmanager

import truststore
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    account_api, agents_api, auth_api, contracts_api, copilot_api, database_api, discovery_api, domains_api, projects_api, runs,
    settings_api, shared_texts_api, users_api, workflows_api,
)
from app.auth import current_user, project_context
from app.config import settings
from app.copilot_service import get_service
from app.db import config as db_config
from app.db.session import NotConfigured, is_configured
from app.orchestrator.runner import get_runner
from app.startup import open_database

# Trust the operating system's certificate store (which holds corporate root CAs) for all HTTPS from this process.
truststore.inject_into_ssl()
logging.basicConfig(level=logging.INFO)
SETUP_REQUIRED = {"setup_required": True, "message": "Choose a database on the setup screen first"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    url = db_config.current_url()
    if url is None:
        logging.getLogger(__name__).info("No database configured yet: waiting for setup at /api/database")
    else:
        open_database(url)
    yield
    get_runner().shutdown()
    get_service().shutdown()


def require_database() -> None:
    if not is_configured():
        raise HTTPException(503, SETUP_REQUIRED)


app = FastAPI(title="Migration Studio", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(database_api.router)  # used by the setup screen, before anyone can sign in
app.include_router(auth_api.router, dependencies=[Depends(require_database)])
# Everything else needs a signed-in user; routes that change things also check a permission (app/auth.py).
for router in (settings_api.router, copilot_api.router, users_api.router, projects_api.router, account_api.router):
    app.include_router(router, dependencies=[Depends(require_database), Depends(current_user)])
# Project data: the request names its project (X-Project), which the user must be able to open.
for router in (runs.router, agents_api.router, domains_api.router, workflows_api.router, contracts_api.router,
               discovery_api.router, shared_texts_api.router):
    app.include_router(router, dependencies=[Depends(require_database), Depends(project_context)])


@app.exception_handler(NotConfigured)
async def not_configured(_: Request, __: NotConfigured):
    return JSONResponse({"detail": SETUP_REQUIRED}, status_code=503)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": is_configured()}
