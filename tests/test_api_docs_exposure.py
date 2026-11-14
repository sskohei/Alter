"""#34: 本番 API では Swagger UI（/docs・/redoc）を閉じ、/openapi.json は公開する。"""

import pytest
from alter_api.main import create_app
from alter_domain.settings import Settings
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _client(environment: str) -> TestClient:
    settings = Settings(_env_file=None, alter_environment=environment)  # type: ignore[call-arg]
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_production_hides_swagger_ui_but_keeps_openapi() -> None:
    client = _client("production")
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "NOT_FOUND"
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["paths"]["/health"]["get"]["operationId"] == "get_health"


@pytest.mark.parametrize("environment", ["local", "preview"])
def test_non_production_keeps_swagger_ui(environment: str) -> None:
    client = _client(environment)
    for path in ("/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]
    assert client.get("/openapi.json").status_code == 200


def test_default_environment_is_local() -> None:
    assert Settings(_env_file=None).alter_environment == "local"  # type: ignore[call-arg]


def test_unknown_environment_is_rejected() -> None:
    # 綴りの誤り（"prod" など）で本番の Swagger UI が開いたままにならないよう、起動時に止める
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alter_environment="prod")  # type: ignore[call-arg]


def test_openapi_is_identical_across_environments() -> None:
    local = _client("local").get("/openapi.json").json()
    production = _client("production").get("/openapi.json").json()
    assert local == production
