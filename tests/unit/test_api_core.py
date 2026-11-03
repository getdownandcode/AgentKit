from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agentkit.api.main import create_app
from agentkit.api.schemas import RunResponse
from agentkit.core.errors import ToolNotFoundError, ToolSecurityError
from agentkit.core.types import RunStatus


def test_app_creation() -> None:
    app = create_app()
    assert app.title == "AgentKit API"


def test_run_response_status_is_a_run_status_enum() -> None:
    """The API boundary should validate statuses and publish their enum values."""
    response = RunResponse.model_validate(
        {
            "run_id": "run-1",
            "session_id": None,
            "goal": "test",
            "status": "succeeded",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    assert response.status is RunStatus.SUCCEEDED


def test_domain_exception_handler() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/test-not-found")
    def raise_not_found() -> None:
        raise ToolNotFoundError("unknown_tool")

    @router.get("/test-security-error")
    def raise_security_error() -> None:
        raise ToolSecurityError("SSRF detected")

    app.include_router(router)

    client = TestClient(app, raise_server_exceptions=False)

    resp_404 = client.get("/test-not-found")
    assert resp_404.status_code == 404
    data_404 = resp_404.json()
    assert data_404["error"]["code"] == "TOOL_NOT_FOUND"
    assert "unknown_tool" in data_404["error"]["message"]

    resp_400 = client.get("/test-security-error")
    assert resp_400.status_code == 400
    data_400 = resp_400.json()
    assert data_400["error"]["code"] == "TOOL_SECURITY_ERROR"
    assert "SSRF detected" in data_400["error"]["message"]


def test_validation_exception_handler() -> None:
    app = create_app()
    router = APIRouter()

    class ItemPayload(BaseModel):
        count: int

    @router.post("/test-validation")
    def validate_item(payload: ItemPayload) -> dict[str, int]:
        return {"count": payload.count}

    app.include_router(router)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/test-validation", json={"count": "not-an-int"})
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert (
        "validation" in data["error"]["message"].lower()
        or "input" in data["error"]["message"].lower()
    )


def test_internal_exception_handler() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/test-crash")
    def crash() -> None:
        raise RuntimeError("Something exploded")

    app.include_router(router)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test-crash")
    assert resp.status_code == 500
    data = resp.json()
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert data["error"]["message"] == "An unexpected internal server error occurred."


def test_http_exception_handler() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/test-http-error")
    def raise_http() -> None:
        raise HTTPException(status_code=403, detail="Forbidden resource")

    app.include_router(router)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test-http-error")
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "HTTP_ERROR"
    assert data["error"]["message"] == "Forbidden resource"
