from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Shared local configuration. External actions are disabled in this scaffold."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    alter_mode: Literal["mock", "live"] = "mock"
    discord_enabled: bool = False
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"
    cors_origins: str = "http://localhost:3000"

    def require_mock(self) -> None:
        if self.alter_mode != "mock":
            raise RuntimeError(
                "Live processing is not implemented in this scaffold; use ALTER_MODE=mock"
            )
