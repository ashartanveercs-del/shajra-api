import importlib

import main
import pytest
from config import Settings
from fastapi import HTTPException
from fastapi.testclient import TestClient

client = TestClient(main.app)


class NoopLeaseManager:
    def __init__(self):
        self.lease = object()

    def acquire(self, scope, acquisition_id, ttl_ms=15_000):
        return self.lease

    def assert_owned(self, lease):
        assert lease is self.lease

    def release(self, lease, request_nonce):
        assert lease is self.lease
        return object()


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


@pytest.mark.parametrize(
    ("method", "path", "payload", "database_method"),
    [
        (
            "post",
            "/api/admin/approved-emails",
            {"Email": "family@example.com", "Name": "Family Member"},
            "add_approved_email",
        ),
        (
            "delete",
            "/api/admin/approved-emails/email-record",
            None,
            "remove_approved_email",
        ),
        ("delete", "/api/admin/comments/comment-record", None, "delete_comment"),
        ("delete", "/api/admin/stories/story-record", None, "delete_story"),
    ],
)
def test_public_write_gate_blocks_all_admin_content_mutations(
    monkeypatch, method, path, payload, database_method
):
    def downstream_operation(*_args, **_kwargs):
        raise AssertionError("disabled public write reached the database")

    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(main.db, database_method, downstream_operation)
    try:
        response = client.request(method, path, json=payload)
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PUBLIC_WRITES_DISABLED"


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


def test_member_create_only_reads_members_for_commit_recovery(monkeypatch):
    member_reads = 0

    def current_members():
        nonlocal member_reads
        member_reads += 1
        return []

    write_gates = importlib.import_module("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    main.app.dependency_overrides[main.get_relationship_lease_manager] = NoopLeaseManager
    monkeypatch.setattr(main.db, "create_member", lambda fields: {"id": "rec-new", **fields})
    monkeypatch.setattr(main.db, "get_all_members", current_members)
    try:
        response = client.post("/api/admin/members", json={"FullName": "Test Person"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == "rec-new"
    assert "_heal" not in response.json()
    assert member_reads == 1


def test_approval_revalidates_current_members_without_fuzzy_relinking(monkeypatch):
    member_reads = 0

    def current_members():
        nonlocal member_reads
        member_reads += 1
        return []

    pending = {
        "id": "pending-1",
        "Status": "Pending",
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
        "RawFatherName": "",
        "RawMotherName": "",
        "RawSpouseName": "",
        "RawGender": "Other",
    }
    write_gates = importlib.import_module("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    main.app.dependency_overrides[main.get_relationship_lease_manager] = NoopLeaseManager
    monkeypatch.setattr(main.db, "get_all_pending", lambda: [pending])
    monkeypatch.setattr(main.db, "create_member", lambda fields: {"id": "rec-new", **fields})
    monkeypatch.setattr(main.db, "update_pending", lambda *_args: True)
    monkeypatch.setattr(main.db, "get_all_members", current_members)
    try:
        response = client.post("/api/admin/approve/pending-1")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert "heal" not in response.json()
    assert member_reads == 1
