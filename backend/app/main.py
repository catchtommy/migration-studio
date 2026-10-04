import logging
from contextlib import asynccontextmanager

import truststore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import agent_store, domain_store, workflow_store
from app.api import agents_api, copilot_api, domains_api, runs, settings_api, workflows_api
from app.config import settings
from app.copilot_service import get_service
from app.db.session import init_db
from app.orchestrator.runner import get_runner

# Trust the operating system's certificate store (which holds corporate root CAs) for all HTTPS from this process.
truststore.inject_into_ssl()
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    agent_store.seed()
    domain_store.seed()
    workflow_store.seed()
    get_runner().recover_interrupted()
    yield
    get_runner().shutdown()
    get_service().shutdown()


app = FastAPI(title="Migration Studio", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(runs.router)
app.include_router(settings_api.router)
app.include_router(agents_api.router)
app.include_router(domains_api.router)
app.include_router(workflows_api.router)
app.include_router(copilot_api.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
