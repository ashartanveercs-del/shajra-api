import importlib

import pytest
from config import Settings
from fastapi import HTTPException


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
