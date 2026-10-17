from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    owner_password_hash: SecretStr = SecretStr("")
    owner_totp_secret: SecretStr = SecretStr("")
    secure_cookies: bool = False
    chat_daily_request_limit: int = Field(default=500, ge=1)
    chat_daily_budget_usd: float = Field(default=5.0, gt=0)

    @property
    def owner_configured(self) -> bool:
        return bool(
            self.owner_password_hash.get_secret_value()
            and self.owner_totp_secret.get_secret_value()
        )

    @field_validator("data_dir", "manifest_path")
    @classmethod
    def resolve_repository_path(cls, value: Path) -> Path:
        """Resolve configured relative data paths against the repository, not the caller's cwd."""
        return value if value.is_absolute() else REPOSITORY_ROOT / value

    @property
    def llm_configured(self) -> bool:
        key = self.openai_api_key.get_secret_value().strip()
        return bool(key) and key != "replace-with-your-openai-api-key"
