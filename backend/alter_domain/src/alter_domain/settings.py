from decimal import Decimal
from typing import Literal

from pydantic import Field, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 非対称署名だけを JWKS で検証する。HS256 は JWKS では検証せず Auth サーバー照会に回す。
SUPPORTED_JWKS_ALGORITHMS = frozenset({"ES256", "RS256"})


class Settings(BaseSettings):
    """Shared configuration. Paid LLM calls require live mode and explicit enablement."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    alter_mode: Literal["mock", "live"] = "mock"
    # 配置先の環境。production では API の Swagger UI（/docs・/redoc）を出さない（#34）
    alter_environment: Literal["local", "preview", "production"] = "local"
    discord_enabled: bool = False
    scheduler_enabled: bool = False
    scheduler_batch_size: int = Field(default=10, ge=1, le=10)
    profile_encryption_key_v1: SecretStr = SecretStr("")
    api_idempotency_hmac_keys: dict[PositiveInt, SecretStr] = Field(default_factory=dict)
    api_idempotency_hmac_active_version: PositiveInt = 1
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"
    database_pool_size: int = Field(default=2, ge=1, le=5)
    database_max_overflow: int = Field(default=0, ge=0, le=2)
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
    # Supabase Auth 管理 API（招待の identity 照合）。API と運営 CLI のサーバー側だけで使う。
    supabase_secret_key: str = ""
    supabase_storage_secret_key: SecretStr = SecretStr("")

    # 交流設定（#16）。利用者が設定できる 1 体あたりの日次予算の上限（USD、小数 6 桁まで）
    agent_daily_budget_max_usd: Decimal = Decimal("1.00")
    # 自律交流 ON の同意文面の版（docs/23）。未設定なら ON にできない（送信先 LLM 未決定を含む）
    alter_consent_version: str = ""

    # API/Worker only. A key alone never enables paid calls.
    llm_enabled: bool = False
    anthropic_api_key: SecretStr = SecretStr("")
    max_daily_cost_usd: Decimal = Decimal("1.00")

    # Discord 本人連携（#26）。シークレットは API だけに置く（docs/14）。未設定なら連携 API は 503
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""

    # Discord Bot（#27）。トークンは Bot プロセスだけに置く。Web の URL は案内リンクに使う
    discord_bot_token: SecretStr = SecretStr("")
    alter_web_url: str = "http://localhost:3000"

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: str) -> str:
        if any(origin.strip() == "*" for origin in value.split(",")):
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return value

    @field_validator("agent_daily_budget_max_usd", "max_daily_cost_usd")
    @classmethod
    def _check_budget_max(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.000001")):
            raise ValueError("Budget limits must be >= 0 with at most 6 decimals")
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
    def api_docs_enabled(self) -> bool:
        """Swagger UI / ReDoc を出すか。/openapi.json は環境によらず公開する。"""
        return self.alter_environment != "production"

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
