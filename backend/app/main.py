"""FastAPI entrypoint. Run from backend/: uvicorn app.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api import health, v1
from app.config import get_settings
from app.learner.migrate import upgrade_to_head
from app.static import FRONTEND_DIST, mount_frontend


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Create or migrate the learner DB on boot so a fresh clone needs no manual
    # `alembic upgrade`. Runs on real startup only; the test client instantiated
    # without a `with` block never triggers it, keeping the suite hermetic.
    upgrade_to_head()
    yield


class ServiceInfo(BaseModel):
    service: str
    docs: str
    health: str


def create_app(frontend_dist: Path = FRONTEND_DIST) -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="WortlAI",
        description=(
            "Voice-first German fluency trainer.\n\n"
            "Domain routes are versioned under `/api/v1`. `/health` and `/readyz` "
            "are unversioned so probes have a stable address."
        ),
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness probes."},
            {"name": "learner", "description": "Sessions, errors and immersion hours."},
        ],
    )

    # Only needed when the frontend is served from its own origin, i.e. the Vite
    # dev server hitting uvicorn directly. In production FastAPI serves the built
    # app itself, so requests are same-origin and this does nothing.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(v1.router)

    # Registered last: its catch-all must lose to every route above.
    if not mount_frontend(app, frontend_dist):

        @app.get("/", response_model=ServiceInfo, include_in_schema=False)
        async def root() -> ServiceInfo:
            return ServiceInfo(
                service="wortlai-backend", docs="/docs", health="/health"
            )

    return app


app = create_app()
