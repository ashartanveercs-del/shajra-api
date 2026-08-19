import main
from fastapi.testclient import TestClient


client = TestClient(main.app)


def _private_member(**overrides):
    member = {
        "id": "rec-person",
        "FullName": "Synthetic Person",
        "Email": "private-email-marker",
        "EmailAddress": "private-email-address-marker",
        "Phone": "private-phone-marker",
        "PhoneNumber": "private-phone-number-marker",
        "WhatsApp": "private-whatsapp-marker",
        "WhatsAppNumber": "private-whatsapp-number-marker",
        "MobileNumber": "private-mobile-marker",
        "RawEmail": "private-raw-email-marker",
        "EmergencyContactNumber": "private-emergency-contact-marker",
        "Gender": "Female",
        "IsAlive": True,
    }
    member.update(overrides)
    return member


def _assert_private_fields_absent(value):
    serialized = str(value)
    for marker in (
        "private-email-marker",
        "private-email-address-marker",
        "private-phone-marker",
        "private-phone-number-marker",
        "private-whatsapp-marker",
        "private-whatsapp-number-marker",
        "private-mobile-marker",
        "private-raw-email-marker",
        "private-emergency-contact-marker",
    ):
        assert marker not in serialized


def test_public_member_routes_redact_contact_fields_without_mutating_source(
    monkeypatch,
):
    source = _private_member()
    monkeypatch.setattr(main.db, "get_all_members", lambda: [source])
    monkeypatch.setattr(main.db, "get_member_by_id", lambda _record_id: source)

    collection = client.get("/api/members")
    detail = client.get("/api/members/rec-person")

    assert collection.status_code == 200
    assert detail.status_code == 200
    _assert_private_fields_absent(collection.json())
    _assert_private_fields_absent(detail.json())
    assert source["Email"] == "private-email-marker"
    assert source["PhoneNumber"] == "private-phone-number-marker"


def test_short_unfiltered_search_returns_empty_without_reading_all_members(monkeypatch):
    def unexpected_read():
        raise AssertionError("short public search enumerated the member table")

    monkeypatch.setattr(main.db, "get_all_members", unexpected_read)
    monkeypatch.setattr(main.db, "search_members", lambda _query: unexpected_read())

    assert client.get("/api/search").json() == []
    assert client.get("/api/search?q=x").json() == []
    assert client.get("/api/search?q=%20%20").json() == []


def test_filtered_search_is_allowed_without_a_name_and_is_redacted(monkeypatch):
    source = _private_member(CurrentCity="Synthetic City", Branch="Branch A")
    monkeypatch.setattr(main.db, "get_all_members", lambda: [source])

    response = client.get("/api/search?city=Synthetic%20City")

    assert response.status_code == 200
    assert [record["id"] for record in response.json()] == ["rec-person"]
    _assert_private_fields_absent(response.json())


def test_authenticated_admin_pending_route_retains_private_fields(monkeypatch):
    pending = {
        "id": "pending-record",
        "RawFullName": "Synthetic Person",
        "RawEmail": "admin-email-marker",
        "RawPhoneNumber": "admin-phone-marker",
    }
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    monkeypatch.setattr(main.db, "get_all_pending", lambda: [pending])
    try:
        response = client.get("/api/admin/pending")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["RawEmail"] == "admin-email-marker"
    assert response.json()[0]["RawPhoneNumber"] == "admin-phone-marker"


def test_public_comment_and_story_routes_redact_author_contact_fields(monkeypatch):
    private_record = {
        "id": "public-content",
        "AuthorName": "Synthetic Author",
        "AuthorEmail": "private-author-email-marker",
        "ContactPhone": "private-author-phone-marker",
    }
    monkeypatch.setattr(
        main.db, "get_comments_for_member", lambda _record_id: [private_record]
    )
    monkeypatch.setattr(main.db, "get_all_stories", lambda: [private_record])
    monkeypatch.setattr(main.db, "get_family_stories", lambda: [private_record])
    monkeypatch.setattr(
        main.db, "get_stories_for_member", lambda _record_id: [private_record]
    )

    responses = [
        client.get("/api/comments/rec-person"),
        client.get("/api/stories"),
        client.get("/api/stories/family"),
        client.get("/api/stories/member/rec-person"),
    ]

    for response in responses:
        assert response.status_code == 200
        serialized = str(response.json())
        assert "private-author-email-marker" not in serialized
        assert "private-author-phone-marker" not in serialized
        assert response.json()[0]["AuthorName"] == "Synthetic Author"
