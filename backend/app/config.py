import json
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# The data folder chosen in Settings. Not in the data folder (it says where that is) or the database (whose connection
# is kept in the data folder). See app/data_folder.py.
LOCATION_FILE = BACKEND_DIR / "data-location.json"


def _chosen_data_dir() -> Path:
    if LOCATION_FILE.is_file():
        return Path(json.loads(LOCATION_FILE.read_text(encoding="utf-8"))["data_dir"])
    return BACKEND_DIR / "data"


class Settings(BaseSettings):
    """Process-level configuration (env vars / .env). Per-migration settings live in the DB."""

    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=BACKEND_DIR / ".env", extra="ignore")

    # STUDIO_DATA_DIR (environment or .env), then the folder chosen in Settings, then backend/data
    data_dir: Path = Field(default_factory=_chosen_data_dir, validate_default=True)
    database_url: str = ""  # overrides the database chosen in the UI (data/database.json); see app/db/config.py
    cors_origins: list[str] = ["http://localhost:5173"]
    # Claude API credentials, when Claude is the AI provider: from the environment or backend/.env.
    anthropic_api_key: str = Field("", validation_alias="ANTHROPIC_API_KEY")
    anthropic_auth_token: str = Field("", validation_alias="ANTHROPIC_AUTH_TOKEN")
    stub_questions: bool = False  # stub agents ask a question per stage, to exercise the question UI without Claude

    @field_validator("data_dir")
    @classmethod
    def _anchored(cls, value: Path) -> Path:
        return BACKEND_DIR / value.expanduser()  # a relative path doesn't depend on where the server was started

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"  # each project's copy of its monolith repository (app/repos.py)


settings = Settings()
DATA_DIR_FROM_ENV = "data_dir" in settings.model_fields_set  # the Settings page can't change it then
