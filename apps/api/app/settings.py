"""Local configuration with environment overrides."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVENTSCOUT_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    database_url: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "eventscout"
    intent_model: str = "gpt-5.6-luna"

    def missing(self, *names: str) -> list[str]:
        """Environment variables a command needs that are unset or blank."""
        return [
            f"EVENTSCOUT_{name.upper()}"
            for name in names
            if (value := getattr(self, name)) is None or not value.get_secret_value().strip()
        ]
