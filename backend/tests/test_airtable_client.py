import airtable_client
import pytest
from requests import Response
from requests.exceptions import HTTPError
from types import SimpleNamespace


def test_create_member_uses_explicit_approved_members_field_allowlist(monkeypatch):
    captured = {}

    def capture_create(_table, fields):
        captured.update(fields)
        return {"id": "created", **fields}

    monkeypatch.setattr(airtable_client, "_create_legacy", capture_create)

    result = airtable_client.create_member(
        {
            "FullName": "Synthetic Person",
            "DateOfDeath": "2001",
            "Branch": "Branch A",
            "Autobiography": "Synthetic autobiography",
            "HeritageStory": "Synthetic heritage story",
            "Email": "synthetic@example.invalid",
            "PhoneNumber": "+000000000",
            "ProfileImageUrl": "https://example.invalid/profile.png",
            "CardStyle": "populated-but-not-an-airtable-field",
            "UnexpectedField": "must-not-be-written",
            "CurrentCity": "",
        }
    )

    assert captured == {
        "FullName": "Synthetic Person",
        "DateOfDeath": "2001",
        "Branch": "Branch A",
        "Autobiography": "Synthetic autobiography",
        "HeritageStory": "Synthetic heritage story",
        "Email": "synthetic@example.invalid",
        "PhoneNumber": "+000000000",
        "ProfileImageUrl": "https://example.invalid/profile.png",
    }
    assert result["id"] == "created"


def test_update_member_uses_allowlist_and_preserves_empty_clears(monkeypatch):
    captured = {}

    def capture_update(_table, record_id, fields):
        captured["record_id"] = record_id
        captured["fields"] = fields
        return {"id": record_id, **fields}

    monkeypatch.setattr(airtable_client, "_update_legacy", capture_update)

    result = airtable_client.update_member(
        "member-1",
        {
            "FullName": "Synthetic Person",
            "SpouseRecordId": "",
            "SpouseName": "",
            "CurrentCity": None,
            "CardStyle": "not-an-airtable-field",
            "id": "must-not-be-written",
            "undo": {"before": "must-not-be-written"},
            "UnexpectedField": "must-not-be-written",
        },
    )

    assert captured == {
        "record_id": "member-1",
        "fields": {
            "FullName": "Synthetic Person",
            "SpouseRecordId": "",
            "SpouseName": "",
        },
    }
    assert result["SpouseRecordId"] == ""
    assert result["SpouseName"] == ""


def test_member_read_propagates_unverified_datastore_failures(monkeypatch):
    def fail_read(_record_id):
        raise RuntimeError("synthetic uncertain read")

    monkeypatch.setattr(airtable_client.members_table, "get", fail_read)

    with pytest.raises(RuntimeError, match="synthetic uncertain read"):
        airtable_client.get_member_by_id("member-1")


def test_member_read_returns_none_only_for_verified_not_found(monkeypatch):
    response = Response()
    response.status_code = 404

    def missing(_record_id):
        raise HTTPError(response=response)

    monkeypatch.setattr(airtable_client.members_table, "get", missing)

    assert airtable_client.get_member_by_id("missing-member") is None

def test_legacy_mutations_default_to_disabled(monkeypatch):
    monkeypatch.delenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", raising=False)
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=False,
            relationship_writes_enabled=False,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is False


def test_explicit_true_cannot_enable_legacy_mutations_without_write_flag(monkeypatch):
    monkeypatch.setenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", "true")
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=False,
            relationship_writes_enabled=False,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is False


def test_explicit_true_preserves_enabled_advertised_write_flag(monkeypatch):
    monkeypatch.setenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", "true")
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=False,
            relationship_writes_enabled=True,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is True


def test_advertised_write_flags_enable_legacy_datastore_mutations(monkeypatch):
    monkeypatch.delenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", raising=False)
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=False,
            relationship_writes_enabled=True,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is True


def test_explicit_legacy_kill_switch_overrides_advertised_write_flags(monkeypatch):
    monkeypatch.setenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", "false")
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=True,
            relationship_writes_enabled=True,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is False


def test_environment_mismatch_disables_legacy_mutations(monkeypatch):
    monkeypatch.delenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", raising=False)
    monkeypatch.setattr(
        airtable_client,
        "get_settings",
        lambda: SimpleNamespace(
            public_writes_enabled=True,
            relationship_writes_enabled=True,
            environment_mismatch=True,
        ),
    )

    assert airtable_client._legacy_mutations_enabled() is False
