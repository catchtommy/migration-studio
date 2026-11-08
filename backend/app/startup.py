"""Opening the database: at startup, after first-time setup, and when the database is switched in Settings."""

import logging

from sqlalchemy import URL

from app import agent_store, auth, domain_store, legacy, projects, run_cleanup, shared_texts, workflow_store
from app.db import session, transfer
from app import workspace
from app.config import settings
from app.orchestrator.runner import get_runner
from app.tools import git_ops

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
    get_runner().release_publish_waits()
    try:  # finished runs need nothing on disk (their documents and diffs are in the database)
        removed = workspace.release_completed()
        if removed:
            log.info("Removed the workspaces of %d completed run(s)", removed)
    except OSError:
        log.exception("Could not remove completed runs' workspaces")
    cleaned = workspace.cleanup_leftovers(settings.workspaces_dir)  # folders Windows held when they were replaced
    if cleaned:
        log.info("Deleted %d workspace folder(s) left from earlier checkouts", cleaned)
    removed = run_cleanup.remove_orphan_contracts()
    if removed:
        log.info("Removed %d contract(s) left by runs deleted earlier, which nothing else uses", removed)
    try:  # the data folder may have been moved by hand
        legacy.repair_moved()
    except (OSError, git_ops.GitError):
        log.exception("Could not repair the shared copies of the monolith")
    log.info("Using %s database", url.get_backend_name())
