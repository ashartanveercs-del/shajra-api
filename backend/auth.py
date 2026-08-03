"""
Shajra System — Authentication Utilities (JWT)
"""
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from config import JWT_ALGORITHM, JWT_EXPIRATION_MINUTES, get_settings
from fastapi import HTTPException


def _get_jwt_secret() -> str:
    jwt_secret = get_settings().jwt_secret
    if jwt_secret is None or not jwt_secret.get_secret_value().strip():
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Admin authentication is not configured."},
        )
    return jwt_secret.get_secret_value()


def verify_admin(username: str, password: str) -> bool:
    """Check admin credentials against env variables."""
    settings = get_settings()
    configured_username = settings.admin_username
    configured_password = settings.admin_password_hash
    if (
        configured_username is None
        or not configured_username.strip()
        or configured_password is None
        or not configured_password.get_secret_value().strip()
    ):
        return False
    return hmac.compare_digest(username, configured_username) and hmac.compare_digest(
        password, configured_password.get_secret_value()
    )


def create_access_token(data: dict) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except (HTTPException, jwt.InvalidTokenError):
        return None
