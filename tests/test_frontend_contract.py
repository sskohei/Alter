from alter_api.contracts.frontend_preview import create_contract_app
from alter_api.main import create_app


def test_preview_contract_is_not_exposed_as_live_api() -> None:
    assert "/api/v1/agents" in create_contract_app().openapi()["paths"]
    assert "/api/v1/agents" not in create_app().openapi()["paths"]


def test_private_info_is_only_in_input_and_edit_response() -> None:
    schemas = create_contract_app().openapi()["components"]["schemas"]
    assert "private_info" in schemas["AgentDraftInput"]["properties"]
    assert "private_info" in schemas["PrivateInfo"]["properties"]
    assert "private_info" not in schemas["Agent"]["properties"]
    assert "private_info" not in schemas["ProfileRevision"]["properties"]
