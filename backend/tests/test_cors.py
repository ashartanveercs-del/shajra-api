import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import Settings
from cors_policy import configure_cors


def _client(vercel_env: str | None) -> TestClient:
    app = FastAPI()

    @app.get("/api/health/live")
    def health_live():
        return {"status": "ok"}

    configure_cors(
        app,
        Settings(
            app_env="test",
            vercel_env=vercel_env,
            cors_allowed_origins="https://shajraheritage.vercel.app",
            _env_file=None,
        ),
    )
    return TestClient(app)


def test_cors_accepts_exact_and_matched_preview_origins_only():
    client = _client("preview")
    origins = (
        "https://shajraheritage.vercel.app",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-git-codex-recover-95ea0c-ashartanveercs-dels-projects.vercel.app",
        "https://backend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-6ilwmwtze-another-team.vercel.app",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app.evil.example",
    )
    results = {}
    for origin in origins:
        simple = client.get("/api/health/live", headers={"Origin": origin})
        preflight = client.options(
            "/api/admin/undo",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "authorization,content-type,x-idempotency-key"
                ),
            },
        )
        results[origin] = {
            "simple_status": simple.status_code,
            "simple_origin": simple.headers.get("access-control-allow-origin"),
            "simple_vary": simple.headers.get("vary"),
            "preflight_status": preflight.status_code,
            "preflight_origin": preflight.headers.get("access-control-allow-origin"),
            "preflight_methods": preflight.headers.get("access-control-allow-methods"),
            "preflight_headers": preflight.headers.get("access-control-allow-headers"),
        }

    allowed = (
        "https://shajraheritage.vercel.app",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-git-codex-recover-95ea0c-ashartanveercs-dels-projects.vercel.app",
    )
    denied = (
        "https://backend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-6ilwmwtze-another-team.vercel.app",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app.evil.example",
    )

    for origin in allowed:
        probe = results[origin]
        assert probe["simple_status"] == 200
        assert probe["simple_origin"] == origin
        assert probe["simple_vary"] == "Origin"
        assert probe["preflight_status"] == 200
        assert probe["preflight_origin"] == origin
        assert probe["preflight_methods"] == "DELETE, GET, POST, PUT"
        assert "Authorization" in probe["preflight_headers"]
        assert "Content-Type" in probe["preflight_headers"]
        assert "X-Idempotency-Key" in probe["preflight_headers"]

    for origin in denied:
        probe = results[origin]
        assert probe["simple_status"] == 200
        assert probe["simple_origin"] is None
        assert probe["preflight_status"] == 400
        assert probe["preflight_origin"] is None


@pytest.mark.parametrize("vercel_env", [None, "development", "production"])
def test_preview_regex_is_disabled_outside_vercel_preview(vercel_env):
    client = _client(vercel_env)
    preview_origin = (
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app"
    )

    exact = client.get(
        "/api/health/live",
        headers={"Origin": "https://shajraheritage.vercel.app"},
    )
    preview = client.get(
        "/api/health/live",
        headers={"Origin": preview_origin},
    )

    assert exact.headers["access-control-allow-origin"] == (
        "https://shajraheritage.vercel.app"
    )
    assert "access-control-allow-origin" not in preview.headers
