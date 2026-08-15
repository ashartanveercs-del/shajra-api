import importlib

import pytest
from config import Settings
from fastapi import HTTPException


def test_ai_client_fails_closed_without_a_configured_key(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    monkeypatch.setattr(ai_service, "get_settings", lambda: Settings(app_env="test", _env_file=None))
    calls = 0

    def record_database_access():
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(ai_service.db, "get_all_members", record_database_access)

    with pytest.raises(HTTPException) as exc_info:
        ai_service.process_submission({"RawFullName": "Test Person"})

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "AI_NOT_CONFIGURED",
        "message": "AI processing is not configured.",
    }
    assert calls == 0


def test_ai_context_does_not_expose_external_exception_text(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    synthetic_exception = "synthetic-provider-request-detail"

    def unavailable_members():
        raise RuntimeError(synthetic_exception)

    monkeypatch.setattr(ai_service.db, "get_all_members", unavailable_members)

    context = ai_service.get_existing_members_context()

    assert context == "Existing member context is temporarily unavailable."
    assert synthetic_exception not in context


def test_ai_fallback_notes_do_not_expose_external_exception_text(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    synthetic_exception = "synthetic-provider-request-detail"

    class FailingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError(synthetic_exception)

    class FailingClient:
        chat = type("Chat", (), {"completions": FailingCompletions()})()

    monkeypatch.setattr(
        ai_service,
        "get_settings",
        lambda: Settings(app_env="test", groq_api_key="test-groq-key", _env_file=None),
    )
    monkeypatch.setattr(ai_service, "get_existing_members_context", lambda: "No members")
    monkeypatch.setattr(ai_service, "get_client", FailingClient)

    result = ai_service.process_submission({"RawFullName": "Test Person"})

    assert result["Notes"] == "AI processing failed. Using raw data."
    assert synthetic_exception not in result["Notes"]


def test_ai_client_constructor_failure_falls_back_before_database_access(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    synthetic_exception = "synthetic-client-constructor-detail"
    calls = 0

    def failing_client_constructor():
        raise RuntimeError(synthetic_exception)

    def record_database_access():
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(ai_service, "get_client", failing_client_constructor)
    monkeypatch.setattr(ai_service.db, "get_all_members", record_database_access)

    result = ai_service.process_submission({"RawFullName": "Test Person"})

    assert result["Notes"] == "AI processing failed. Using raw data."
    assert synthetic_exception not in result["Notes"]
    assert calls == 0
