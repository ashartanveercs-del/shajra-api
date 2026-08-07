import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
import pydantic_settings


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import Settings


VALID_RUNTIME_SETTINGS = {
    "airtable_pat": "test-token",
    "airtable_base_id": "app-test",
    "admin_username": "admin",
    "admin_password_hash": "test-password-hash",
    "jwt_secret": "x" * 32,
    "mutation_preview_secret": "test-mutation-preview-secret",
    "upstash_redis_rest_url": "https://example.upstash.io",
    "upstash_redis_rest_token": "test-upstash-token",
    "redis_namespace": "preview-1",
    "redis_key_hmac_secret": "test-hmac-secret",
}

REQUIRED_SETTINGS = (
    "airtable_pat",
    "airtable_base_id",
    "admin_username",
    "admin_password_hash",
    "jwt_secret",
    "mutation_preview_secret",
    "upstash_redis_rest_url",
    "upstash_redis_rest_token",
    "redis_namespace",
    "redis_key_hmac_secret",
)

COMPATIBILITY_ENV = {
    "APP_ENV": "test",
    "AIRTABLE_PAT": "synthetic-airtable-pat",
    "AIRTABLE_BASE_ID": "app-synthetic",
    "GROQ_API_KEY": "synthetic-groq-key",
    "CLOUDINARY_URL": "synthetic-cloudinary-url",
    "ADMIN_USERNAME": "synthetic-admin",
    "ADMIN_PASSWORD_HASH": "synthetic-password-hash",
    "JWT_SECRET": "synthetic-jwt-secret",
    "MUTATION_PREVIEW_SECRET": "synthetic-mutation-preview-secret",
    "JWT_ISSUER": "synthetic-issuer",
    "JWT_AUDIENCE": "synthetic-audience",
    "CORS_ALLOWED_ORIGINS": "https://synthetic.example",
    "PUBLIC_WRITES_ENABLED": "false",
    "RELATIONSHIP_WRITES_ENABLED": "false",
    "NORMALIZED_READS_ENABLED": "false",
    "PYTHONPATH": str(Path(pydantic_settings.__file__).resolve().parent.parent),
}

if "SYSTEMROOT" in os.environ:
    COMPATIBILITY_ENV["SYSTEMROOT"] = os.environ["SYSTEMROOT"]


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
    assert isinstance(settings.upstash_redis_rest_token, SecretStr)
    assert isinstance(settings.redis_key_hmac_secret, SecretStr)


def test_allowed_origins_trims_and_drops_empty_values():
    settings = Settings(
        cors_allowed_origins=" https://one.example , ,http://two.example,   ",
        _env_file=None,
    )

    assert settings.allowed_origins == ["https://one.example", "http://two.example"]


def test_legacy_compatibility_exports_supply_current_v1_runtime_values():
    script = """
import json
import config

expected_values = {
    "AIRTABLE_PAT": "synthetic-airtable-pat",
    "AIRTABLE_BASE_ID": "app-synthetic",
    "GROQ_API_KEY": "synthetic-groq-key",
    "ADMIN_USERNAME": "synthetic-admin",
    "ADMIN_PASSWORD": "synthetic-password-hash",
    "JWT_SECRET": "synthetic-jwt-secret",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRATION_MINUTES": 60 * 24,
    "APPROVED_MEMBERS_TABLE": "ApprovedMembers",
    "PENDING_SUBMISSIONS_TABLE": "PendingSubmissions",
    "APPROVED_EMAILS_TABLE": "ApprovedEmails",
    "ALBUMS_TABLE": "Albums",
    "PHOTOS_TABLE": "Photos",
}
string_exports = (
    "AIRTABLE_PAT",
    "AIRTABLE_BASE_ID",
    "GROQ_API_KEY",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "JWT_ALGORITHM",
    "APPROVED_MEMBERS_TABLE",
    "PENDING_SUBMISSIONS_TABLE",
    "APPROVED_EMAILS_TABLE",
    "ALBUMS_TABLE",
    "PHOTOS_TABLE",
)

print(json.dumps({
    "values_match": all(getattr(config, name) == value for name, value in expected_values.items()),
    "string_exports": all(isinstance(getattr(config, name), str) for name in string_exports),
    "expiration_is_int": isinstance(config.JWT_EXPIRATION_MINUTES, int),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        cwd=BACKEND_DIR,
        env=COMPATIBILITY_ENV,
        text=True,
    )
    exports = json.loads(result.stdout)

    assert exports == {
        "values_match": True,
        "string_exports": True,
        "expiration_is_int": True,
    }

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
