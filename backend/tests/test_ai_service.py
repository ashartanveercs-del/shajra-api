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


def test_match_member_id_resolves_unique_names():
    ai_service = importlib.import_module("ai_service")
    members = [
        {"id": "rec_salman", "FullName": "Salman Habib", "Gender": "Male"},
        {"id": "rec_aiemen", "FullName": "Aiemen Aslam", "Gender": "Female"},
        {"id": "rec_sobia", "FullName": "Sobia Alamgir", "Gender": "Female"},
        {"id": "rec_aftab", "FullName": "Aftab Alamgir", "Gender": "Male"},
        {"id": "rec_tanveer", "FullName": "Tanveer Kamal", "Gender": "Male"},
        {"id": "rec_khushaar", "FullName": "Khushaar Tanveer", "Gender": "Female"},
    ]

    # Correct unique matches (these were the exact LLM-hallucination failures).
    assert ai_service._match_member_id("Aiemen Aslam", members, gender="Female") == "rec_aiemen"
    assert ai_service._match_member_id("Tanveer Kamal Rasheed", members, gender="Male") == "rec_tanveer"
    assert ai_service._match_member_id("Aftab Alamgir", members, gender="Male") == "rec_aftab"
    assert ai_service._match_member_id("Sobia Alamgir", members, gender="Female") == "rec_sobia"
    assert ai_service._match_member_id("Salman Habib", members, gender="Male") == "rec_salman"

    # Name-only / unknown people never match a record.
    assert ai_service._match_member_id("Hashir Tihami", members, gender="Male") == ""
    assert ai_service._match_member_id("", members, gender="Male") == ""
    assert ai_service._match_member_id("Someone Else", members, gender="Male") == ""

    # Gender mismatch must not match.
    assert ai_service._match_member_id("Salman Habib", members, gender="Female") == ""


def test_match_member_id_never_guesses_ambiguous_names():
    ai_service = importlib.import_module("ai_service")
    # Two people share a surname key; a bare-name reference must NOT pick either.
    members = [
        {"id": "rec_a", "FullName": "Muhammad Ali", "Gender": "Male"},
        {"id": "rec_b", "FullName": "Ali Raza", "Gender": "Male"},
    ]
    assert ai_service._match_member_id("Ali", members, gender="Male") == ""
