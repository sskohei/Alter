import pytest
from alter_api.settings_routes import AgentSettingsPatch
from pydantic import ValidationError


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"daily_budget_usd": -1},
        {"frequency_per_day": 3},
        {"autonomy_enabled": None},
        {"web_visibility": "public"},
        {"owner_id": "untrusted"},
    ],
)
def test_rejects_invalid_settings_contract(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentSettingsPatch.model_validate(body)


def test_stop_patch_does_not_grant_other_consents() -> None:
    value = AgentSettingsPatch(autonomy_enabled=False)
    assert value.model_dump(exclude_unset=True) == {"autonomy_enabled": False}


def test_preview_uses_formal_settings_and_block_contracts() -> None:
    from alter_api.contracts.frontend_preview import create_contract_app
    from alter_api.main import create_app

    live = create_app().openapi()
    preview = create_contract_app().openapi()
    for path in ("/api/v1/agents/{id}/settings", "/api/v1/agents/{id}/blocks/{other_id}"):
        assert live["paths"][path] == preview["paths"][path]
    for name in ("AgentSettings", "AgentSettingsPatch"):
        assert live["components"]["schemas"][name] == preview["components"]["schemas"][name]
