"""Which database the studio uses. The connection can't be stored in the database it connects to, so it lives in
`data/database.json` (readable by its owner only), written by the setup screen and the Settings page.

Precedence: the STUDIO_DATABASE_URL environment variable, then database.json, then an existing data/studio.db (installs
from before the database could be chosen keep working on SQLite). Otherwise the studio starts in setup mode.
"""

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.pool import NullPool

from app.config import settings

DIALECTS = ("postgresql", "oracle", "sqlite")
DRIVERS = {"postgresql": "postgresql+psycopg", "oracle": "oracle+oracledb", "sqlite": "sqlite"}
DEFAULT_PORTS = {"postgresql": 5432, "oracle": 1521}
LABELS = {"postgresql": "PostgreSQL", "oracle": "Oracle", "sqlite": "SQLite"}


@dataclass
class DatabaseConfig:
    dialect: str  # postgresql | oracle | sqlite
    host: str = ""
    port: int | None = None
    database: str = ""  # PostgreSQL database name, Oracle service name, or the SQLite file path
    username: str = ""
    password: str = ""
    options: dict[str, str] = field(default_factory=dict)  # extra URL query parameters, e.g. sslmode=require
    # Oracle only: an Oracle Instant Client folder, to connect in thick mode (e.g. for users whose passwords only have
    # Oracle's old 10G verifier). Empty: thin mode, which needs no Oracle software.
    oracle_client: str = ""

    def url(self) -> URL:
        if self.dialect not in DIALECTS:
            raise ValueError(f"Unknown database type {self.dialect!r}; use one of {', '.join(DIALECTS)}")
        if self.dialect == "sqlite":
            return make_url(f"sqlite:///{self.database or default_sqlite_path()}")
        query = dict(self.options)
        database = self.database
        if self.dialect == "oracle":
            query["service_name"] = self.database  # oracle+oracledb takes the service name as a query parameter
            database = None
        return URL.create(DRIVERS[self.dialect], username=self.username or None, password=self.password or None,
                          host=self.host or None, port=self.port or DEFAULT_PORTS[self.dialect],
                          database=database or None, query=query)

    def public(self) -> dict:
        """The settings without the password, for the UI."""
        out = asdict(self)
        out["has_password"] = bool(out.pop("password"))
        return out


def config_path() -> Path:
    return settings.data_dir / "database.json"


def default_sqlite_path() -> Path:
    return settings.data_dir / "studio.db"


def load() -> DatabaseConfig | None:
    path = config_path()
    if not path.is_file():
        return None
    return DatabaseConfig(**json.loads(path.read_text(encoding="utf-8")))


def save(cfg: DatabaseConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Created owner-only before the password is written to it.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(cfg), f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def current_url() -> URL | None:
    """The database to use at startup, or None when the studio must be set up first."""
    if settings.database_url:
        return make_url(settings.database_url)
    cfg = load()
    if cfg is not None:
        return cfg.url()
    if default_sqlite_path().is_file():
        return DatabaseConfig(dialect="sqlite").url()
    return None


def from_env() -> bool:
    return bool(settings.database_url)


def dialect_of(url: URL) -> str:
    return url.get_backend_name()


def connect_args(url: URL) -> dict:
    """Fail fast when the database server can't be reached, instead of waiting for the OS TCP timeout."""
    return {"postgresql": {"connect_timeout": 10}, "oracle": {"tcp_connect_timeout": 10},
            "sqlite": {"check_same_thread": False}}[url.get_backend_name()]


class ConnectionFailed(RuntimeError):
    pass


_oracle_client: str | None = None  # the Instant Client folder thick mode was started with in this process
_oracle_lock = threading.Lock()


def use_oracle_client(folder: str | None) -> None:
    """Switch python-oracledb to thick mode with the Oracle Instant Client in `folder` (or STUDIO_ORACLE_CLIENT_DIR).
    Thick mode is per process: once on, it stays on until the studio restarts. No folder: thin mode."""
    global _oracle_client
    folder = (folder or os.environ.get("STUDIO_ORACLE_CLIENT_DIR") or "").strip()
    if not folder:
        return
    with _oracle_lock:
        if _oracle_client == folder:
            return
        if _oracle_client is not None:
            raise ConnectionFailed(f"Oracle Instant Client is already loaded from {_oracle_client}; restart the studio "
                                   "to use another folder")
        if not Path(folder).is_dir():
            raise ConnectionFailed(f"There is no Oracle Instant Client folder {folder}")
        import oracledb
        try:
            # On Linux the library is found through LD_LIBRARY_PATH (or ldconfig), set before the studio starts.
            oracledb.init_oracle_client(**({} if sys.platform.startswith("linux") else {"lib_dir": folder}))
        except oracledb.Error as e:
            raise ConnectionFailed(f"Could not load Oracle Instant Client from {folder}: {e}") from e
        _oracle_client = folder


def prepare_driver(url: URL, cfg: "DatabaseConfig | None" = None) -> None:
    """Driver set-up a database needs before connecting (Oracle thick mode, when an Instant Client is configured)."""
    if url.get_backend_name() == "oracle":
        cfg = cfg if cfg is not None else load()
        use_oracle_client(cfg.oracle_client if cfg and cfg.dialect == "oracle" else None)


def _explain(message: str) -> str:
    """The driver's message, with what to do for errors people hit when first connecting."""
    if "DPY-3015" in message:
        return (f"{message}. This database user's password only has Oracle's old 10G verifier, which thin mode can't "
                "use. Ask the DBA to reset the password (with SQLNET.ALLOWED_LOGON_VERSION_SERVER of 11 or higher, so "
                "a newer verifier is made), or install Oracle Instant Client and enter its folder below to connect "
                "in thick mode.")
    return message


def test_connection(url: URL, cfg: "DatabaseConfig | None" = None) -> str:
    """Connect and return the server's version text. Raises ConnectionFailed with a readable reason."""
    prepare_driver(url, cfg)
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args(url))
    try:
        with engine.connect() as conn:
            name = url.get_backend_name()
            conn.execute(text("SELECT 1 FROM DUAL" if name == "oracle" else "SELECT 1"))
            version = ".".join(str(v) for v in engine.dialect.server_version_info or ())
            note = ""
            if name == "oracle":  # documents are NCLOB (Unicode) either way; short texts follow the database's set
                charset = conn.execute(text("SELECT value FROM nls_database_parameters "
                                            "WHERE parameter = 'NLS_CHARACTERSET'")).scalar()
                if charset and charset not in ("AL32UTF8", "UTF8"):
                    note = (f" · character set {charset}: documents are stored as Unicode; names with characters "
                            f"outside {charset} would show as ¿ (AL32UTF8 avoids that)")
        return f"{LABELS[name]} {version}{note}".strip()
    except Exception as e:  # driver errors vary; show the driver's own message
        orig = getattr(e, "orig", None) or e
        message = str(orig).strip().splitlines()[0] if str(orig).strip() else type(orig).__name__
        raise ConnectionFailed(_explain(message)) from e
    finally:
        engine.dispose()
