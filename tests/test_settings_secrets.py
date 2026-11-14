"""#32: 設定値の秘密が repr・文字列化・JSON に出ないこと。"""

import json

from alter_domain.settings import Settings
from pydantic import SecretStr

SECRETS = {
    "supabase_secret_key": "SECRET-supabase-admin",
    "supabase_storage_secret_key": "SECRET-storage-admin",
    "anthropic_api_key": "SECRET-anthropic",
    "discord_client_secret": "SECRET-discord-client",
    "discord_bot_token": "SECRET-discord-bot",
    "profile_encryption_key_v1": "SECRET-profile-key",
}
DB_PASSWORD = "SECRET-db-password"


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=f"postgresql+psycopg://postgres.ref:{DB_PASSWORD}@pooler.example/postgres",
        api_idempotency_hmac_keys={1: SecretStr("SECRET-hmac")},
        **SECRETS,
    )


def test_repr_and_str_hide_every_secret() -> None:
    settings = _settings()
    for text in (repr(settings), str(settings)):
        for value in [*SECRETS.values(), DB_PASSWORD, "SECRET-hmac"]:
            assert value not in text


def test_json_dump_masks_secret_fields() -> None:
    dumped = json.dumps(_settings().model_dump(mode="json"))
    for value in [*SECRETS.values(), "SECRET-hmac"]:
        assert value not in dumped


def test_every_key_like_field_is_secret() -> None:
    # 鍵・秘密・token の項目を足したら SecretStr にする（公開キーは除く）
    public = {
        "supabase_publishable_key",
        "discord_client_id",
        "api_idempotency_hmac_active_version",
    }
    for name, field in Settings.model_fields.items():
        if name in public or not any(w in name for w in ("key", "secret", "token", "password")):
            continue
        assert field.annotation is SecretStr or "SecretStr" in str(field.annotation), name


def test_secret_values_are_still_usable() -> None:
    settings = _settings()
    assert settings.supabase_secret_key.get_secret_value() == SECRETS["supabase_secret_key"]
    assert settings.discord_client_secret.get_secret_value() == SECRETS["discord_client_secret"]
    assert DB_PASSWORD in settings.database_url
