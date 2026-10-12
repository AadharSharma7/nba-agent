from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql://nba_agent_app:local-prototype-only@localhost:5432/nba_agent"
    )
    admin_database_url: SecretStr = SecretStr("postgresql://aadhar@localhost:5432/postgres")
    openai_api_key: SecretStr = SecretStr("")
    gatekeeper_model: str = "gpt-4.1-mini"
    analyst_model: str = "gpt-4.1-mini"
    data_dir: Path = REPOSITORY_ROOT / "data/raw/kaggle/v515"
    manifest_path: Path = REPOSITORY_ROOT / "audit/reports/snapshot_manifest.json"
    host: str = "127.0.0.1"
    port: int = 8001

    @field_validator("data_dir", "manifest_path")
    @classmethod
    def resolve_repository_path(cls, value: Path) -> Path:
        """Resolve configured relative data paths against the repository, not the caller's cwd."""
        return value if value.is_absolute() else REPOSITORY_ROOT / value

    @property
    def llm_configured(self) -> bool:
        key = self.openai_api_key.get_secret_value().strip()
        return bool(key) and key != "replace-with-your-openai-api-key"
