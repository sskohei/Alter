from alter_api.contracts.frontend_preview import create_contract_app
from alter_api.main import create_app


def test_preview_contract_is_not_exposed_as_live_api() -> None:
    assert "/api/v1/agents" in create_contract_app().openapi()["paths"]
    live = create_app().openapi()["paths"]
    # #15 now exposes the real agent API; preview-only endpoints/operation IDs stay unmounted.
    assert "/api/v1/agents" in live
    assert "/__mock/profiles" not in live
    assert "/api/v1/agents/{agent_id}/profile/suggest" in live
    for path in live.values():
        for method, operation in path.items():
            if method in {"get", "post", "patch", "put", "delete"}:
                assert not operation["operationId"].startswith("preview_")


def test_private_info_is_only_in_input_and_edit_response() -> None:
    schemas = create_contract_app().openapi()["components"]["schemas"]
    assert "private_info" in schemas["AgentDraftInput"]["properties"]
    assert "private_info" in schemas["PrivateInfo"]["properties"]
    assert "private_info" not in schemas["Agent"]["properties"]
    assert "private_info" not in schemas["ProfileRevision"]["properties"]


def test_activity_preview_reuses_the_live_contract() -> None:
    preview = create_contract_app().openapi()
    live = create_app().openapi()
    for path in (
        "/api/v1/conversations",
        "/api/v1/conversations/{id}",
        "/api/v1/notifications",
        "/api/v1/notifications/{id}/read",
    ):
        assert preview["paths"][path] == live["paths"][path]
    for name in (
        "ConversationSummary",
        "ConversationDetail",
        "ConversationMessage",
        "Notification",
        "Page_ConversationSummary_",
        "Page_Notification_",
    ):
        assert preview["components"]["schemas"][name] == live["components"]["schemas"][name]


def test_memory_preview_reuses_formal_contract() -> None:
    preview = create_contract_app().openapi()
    live = create_app().openapi()
    for path in ("/api/v1/memories", "/api/v1/memories/{id}", "/api/v1/relationships"):
        assert preview["paths"][path] == live["paths"][path]
    for name in ("Memory", "Relationship", "Page_Memory_", "Page_Relationship_"):
        assert preview["components"]["schemas"][name] == live["components"]["schemas"][name]


def test_account_preview_reuses_formal_contract() -> None:
    preview = create_contract_app().openapi()
    live = create_app().openapi()
    for path in ("/api/v1/users/me", "/api/v1/preferences", "/api/v1/users/me/deletion"):
        assert preview["paths"][path] == live["paths"][path]
    for name in (
        "Profile",
        "ProfilePatch",
        "Preferences",
        "PreferencesPatch",
        "DeletionInput",
        "DeletionRequest",
    ):
        assert preview["components"]["schemas"][name] == live["components"]["schemas"][name]
