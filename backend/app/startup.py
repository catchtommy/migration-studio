"""Opening the database: at startup, after first-time setup, and when the database is switched in Settings."""

import logging

from sqlalchemy import URL

from app import agent_store, auth, domain_store, projects, workflow_store
from app.db import session, transfer
from app.orchestrator.runner import get_runner

log = logging.getLogger(__name__)


def open_database(url: URL) -> None:
    session.configure(url)
    session.init_db()
    transfer.import_legacy_checkpoints(session.get_engine())
    auth.seed()
    projects.seed()
    agent_store.seed()
    domain_store.seed()
    workflow_store.seed()
    get_runner().recover_interrupted()
    log.info("Using %s database", url.get_backend_name())
