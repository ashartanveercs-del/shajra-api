import importlib

import main
import pytest
from config import Settings
from fastapi import HTTPException
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_public_submission_is_disabled_before_ai_or_database_work(monkeypatch):
    def downstream_operation(_raw_data):
        raise AssertionError("disabled submission reached downstream processing")

    monkeypatch.setattr(main.ai_service, "process_and_store_submission", downstream_operation)

    response = client.post("/api/submit", json={"fullName": "Test Person"})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "PUBLIC_WRITES_DISABLED",
        "message": "Submissions are temporarily unavailable.",
    }


def test_relationship_dependency_fails_closed_with_test_settings(monkeypatch):
    write_gates = importlib.import_module("write_gates")
    monkeypatch.setattr(
        write_gates,
        "get_settings",
        lambda: Settings(app_env="test", relationship_writes_enabled=False, _env_file=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        write_gates.require_relationship_writes()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "RELATIONSHIP_WRITES_DISABLED",
        "message": "Relationship editing is temporarily unavailable.",
    }


def test_authenticated_relationship_write_is_disabled_before_database_work(monkeypatch):
    def downstream_operation(*_args, **_kwargs):
        raise AssertionError("disabled relationship write reached the database")

    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(main.db, "create_member", downstream_operation)
    try:
        response = client.post("/api/admin/members", json={"FullName": "Test Person"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RELATIONSHIP_WRITES_DISABLED"


def test_graph_heal_is_permanently_gone_for_an_authenticated_admin(monkeypatch):
    def unexpected_fuzzy_operation(*_args, **_kwargs):
        raise AssertionError("removed heal endpoint reached graph traversal")

    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(main.db, "get_all_members", unexpected_fuzzy_operation)
    try:
        response = client.post("/api/admin/heal")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "SELF_HEAL_REMOVED",
        "message": "Automatic graph healing has been removed.",
    }


def test_member_create_does_not_run_fuzzy_relinking(monkeypatch):
    def unexpected_fuzzy_operation(*_args, **_kwargs):
        raise AssertionError("member creation ran fuzzy relinking or self-healing")

    write_gates = importlib.import_module("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    monkeypatch.setattr(main.db, "create_member", lambda fields: {"id": "rec-new", **fields})
    monkeypatch.setattr(main.db, "get_all_members", unexpected_fuzzy_operation)
    try:
        response = client.post("/api/admin/members", json={"FullName": "Test Person"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == "rec-new"
    assert "_heal" not in response.json()


def test_approval_does_not_run_fuzzy_relinking(monkeypatch):
    def unexpected_fuzzy_operation(*_args, **_kwargs):
        raise AssertionError("approval ran fuzzy relinking or self-healing")

    pending = {
        "id": "pending-1",
        "CleanFullName": "Test Person",
        "CleanFatherName": "",
        "CleanMotherName": "",
        "CleanSpouseName": "",
        "CleanDOB": "",
        "CleanDOD": "",
        "CleanCity": "",
        "CleanCountry": "",
        "CleanBurialLocation": "",
        "CleanGender": "Other",
        "CleanEmail": "",
        "CleanPhoneNumber": "",
        "CleanProfileImage": "",
        "AIMatchedFatherId": "",
        "AIMatchedMotherId": "",
        "AIMatchedSpouseId": "",
        "RawBiography": "",
    }
    write_gates = importlib.import_module("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    monkeypatch.setattr(main.db, "get_all_pending", lambda: [pending])
    monkeypatch.setattr(main.db, "create_member", lambda fields: {"id": "rec-new", **fields})
    monkeypatch.setattr(main.db, "update_pending", lambda *_args: True)
    monkeypatch.setattr(main.db, "get_all_members", unexpected_fuzzy_operation)
    try:
        response = client.post("/api/admin/approve/pending-1")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert "heal" not in response.json()
