"""Production serving: FastAPI hands out the built frontend.

One service, one origin - so there is no CORS and the voice WebSocket is
same-origin. In development the frontend runs on Vite instead and proxies here,
so these routes must not exist when there is no build to serve.
"""

from fastapi.testclient import TestClient

from app.static import mount_frontend


def build_dir(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>WortlAI</title>", "utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')", "utf-8")
    return dist


def test_no_frontend_routes_when_there_is_no_build(tmp_path):
    """Nothing is mounted in development, so a stale build can never shadow the
    API and a missing one is not an error."""
    from app.main import create_app

    app = create_app(frontend_dist=tmp_path / "does-not-exist")
    client = TestClient(app)

    assert client.get("/").status_code == 200  # the service-info route
    assert client.get("/some/spa/route").status_code == 404


def test_serves_index_and_assets_from_the_build(tmp_path):
    from app.main import create_app

    app = create_app(frontend_dist=build_dir(tmp_path))
    client = TestClient(app)

    assert "WortlAI" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200


def test_unknown_paths_fall_back_to_index_so_client_routing_works(tmp_path):
    """A deep link like /talk is a client route: the server must return index.html
    rather than 404, or a refresh on that page breaks."""
    from app.main import create_app

    client = TestClient(create_app(frontend_dist=build_dir(tmp_path)))

    response = client.get("/talk")
    assert response.status_code == 200
    assert "WortlAI" in response.text


def test_api_and_probes_win_over_the_spa_fallback(tmp_path):
    """The catch-all must never swallow backend routes."""
    from app.main import create_app

    client = TestClient(create_app(frontend_dist=build_dir(tmp_path)))

    assert client.get("/health").json()["status"] == "ok"
    # An unknown API path is a real 404, not the SPA shell.
    missing = client.get("/api/v1/nope")
    assert missing.status_code == 404
    assert "WortlAI" not in missing.text


def test_mount_frontend_is_a_noop_without_a_build(tmp_path):
    from app.main import create_app

    app = create_app(frontend_dist=tmp_path / "nothing")
    assert mount_frontend(app, tmp_path / "nothing") is False
