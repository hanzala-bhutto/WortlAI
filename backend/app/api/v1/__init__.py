"""Version 1 of the domain API.

Every domain router (session, voice WebSocket, review, ingest, progress) mounts
on the router below, so all of them land under /api/v1. Probes stay out of here
on purpose - see app/api/health.py.
"""

from fastapi import APIRouter

from app.api.v1 import learner, scenarios, voice

router = APIRouter(prefix="/api/v1")
router.include_router(learner.router)
router.include_router(scenarios.router)
router.include_router(voice.router)
