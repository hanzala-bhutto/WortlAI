"""Operational endpoints and the OpenAPI contract they publish."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# No frontend build, so these tests behave the same whether or not someone has
# run `npm run build` locally.
app = create_app(frontend_dist=Path("no-frontend-build"))
client = TestClient(app)


def test_health_reports_ok_and_key_status():
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    # /health must say which credentials exist so a bad .env is visible early.
    assert set(body["keys_configured"]) == {"groq", "nim", "langfuse"}
    assert all(isinstance(v, bool) for v in body["keys_configured"].values())


def test_readyz_reports_qdrant_without_failing_when_it_is_down(monkeypatch):
    """A cold Qdrant is reported, not fatal - nothing needs it until Phase 2."""

    async def refuse(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", refuse)

    response = client.get("/readyz")
    assert response.status_code == 200

    body = response.json()
    assert body["qdrant"]["ready"] is False
    assert "connection refused" in body["qdrant"]["error"]


def test_readyz_reports_qdrant_ready_when_it_answers(monkeypatch):
    async def ok(*args, **kwargs):
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", ok)

    body = client.get("/readyz").json()
    assert body["qdrant"]["ready"] is True
    assert body["qdrant"]["error"] is None


# --- Contract: operational probes stay unversioned, domain routes do not ---


def test_probes_are_not_versioned():
    """Container probes and /dev depend on a stable address; /api/v1 must not
    swallow them."""
    schema = app.openapi()

    assert "/health" in schema["paths"]
    assert "/readyz" in schema["paths"]
    assert not any(path.startswith("/api/") for path in ("/health", "/readyz"))


def test_domain_routes_live_under_api_v1():
    """Every non-probe, non-docs route is versioned."""
    unversioned = [
        path
        for path in app.openapi()["paths"]
        if not path.startswith("/api/v1") and path not in {"/health", "/readyz", "/"}
    ]
    assert unversioned == []


@pytest.mark.parametrize("path", ["/health", "/readyz"])
def test_endpoints_publish_a_real_response_schema(path):
    """An untyped `-> dict` handler yields `additionalProperties: true`, which is
    a useless contract for the frontend. Every endpoint declares a model."""
    schema = app.openapi()
    json_schema = schema["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]

    assert "$ref" in json_schema, f"{path} does not declare a response_model"

    model_name = json_schema["$ref"].rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][model_name]["properties"]
    assert "status" in properties
