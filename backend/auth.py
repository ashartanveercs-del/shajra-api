"""
Shajra System — Authentication Utilities (JWT)
"""
from datetime import datetime, timedelta, timezone
import jwt
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_MINUTES, ADMIN_USERNAME, ADMIN_PASSWORD


def verify_admin(username: str, password: str) -> bool:
    """Check admin credentials against env variables."""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def create_access_token(data: dict) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.InvalidTokenError:
        return None
