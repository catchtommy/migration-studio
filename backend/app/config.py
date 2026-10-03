from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Process-level configuration (env vars / .env). Per-migration settings live in the DB."""

    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=BACKEND_DIR / ".env", extra="ignore")

    data_dir: Path = BACKEND_DIR / "data"
    database_url: str = ""  # defaults to SQLite under data_dir
    cors_origins: list[str] = ["http://localhost:5173"]
    stub_questions: bool = False  # stub agents ask a question per stage, to exercise the question UI without Claude

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'studio.db'}"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"


settings = Settings()
