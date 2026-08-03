import pytest
from pydantic import ValidationError

from config import Settings


def test_production_rejects_missing_secrets():
    with pytest.raises(ValidationError):
        Settings(app_env="production", _env_file=None)


def test_test_environment_defaults_all_writes_off():
    settings = Settings(
        app_env="test",
        airtable_pat="test-token",
        airtable_base_id="app-test",
        admin_username="admin",
        admin_password_hash="hash",
        jwt_secret="x" * 32,
        jwt_issuer="shajra-test",
        jwt_audience="shajra-admin-test",
        _env_file=None,
    )
    assert settings.public_writes_enabled is False
    assert settings.relationship_writes_enabled is False
    assert settings.normalized_reads_enabled is False
