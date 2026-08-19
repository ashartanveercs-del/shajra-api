"""Shajra configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from upstash_url import require_canonical_upstash_url


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
VERCEL_PREVIEW_FRONTEND_ORIGIN_REGEX = (
    r"^https://frontend-(?:[a-z0-9]{9}|git-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)"
    r"-ashartanveercs-dels-projects\.vercel\.app$"
)
DNS_LABEL_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE | re.ASCII,
)


def _is_missing(value: str | SecretStr | None) -> bool:
    if value is None:
        return True
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return not value.strip()


def _is_canonical_https_origin(value: str) -> bool:
    if "*" in value:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname or ""
    labels = hostname.split(".")
    return bool(
        parsed.scheme == "https"
        and hostname
        and parsed.netloc == hostname
        and port is None
        and len(hostname) <= 253
        and len(labels) >= 2
        and all(DNS_LABEL_PATTERN.fullmatch(label) for label in labels)
        and not parsed.username
        and not parsed.password
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "preview", "production"] = "development"
    vercel_env: Literal["development", "preview", "production"] | None = None
    airtable_pat: SecretStr | None = None
    airtable_base_id: str | None = None
    groq_api_key: SecretStr | None = None
    cloudinary_url: SecretStr | None = None
    admin_username: str | None = None
    admin_password_hash: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    mutation_preview_secret: SecretStr | None = None
    upstash_redis_rest_url: str | None = None
    upstash_redis_rest_token: SecretStr | None = None
    redis_namespace: str | None = None
    redis_key_hmac_secret: SecretStr | None = None
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    jwt_issuer: str = "shajra"
    jwt_audience: str = "shajra-admin"
    cors_allowed_origins: str = "http://localhost:3000"
    public_writes_enabled: bool = False
    relationship_writes_enabled: bool = False
    normalized_reads_enabled: bool = False

    @model_validator(mode="after")
    def require_runtime_secrets(self) -> "Settings":
        if self.app_env in {"preview", "production"}:
            required = {
                "AIRTABLE_PAT": self.airtable_pat,
                "AIRTABLE_BASE_ID": self.airtable_base_id,
                "ADMIN_USERNAME": self.admin_username,
                "ADMIN_PASSWORD_HASH": self.admin_password_hash,
                "JWT_SECRET": self.jwt_secret,
                "MUTATION_PREVIEW_SECRET": self.mutation_preview_secret,
                "UPSTASH_REDIS_REST_URL": self.upstash_redis_rest_url,
                "UPSTASH_REDIS_REST_TOKEN": self.upstash_redis_rest_token,
                "REDIS_NAMESPACE": self.redis_namespace,
                "REDIS_KEY_HMAC_SECRET": self.redis_key_hmac_secret,
            }
            missing = [name for name, value in required.items() if _is_missing(value)]
            if missing:
                raise ValueError("Missing required settings: " + ", ".join(sorted(missing)))
            require_canonical_upstash_url(self.upstash_redis_rest_url or "")
            if not re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*", self.redis_namespace or ""
            ) or not 1 <= len(self.redis_namespace or "") <= 32:
                raise ValueError("Invalid REDIS_NAMESPACE")
        if self.runtime_environment in {"preview", "production"}:
            origins = self.allowed_origins
            if not origins or not all(_is_canonical_https_origin(origin) for origin in origins):
                raise ValueError("Invalid CORS_ALLOWED_ORIGINS")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_allowed_origins.split(",") if value.strip()]

    @property
    def allowed_origin_regex(self) -> str | None:
        if self.vercel_env != "preview":
            return None
        return VERCEL_PREVIEW_FRONTEND_ORIGIN_REGEX

    @property
    def runtime_environment(self) -> Literal["development", "test", "preview", "production"]:
        if self.vercel_env in {"preview", "production"}:
            return self.vercel_env
        return self.app_env

    @property
    def environment_mismatch(self) -> bool:
        return (
            self.vercel_env in {"preview", "production"}
            and self.app_env != self.vercel_env
        )

    @property
    def effective_public_writes_enabled(self) -> bool:
        return self.public_writes_enabled and not self.environment_mismatch

    @property
    def effective_relationship_writes_enabled(self) -> bool:
        return self.relationship_writes_enabled and not self.environment_mismatch

    @property
    def effective_normalized_reads_enabled(self) -> bool:
        return self.normalized_reads_enabled and not self.environment_mismatch


@lru_cache
def get_settings() -> Settings:
    return Settings()


_settings = get_settings()

# Temporary compatibility exports for current v1 consumers. Task 3 will migrate
# those consumers to use Settings directly.
AIRTABLE_PAT = _settings.airtable_pat.get_secret_value() if _settings.airtable_pat else None
AIRTABLE_BASE_ID = _settings.airtable_base_id
GROQ_API_KEY = _settings.groq_api_key.get_secret_value() if _settings.groq_api_key else None
ADMIN_USERNAME = _settings.admin_username
ADMIN_PASSWORD = (
    _settings.admin_password_hash.get_secret_value()
    if _settings.admin_password_hash
    else None
)
JWT_SECRET = _settings.jwt_secret.get_secret_value() if _settings.jwt_secret else None
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24

APPROVED_MEMBERS_TABLE = "ApprovedMembers"
PENDING_SUBMISSIONS_TABLE = "PendingSubmissions"
APPROVED_EMAILS_TABLE = "ApprovedEmails"
ALBUMS_TABLE = "Albums"
PHOTOS_TABLE = "Photos"
