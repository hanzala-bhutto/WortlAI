"""Version 1 of the domain API.

Every domain router (session, voice WebSocket, review, ingest, progress) mounts
on the router below, so all of them land under /api/v1. Probes stay out of here
on purpose - see app/api/health.py.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")

# Routers are attached here as their issues land:
#   from app.api.v1 import session, voice
#   router.include_router(session.router)
