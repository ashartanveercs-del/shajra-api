import main
from config import Settings
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_liveness_has_no_secret_values():
    response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_flags_without_values(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(
            app_env="test",
            airtable_pat="airtable-secret",
            airtable_base_id="app-test",
            groq_api_key="groq-secret",
            cloudinary_url="cloudinary-secret",
            _env_file=None,
        ),
    )

    response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "environment": "test",
        "environmentMismatch": False,
        "configured": {
            "airtable": True,
            "groq": True,
            "cloudinary": True,
            "coordination": False,
        },
        "writes": {
            "public": False,
            "relationships": False,
            "datastore": False,
        },
        "normalizedReads": False,
    }
    assert "secret" not in response.text


def test_admin_integration_status_is_read_only_and_secret_free(monkeypatch):
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(
            app_env="test",
            groq_api_key="groq-secret",
            cloudinary_url="cloudinary-secret",
            _env_file=None,
        ),
    )
    try:
        response = client.get("/api/admin/integrations")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "groqConfigured": True,
        "cloudinaryConfigured": True,
        "coordinationConfigured": False,
        "datastoreMutationsEnabled": False,
    }
    assert "secret" not in response.text


def test_main_app_cors_accepts_the_configured_exact_origin():
    origin = "http://localhost:3000"
    response = client.options(
        "/api/admin/undo",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-idempotency-key"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-methods"] == "DELETE, GET, POST, PUT"


def test_vercel_runtime_environment_never_uses_memory_history(monkeypatch):
    settings = Settings(
        app_env="development",
        vercel_env="production",
        cors_allowed_origins="https://shajraheritage.vercel.app",
        _env_file=None,
    )
    durable_store = object()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main.runtime_coordination,
        "build_change_history_store",
        lambda actual: durable_store if actual is settings else None,
    )

    assert main.get_change_history_store() is durable_store


def test_coordination_metadata_requires_complete_settings_without_network_calls(
    monkeypatch,
):
    settings = Settings(
        app_env="test",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="synthetic-upstash-secret",
        redis_namespace="test-1",
        redis_key_hmac_secret="synthetic-hmac-secret",
        _env_file=None,
    )
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    try:
        ready = client.get("/api/health/ready")
        integrations = client.get("/api/admin/integrations")
    finally:
        main.app.dependency_overrides.clear()

    assert ready.status_code == 200
    assert ready.json()["configured"]["coordination"] is True
    assert integrations.status_code == 200
    assert integrations.json()["coordinationConfigured"] is True
    assert "synthetic-upstash-secret" not in ready.text + integrations.text
    assert "synthetic-hmac-secret" not in ready.text + integrations.text


def test_admin_settings_routes_are_not_available():
    response = client.get("/api/admin/settings")

    assert response.status_code == 404
