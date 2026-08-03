import pytest
from pydantic import SecretStr, ValidationError

import config
from config import Settings


VALID_RUNTIME_SETTINGS = {
    "airtable_pat": "test-token",
    "airtable_base_id": "app-test",
    "admin_username": "admin",
    "admin_password_hash": "test-password-hash",
    "jwt_secret": "x" * 32,
    "mutation_preview_secret": "test-mutation-preview-secret",
}

REQUIRED_SETTINGS = (
    "airtable_pat",
    "airtable_base_id",
    "admin_username",
    "admin_password_hash",
    "jwt_secret",
    "mutation_preview_secret",
)


def test_production_rejects_missing_secrets():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            **dict.fromkeys(REQUIRED_SETTINGS),
            _env_file=None,
        )


@pytest.mark.parametrize("app_env", ["preview", "production"])
def test_runtime_environments_reject_missing_required_settings(app_env):
    with pytest.raises(ValidationError):
        Settings(
            app_env=app_env,
            **dict.fromkeys(REQUIRED_SETTINGS),
            _env_file=None,
        )


@pytest.mark.parametrize("app_env", ["preview", "production"])
@pytest.mark.parametrize("setting_name", REQUIRED_SETTINGS)
@pytest.mark.parametrize("invalid_value", ["", "   "])
def test_runtime_environments_reject_empty_or_whitespace_required_settings(
    app_env, setting_name, invalid_value
):
    settings = {**VALID_RUNTIME_SETTINGS, setting_name: invalid_value}

    with pytest.raises(ValidationError, match=setting_name.upper()):
        Settings(app_env=app_env, **settings, _env_file=None)


def test_runtime_settings_keep_secrets_typed():
    settings = Settings(app_env="preview", **VALID_RUNTIME_SETTINGS, _env_file=None)

    assert isinstance(settings.airtable_pat, SecretStr)
    assert isinstance(settings.admin_password_hash, SecretStr)
    assert isinstance(settings.jwt_secret, SecretStr)
    assert isinstance(settings.mutation_preview_secret, SecretStr)


def test_allowed_origins_trims_and_drops_empty_values():
    settings = Settings(
        cors_allowed_origins=" https://one.example , ,http://two.example,   ",
        _env_file=None,
    )

    assert settings.allowed_origins == ["https://one.example", "http://two.example"]


def test_legacy_compatibility_exports_supply_current_v1_runtime_values():
    assert config.AIRTABLE_PAT == "test-token"
    assert config.AIRTABLE_BASE_ID == "app-test"
    assert config.GROQ_API_KEY is None
    assert config.ADMIN_USERNAME == "admin-test"
    assert config.ADMIN_PASSWORD == "test-only-hash"
    assert config.JWT_SECRET == "test-secret-at-least-32-characters-long"
    assert config.JWT_ALGORITHM == "HS256"
    assert config.JWT_EXPIRATION_MINUTES == 60 * 24
    assert config.APPROVED_MEMBERS_TABLE == "ApprovedMembers"
    assert config.PENDING_SUBMISSIONS_TABLE == "PendingSubmissions"
    assert config.APPROVED_EMAILS_TABLE == "ApprovedEmails"
    assert config.ALBUMS_TABLE == "Albums"
    assert config.PHOTOS_TABLE == "Photos"

    assert all(
        isinstance(value, str)
        for value in (
            config.AIRTABLE_PAT,
            config.AIRTABLE_BASE_ID,
            config.ADMIN_USERNAME,
            config.ADMIN_PASSWORD,
            config.JWT_SECRET,
        )
    )


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
