import json
import os
import re
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
    "cors_allowed_origins": "https://synthetic.example",
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


@pytest.mark.parametrize("app_env", ["preview", "production"])
@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://plaintext-secret.invalid",
        "https://user:plaintext-secret@example.upstash.io",
        "https://example.upstash.io/#plaintext-secret",
        "https://example.upstash.io?plaintext-secret=true",
        "https://example.upstash.io/",
        "https://EXAMPLE.upstash.io",
        "https://bad..upstash.io",
        "https://-bad.upstash.io",
        "not-a-url-plaintext-secret",
    ],
)
def test_runtime_rejects_noncanonical_or_unsafe_upstash_urls_without_echoing(
    app_env, unsafe_url
):
    values = {**VALID_RUNTIME_SETTINGS, "upstash_redis_rest_url": unsafe_url}

    with pytest.raises(ValidationError) as raised:
        Settings(app_env=app_env, **values, _env_file=None)

    assert "Invalid UPSTASH_REDIS_REST_URL" in str(raised.value)
    assert "plaintext-secret" not in str(raised.value)


def test_allowed_origins_trims_and_drops_empty_values():
    settings = Settings(
        cors_allowed_origins=" https://one.example , ,http://two.example,   ",
        _env_file=None,
    )

    assert settings.allowed_origins == ["https://one.example", "http://two.example"]


def test_vercel_preview_origin_regex_is_preview_only_and_tightly_scoped():
    preview = Settings(
        vercel_env="preview",
        cors_allowed_origins="https://shajraheritage.vercel.app",
        _env_file=None,
    )
    production = Settings(
        vercel_env="production",
        cors_allowed_origins="https://shajraheritage.vercel.app",
        _env_file=None,
    )

    assert preview.allowed_origin_regex is not None
    assert production.allowed_origin_regex is None

    allowed = (
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-git-codex-recover-95ea0c-ashartanveercs-dels-projects.vercel.app",
    )
    rejected = (
        "http://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://backend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app",
        "https://frontend-6ilwmwtze-another-team.vercel.app",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app.evil.example",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app:443",
        "https://frontend-6ilwmwtze-ashartanveercs-dels-projects.vercel.app/tree",
    )

    assert all(re.fullmatch(preview.allowed_origin_regex, origin) for origin in allowed)
    assert not any(re.fullmatch(preview.allowed_origin_regex, origin) for origin in rejected)


@pytest.mark.parametrize(
    "invalid_origins",
    [
        "",
        "*",
        "http://example.com",
        "https://*.example.com",
        "https://example.com:443",
        "https://example.com:bad",
        "https://example..com",
        "https://-bad.example",
        "https://\u0131.example",
        "https://\u017f.example",
        "https://example.com/",
        "https://example.com/path",
        "https://example.com?query=true",
        "https://example.com#fragment",
    ],
)
def test_runtime_rejects_noncanonical_cors_origins(invalid_origins):
    with pytest.raises(ValidationError, match="Invalid CORS_ALLOWED_ORIGINS"):
        Settings(
            app_env="preview",
            **{**VALID_RUNTIME_SETTINGS, "cors_allowed_origins": invalid_origins},
            _env_file=None,
        )


def test_vercel_environment_mismatch_is_read_only_and_uses_runtime_environment():
    settings = Settings(
        app_env="development",
        vercel_env="production",
        cors_allowed_origins="https://shajraheritage.vercel.app",
        public_writes_enabled=True,
        relationship_writes_enabled=True,
        normalized_reads_enabled=True,
        _env_file=None,
    )

    assert settings.runtime_environment == "production"
    assert settings.environment_mismatch is True
    assert settings.effective_public_writes_enabled is False
    assert settings.effective_relationship_writes_enabled is False
    assert settings.effective_normalized_reads_enabled is False


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
