import importlib
from types import SimpleNamespace

import main
import pytest
from argon2 import PasswordHasher
from config import Settings
from coordination import CoordinationError, RateLimitPolicyId
from fastapi import HTTPException
from fastapi.testclient import TestClient


client = TestClient(main.app)


def test_authentication_fails_closed_when_safe_configuration_is_missing(monkeypatch):
    auth = importlib.import_module("auth")
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(app_env="test", jwt_secret=None, _env_file=None),
    )

    assert auth.verify_admin("admin", "password") is False
    with pytest.raises(HTTPException) as exc_info:
        auth.create_access_token({"sub": "admin"})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "AUTH_NOT_CONFIGURED"


@pytest.mark.parametrize(
    ("configured_username", "configured_password"),
    [
        (None, None),
        ("", ""),
        ("   ", "   "),
        (None, "configured-password"),
        ("", "configured-password"),
        ("   ", "configured-password"),
        ("configured-admin", None),
        ("configured-admin", ""),
        ("configured-admin", "   "),
    ],
)
def test_blank_or_missing_admin_credentials_fail_closed(
    monkeypatch, configured_username, configured_password
):
    auth = importlib.import_module("auth")
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            app_env="test",
            admin_username=configured_username,
            admin_password_hash=configured_password,
            _env_file=None,
        ),
    )

    assert auth.verify_admin("", "") is False


def test_admin_password_is_verified_as_argon2_hash(monkeypatch):
    auth = importlib.import_module("auth")
    configured_hash = PasswordHasher().hash("synthetic-correct-password")
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            app_env="test",
            admin_username="configured-admin",
            admin_password_hash=configured_hash,
            _env_file=None,
        ),
    )

    assert auth.verify_admin("configured-admin", "synthetic-correct-password") is True
    assert auth.verify_admin("configured-admin", "synthetic-wrong-password") is False
    assert auth.verify_admin("wrong-admin", "synthetic-correct-password") is False


def test_malformed_admin_password_hash_fails_closed(monkeypatch):
    auth = importlib.import_module("auth")
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            app_env="test",
            admin_username="configured-admin",
            admin_password_hash="not-an-argon2-hash",
            _env_file=None,
        ),
    )

    assert auth.verify_admin("configured-admin", "synthetic-password") is False


def test_admin_login_is_rate_limited_before_password_verification(monkeypatch):
    observed = {}

    class DenyingRateLimiter:
        def consume(self, policy, subject, request_nonce):
            observed.update(policy=policy, subject=subject, nonce=request_nonce)
            return SimpleNamespace(allowed=False, retry_after_ms=61_000)

    main.app.dependency_overrides[main.get_login_rate_limiter] = DenyingRateLimiter
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(
        main,
        "verify_admin",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("rate-limited login reached Argon2")
        ),
    )
    try:
        response = client.post(
            "/api/admin/login",
            headers={"x-forwarded-for": "203.0.113.9"},
            json={"username": "admin", "password": "synthetic"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "61"
    assert response.json()["detail"]["code"] == "RATE_LIMITED"
    assert observed["policy"] is RateLimitPolicyId.LOGIN
    assert observed["subject"].normalized_ip == "203.0.113.9"
    assert observed["nonce"]


def test_admin_login_fails_closed_when_rate_limiter_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        main.runtime_coordination,
        "build_rate_limiter",
        lambda _settings: (_ for _ in ()).throw(
            CoordinationError("COORDINATION_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        main,
        "verify_admin",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("unprotected login reached Argon2")
        ),
    )

    response = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "synthetic"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "COORDINATION_UNAVAILABLE",
        "message": "Login protection is temporarily unavailable.",
    }


def test_admin_login_allowed_path_consumes_limit_before_returning_token(monkeypatch):
    observed = {}

    class AllowingRateLimiter:
        def consume(self, policy, subject, request_nonce):
            observed.update(policy=policy, subject=subject, nonce=request_nonce)
            return SimpleNamespace(allowed=True, retry_after_ms=0)

    main.app.dependency_overrides[main.get_login_rate_limiter] = AllowingRateLimiter
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(main, "verify_admin", lambda username, password: True)
    monkeypatch.setattr(main, "create_access_token", lambda payload: "synthetic-token")
    try:
        response = client.post(
            "/api/admin/login",
            headers={"x-forwarded-for": "203.0.113.10"},
            json={"username": "admin", "password": "synthetic"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "synthetic-token",
        "token_type": "bearer",
    }
    assert observed["policy"] is RateLimitPolicyId.LOGIN
    assert observed["nonce"]
