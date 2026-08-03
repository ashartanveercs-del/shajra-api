import importlib

import pytest
from config import Settings
from fastapi import HTTPException


def test_ai_client_fails_closed_without_a_configured_key(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    monkeypatch.setattr(ai_service, "get_settings", lambda: Settings(app_env="test", _env_file=None))
    monkeypatch.setattr(ai_service.db, "get_all_members", list)

    with pytest.raises(HTTPException) as exc_info:
        ai_service.process_submission({"RawFullName": "Test Person"})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "AI_NOT_CONFIGURED",
        "message": "AI processing is not configured.",
    }
