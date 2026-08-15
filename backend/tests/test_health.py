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
        "configured": {"airtable": True, "groq": True, "cloudinary": True},
        "writes": {"public": False, "relationships": False},
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
    }
    assert "secret" not in response.text


def test_admin_settings_routes_are_not_available():
    response = client.get("/api/admin/settings")

    assert response.status_code == 404
