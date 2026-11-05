from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 非対称署名だけを JWKS で検証する。HS256 は JWKS では検証せず Auth サーバー照会に回す。
SUPPORTED_JWKS_ALGORITHMS = frozenset({"ES256", "RS256"})


class Settings(BaseSettings):
    """Shared local configuration. External actions are disabled in this scaffold."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    alter_mode: Literal["mock", "live"] = "mock"
    discord_enabled: bool = False
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"
    cors_origins: str = "http://localhost:3000"

    # Supabase Auth（API の JWT 検証）
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_publishable_key: str = ""
    supabase_jwt_issuer: str = "http://127.0.0.1:54321/auth/v1"
    supabase_jwt_audience: str = "authenticated"
    # 許可する署名アルゴリズム（CSV）。トークンのヘッダー値で動的に選ばない。
    supabase_jwt_algorithms: str = "ES256,RS256"
    # JWKS を短時間キャッシュする秒数
    supabase_jwks_cache_seconds: int = 300

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: str) -> str:
        if any(origin.strip() == "*" for origin in value.split(",")):
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return value

    @field_validator("supabase_jwt_algorithms")
    @classmethod
    def _check_algorithms(cls, value: str) -> str:
        algorithms = {alg.strip() for alg in value.split(",") if alg.strip()}
        if not algorithms or not algorithms <= SUPPORTED_JWKS_ALGORITHMS:
            raise ValueError(
                f"SUPABASE_JWT_ALGORITHMS must be a subset of {sorted(SUPPORTED_JWKS_ALGORITHMS)}"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def jwt_algorithm_list(self) -> list[str]:
        return sorted(alg.strip() for alg in self.supabase_jwt_algorithms.split(",") if alg.strip())

    def require_mock(self) -> None:
        if self.alter_mode != "mock":
            raise RuntimeError(
                "Live processing is not implemented in this scaffold; use ALTER_MODE=mock"
            )
