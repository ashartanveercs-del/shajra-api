"""Shajra configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _is_missing(value: str | SecretStr | None) -> bool:
    if value is None:
        return True
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return not value.strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "preview", "production"] = "development"
    airtable_pat: SecretStr | None = None
    airtable_base_id: str | None = None
    groq_api_key: SecretStr | None = None
    cloudinary_url: SecretStr | None = None
    admin_username: str | None = None
    admin_password_hash: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    mutation_preview_secret: SecretStr | None = None
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
            }
            missing = [name for name, value in required.items() if _is_missing(value)]
            if missing:
                raise ValueError("Missing required settings: " + ", ".join(sorted(missing)))
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_allowed_origins.split(",") if value.strip()]


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
