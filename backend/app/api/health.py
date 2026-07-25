"""Health and readiness probes.

Deliberately unversioned: container probes and the /dev skill need a stable
address. Domain routes are versioned instead, under /api/v1.

/health is cheap and touches nothing. /readyz reaches out to Qdrant, so it is the
one to call after `docker compose up -d`.
"""

from typing import Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])


class KeysConfigured(BaseModel):
    groq: bool
    nim: bool
    langfuse: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    keys_configured: KeysConfigured


class QdrantStatus(BaseModel):
    url: str
    ready: bool
    error: str | None = Field(
        default=None, description="Present only when Qdrant could not be reached."
    )


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description=(
            "degraded when a required API key is missing. A cold Qdrant is "
            "reported but does not make the backend unready - nothing needs it "
            "until Phase 2 ingestion."
        )
    )
    qdrant: QdrantStatus
    keys_configured: KeysConfigured


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health() -> HealthResponse:
    settings: Settings = get_settings()
    return HealthResponse(
        status="ok",
        service="wortlai-backend",
        keys_configured=KeysConfigured(**settings.configured_keys()),
    )


@router.get("/readyz", response_model=ReadinessResponse, summary="Readiness")
async def readyz() -> ReadinessResponse:
    settings: Settings = get_settings()

    qdrant = QdrantStatus(url=settings.qdrant_url, ready=False)
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.qdrant_url}/readyz")
        qdrant.ready = response.status_code == 200
    except httpx.HTTPError as exc:
        qdrant.error = f"{type(exc).__name__}: {exc}"

    keys = settings.configured_keys()
    return ReadinessResponse(
        status="ok" if keys["groq"] else "degraded",
        qdrant=qdrant,
        keys_configured=KeysConfigured(**keys),
    )
