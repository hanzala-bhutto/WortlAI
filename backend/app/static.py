"""Serving the built frontend in production.

Development runs Vite on 3001, which proxies API paths here, so nothing is
mounted unless a build exists. Production builds the frontend to
frontend/dist and this module serves it from the same origin as the API.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import REPO_ROOT

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Prefixes the backend owns. An unknown path under one of these is a genuine 404,
# not a client route, so it must not receive the SPA shell.
BACKEND_PREFIXES = ("api/", "health", "readyz", "docs", "redoc", "openapi.json")


def mount_frontend(app: FastAPI, dist: Path = FRONTEND_DIST) -> bool:
    """Serve `dist` at the root, falling back to index.html for client routes.

    Returns whether anything was mounted, so callers can log the reason a build
    is not being served.
    """
    index = dist / "index.html"
    if not index.is_file():
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # Registered last so every API route and probe is matched first. Client
    # routes such as /talk have no server route, and must return the shell
    # rather than a 404 or a page reload there breaks.
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str) -> FileResponse:
        if path.startswith(BACKEND_PREFIXES):
            raise HTTPException(status_code=404)

        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    return True
