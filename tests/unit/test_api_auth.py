from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
import pytest

from agentkit.api.auth import verify_api_key
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.errors import AuthenticationError


@pytest.mark.asyncio
async def test_verify_api_key_direct() -> None:
    settings = Settings(API_KEYS="valid_key_1, valid_key_2")

    # Valid keys succeed
    assert await verify_api_key(api_key="valid_key_1", settings=settings) == "valid_key_1"
    assert await verify_api_key(api_key="valid_key_2", settings=settings) == "valid_key_2"

    # Missing key raises AuthenticationError
    with pytest.raises(AuthenticationError):
        await verify_api_key(api_key=None, settings=settings)

    # Empty key raises AuthenticationError
    with pytest.raises(AuthenticationError):
        await verify_api_key(api_key="", settings=settings)

    # Invalid key raises AuthenticationError
    with pytest.raises(AuthenticationError):
        await verify_api_key(api_key="wrong_key", settings=settings)


def test_api_key_auth_endpoint_integration() -> None:
    settings = Settings(API_KEYS="secret_token_123")
    app = create_app(settings=settings)
    router = APIRouter()

    @router.get("/protected", dependencies=[Depends(verify_api_key)])
    def protected_route() -> dict[str, str]:
        return {"status": "authorized"}

    @router.get("/public")
    def public_route() -> dict[str, str]:
        return {"status": "public"}

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    # Public route does not require auth
    resp_pub = client.get("/public")
    assert resp_pub.status_code == 200
    assert resp_pub.json() == {"status": "public"}

    # Protected route with missing header -> 401
    resp_missing = client.get("/protected")
    assert resp_missing.status_code == 401
    data_missing = resp_missing.json()
    assert data_missing["error"]["code"] == "UNAUTHORIZED"

    # Protected route with invalid header -> 401
    resp_invalid = client.get("/protected", headers={"X-API-Key": "wrong_token"})
    assert resp_invalid.status_code == 401
    data_invalid = resp_invalid.json()
    assert data_invalid["error"]["code"] == "UNAUTHORIZED"

    # Protected route with valid header -> 200
    resp_valid = client.get("/protected", headers={"X-API-Key": "secret_token_123"})
    assert resp_valid.status_code == 200
    assert resp_valid.json() == {"status": "authorized"}
