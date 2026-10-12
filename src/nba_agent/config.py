from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql://nba_agent_app:local-prototype-only@localhost:5432/nba_agent"
    )
    admin_database_url: SecretStr = SecretStr("postgresql://aadhar@localhost:5432/postgres")
    openai_api_key: SecretStr = SecretStr("")
    gatekeeper_model: str = "gpt-4.1-mini"
    analyst_model: str = "gpt-4.1-mini"
    data_dir: Path = Path("data/raw/kaggle/v515")
    manifest_path: Path = Path("audit/reports/snapshot_manifest.json")
    host: str = "127.0.0.1"
    port: int = 8001

    @property
    def llm_configured(self) -> bool:
        key = self.openai_api_key.get_secret_value().strip()
        return bool(key) and key != "replace-with-your-openai-api-key"
