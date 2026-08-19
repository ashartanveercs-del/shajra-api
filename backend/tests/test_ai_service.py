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
    assert ai_service._match_member_id("  T\u00c1NVEER   KAMAL  ", members, gender="Male") == "rec_tanveer"
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


def test_match_member_id_never_truncates_a_longer_name_to_a_shorter_person():
    ai_service = importlib.import_module("ai_service")
    members = [
        {"id": "rec_short", "FullName": "Tanveer Kamal", "Gender": "Male"},
    ]

    assert (
        ai_service._match_member_id(
            "Tanveer Kamal Rasheed", members, gender="Male"
        )
        == ""
    )


def test_match_member_id_rejects_normalized_duplicate_exact_names():
    ai_service = importlib.import_module("ai_service")
    members = [
        {"id": "rec_a", "FullName": "Jos\u00e9 Khan", "Gender": "Male"},
        {"id": "rec_b", "FullName": " JOSE   KHAN ", "Gender": "Male"},
    ]

    assert ai_service._match_member_id("Jose Khan", members, gender="Male") == ""


def test_ai_matching_context_contains_only_relationship_matching_fields(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    member = {
        "id": "record-secret",
        "FullName": "Existing Person",
        "FatherName": "Existing Father",
        "MotherName": "Existing Mother",
        "SpouseName": "Existing Spouse",
        "Gender": "Female",
        "Email": "private-email-marker",
        "PhoneNumber": "private-phone-marker",
        "Biography": "private-biography-marker",
        "CurrentCity": "private-city-marker",
        "BurialLocation": "private-burial-marker",
    }
    monkeypatch.setattr(ai_service.db, "get_all_members", lambda: [member])

    context = ai_service.get_existing_members_context()

    assert "Existing Person" in context
    assert "Existing Father" in context
    assert "Existing Mother" in context
    assert "Existing Spouse" in context
    assert "Female" in context
    for private_marker in (
        "record-secret",
        "private-email-marker",
        "private-phone-marker",
        "private-biography-marker",
        "private-city-marker",
        "private-burial-marker",
    ):
        assert private_marker not in context


def test_ai_submission_prompt_excludes_private_and_unneeded_fields():
    ai_service = importlib.import_module("ai_service")
    raw_data = {
        "RawFullName": "Submitted Person",
        "RawFatherName": "Submitted Father",
        "RawMotherName": "Submitted Mother",
        "RawSpouseName": "Submitted Spouse",
        "RawDateOfBirth": "2000",
        "RawDateOfDeath": "",
        "RawGender": "Female",
        "RawEmail": "submission-email-marker",
        "RawPhoneNumber": "submission-phone-marker",
        "RawProfileImage": "submission-image-marker",
        "RawBiography": "submission-biography-marker",
        "RawLocation": "submission-location-marker",
        "RawBurialLocation": "submission-burial-marker",
    }

    prompt = ai_service._build_submission_prompt(raw_data, "safe matching context")

    for matching_value in (
        "Submitted Person",
        "Submitted Father",
        "Submitted Mother",
        "Submitted Spouse",
        "Female",
        "safe matching context",
    ):
        assert matching_value in prompt
    for private_marker in (
        "submission-email-marker",
        "submission-phone-marker",
        "submission-image-marker",
        "submission-biography-marker",
        "submission-location-marker",
        "submission-burial-marker",
    ):
        assert private_marker not in prompt


def test_local_only_submission_fields_ignore_provider_values():
    ai_service = importlib.import_module("ai_service")
    provider_result = {
        "CleanCity": "provider-city-marker",
        "CleanCountry": "provider-country-marker",
        "CleanBurialLocation": "provider-burial-marker",
        "CleanEmail": "provider-email-marker",
        "CleanPhoneNumber": "provider-phone-marker",
        "CleanProfileImage": "provider-image-marker",
    }
    raw_data = {
        "RawLocation": "Synthetic City, Synthetic Country",
        "RawBurialLocation": "Synthetic Burial",
        "RawEmail": "local-email-marker",
        "RawPhoneNumber": "local-phone-marker",
        "RawProfileImage": "local-image-marker",
    }

    result = ai_service._apply_local_only_fields(provider_result, raw_data)

    assert result["CleanCity"] == "Synthetic City"
    assert result["CleanCountry"] == "Synthetic Country"
    assert result["CleanBurialLocation"] == "Synthetic Burial"
    assert result["CleanEmail"] == "local-email-marker"
    assert result["CleanPhoneNumber"] == "local-phone-marker"
    assert result["CleanProfileImage"] == "local-image-marker"
    assert "provider-" not in str(result)


def test_submission_relationship_ids_use_exact_raw_names_not_ai_rewrites(monkeypatch):
    ai_service = importlib.import_module("ai_service")
    captured = {}
    raw_data = {
        "RawFullName": "Submitted Person",
        "RawFatherName": "Tanveer Kamal Rasheed",
        "RawMotherName": "",
        "RawSpouseName": "",
        "RawGender": "Female",
    }
    provider_result = {
        "CleanFullName": "Submitted Person",
        "CleanFatherName": "Tanveer Kamal",
        "CleanMotherName": "",
        "CleanSpouseName": "",
        "CleanGender": "Female",
    }

    monkeypatch.setattr(ai_service, "process_submission", lambda _raw: provider_result)
    monkeypatch.setattr(
        ai_service.db,
        "get_all_members",
        lambda: [
            {"id": "wrong-short-id", "FullName": "Tanveer Kamal", "Gender": "Male"}
        ],
    )

    def capture_pending(fields):
        captured.update(fields)
        return {"id": "pending-1", **fields}

    monkeypatch.setattr(ai_service.db, "create_pending", capture_pending)

    result = ai_service.process_and_store_submission(raw_data)

    assert result["AIMatchedFatherId"] == ""
    assert captured["AIMatchedFatherId"] == ""
    assert captured["CleanFatherName"] == "Tanveer Kamal"
