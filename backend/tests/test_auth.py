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
