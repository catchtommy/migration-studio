"""Opening the database: at startup, after first-time setup, and when the database is switched in Settings."""

import logging

from sqlalchemy import URL

from app import agent_store, auth, domain_store, projects, shared_texts, workflow_store
from app.db import session, transfer
from app import workspace
from app.config import settings
from app.orchestrator.runner import get_runner

log = logging.getLogger(__name__)


def open_database(url: URL) -> None:
    session.configure(url)
    session.init_db()
    transfer.import_legacy_checkpoints(session.get_engine())
    auth.seed()
    projects.seed()
    agent_store.seed()
    shared_texts.seed()  # after the agents: it gives their instructions placeholders
    domain_store.seed()
    workflow_store.seed()
    get_runner().recover_interrupted()
    try:  # finished runs need nothing on disk (their documents and diffs are in the database)
        removed = workspace.release_completed()
        if removed:
            log.info("Removed the workspaces of %d completed run(s)", removed)
    except OSError:
        log.exception("Could not remove completed runs' workspaces")
    cleaned = workspace.cleanup_leftovers(settings.workspaces_dir)  # folders Windows held when they were replaced
    if cleaned:
        log.info("Deleted %d workspace folder(s) left from earlier checkouts", cleaned)
    log.info("Using %s database", url.get_backend_name())
