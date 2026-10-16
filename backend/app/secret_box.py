"""Encrypting secrets kept in the database (users' git access tokens).

The key is STUDIO_SECRET_KEY, or else data/secret.key (made on first use, readable by the server's account only).
Every server sharing the database needs the same key. If it's lost, the stored tokens can't be read and their users
enter them again.
"""

import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_lock = threading.Lock()
_fernet: Fernet | None = None


class SecretUnreadable(RuntimeError):
    pass


def _box() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            key = os.environ.get("STUDIO_SECRET_KEY", "").encode()
            if not key:
                path = settings.data_dir / "secret.key"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(Fernet.generate_key())
                key = path.read_bytes().strip()
            _fernet = Fernet(key)
        return _fernet


def encrypt(value: str) -> str:
    return _box().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _box().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise SecretUnreadable("This secret was saved with another key; enter it again") from e
