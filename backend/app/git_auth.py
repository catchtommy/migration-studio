"""Whose git access fetches and pushes: each user's own token for the repository's host (My settings), passed to git
through GIT_ASKPASS so it never appears in a URL, a command line or a log. Without a token for the host (or for SSH
URLs), git uses the server's own setup (SSH keys, credential helper)."""

import re
import stat
from urllib.parse import urlsplit

from sqlalchemy import select

from app import secret_box
from app.config import settings
from app.db.models import GitCredential, User
from app.db.session import session_scope

_SCP = re.compile(r"^[\w.-]+@([\w.-]+):")  # git@github.com:org/repo.git
_ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$STUDIO_GIT_USERNAME" ;;
  *) printf '%s' "$STUDIO_GIT_PASSWORD" ;;
esac
"""


def host_of(url: str | None) -> str | None:
    """The host of an HTTP(S) or SSH git URL (None for local paths)."""
    if not url:
        return None
    m = _SCP.match(url)
    if m:
        return m.group(1).lower()
    parts = urlsplit(url)
    return parts.hostname.lower() if parts.scheme in ("http", "https", "ssh", "git") and parts.hostname else None


def normalize_host(host: str) -> str:
    """What users type (a host, or a URL of one) as a bare lowercase host."""
    host = host.strip()
    return (host_of(host) if "://" in host or "@" in host else host.strip("/")).lower()


def uses_https(url: str | None) -> bool:
    return bool(url) and urlsplit(url).scheme in ("http", "https")


def _askpass() -> str:
    path = settings.data_dir / "git-askpass.sh"
    if not path.exists() or path.read_text() != _ASKPASS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_ASKPASS)
        path.chmod(stat.S_IRWXU)
    return str(path)


def credential(user_id: int | None, url: str | None) -> tuple[str, str] | None:
    """(username, token) the user saved for the URL's host, if any."""
    host = host_of(url)
    if user_id is None or host is None or not uses_https(url):
        return None
    with session_scope() as s:
        row = s.scalar(select(GitCredential).where(GitCredential.user_id == user_id, GitCredential.host == host))
        return (row.username, secret_box.decrypt(row.token)) if row else None


def env_for(user_id: int | None, url: str | None) -> dict | None:
    """Environment for a git command reaching `url` as the user: their token, and no other credential source (so a
    server-wide helper can't stand in for them). None: the server's own git setup."""
    found = credential(user_id, url)
    if found is None:
        return None
    username, token = found
    return {"GIT_ASKPASS": _askpass(), "GIT_TERMINAL_PROMPT": "0", "STUDIO_GIT_USERNAME": username,
            "STUDIO_GIT_PASSWORD": token, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": ""}


def identity(user_id: int) -> tuple[str, str] | None:
    """(name, email) the user's commits are authored as; None when they haven't set an email."""
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or not user.git_email:
            return None
        return (user.git_name or user.display_name, user.git_email)


def scrub(text: str) -> str:
    """Error text from git, without credentials someone may have put in a URL."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)

