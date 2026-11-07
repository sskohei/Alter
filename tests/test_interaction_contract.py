import pytest
from alter_api.contracts.interaction_preview import AgentSettingsPatch
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
