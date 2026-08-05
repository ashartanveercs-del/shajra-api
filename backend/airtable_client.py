"""Legacy v1 Airtable compatibility facade.

New repository code lives under :mod:`repositories.airtable`; this module keeps
the existing route imports working until the v1 API is retired.
"""

import os
from typing import Any

from pyairtable.formulas import BLANK, EQ, OR, Field

from config import (
    AIRTABLE_BASE_ID,
    AIRTABLE_PAT,
    APPROVED_EMAILS_TABLE,
    APPROVED_MEMBERS_TABLE,
    PENDING_SUBMISSIONS_TABLE,
)
from repositories.airtable.client import AirtableClient
from repositories.airtable.formulas import (
    case_insensitive_exact,
    case_insensitive_substring,
    exact_match,
)

_client = AirtableClient(AIRTABLE_PAT, AIRTABLE_BASE_ID)


class _LazyTable:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attribute: str) -> Any:
        return getattr(_client.table(self._name), attribute)


members_table = _LazyTable(APPROVED_MEMBERS_TABLE)
pending_table = _LazyTable(PENDING_SUBMISSIONS_TABLE)
comments_table = _LazyTable("Comments")
stories_table = _LazyTable("Stories")
albums_table = _LazyTable("PhotoAlbums")
approved_emails_table = _LazyTable(APPROVED_EMAILS_TABLE)


def _flatten(record: dict[str, object]) -> dict[str, object]:
    fields = dict(record.get("fields", {}))
    for field in ("FatherRecordId", "MotherRecordId", "SpouseRecordId"):
        value = fields.get(field)
        if isinstance(value, list):
            fields[field] = value[0] if value else ""
    result = {"id": record["id"], **fields}
    result.setdefault("FullName", "Unknown")
    result.setdefault("Gender", "Male")
    result.setdefault("IsAlive", True)
    return result


def _read_all(table: _LazyTable, formula: str | None = None) -> list[dict[str, object]]:
    records = table.all() if formula is None else table.all(formula=formula)
    return [_flatten(record) for record in records]


def _legacy_mutations_enabled() -> bool:
    return os.getenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _require_legacy_mutations_enabled() -> None:
    if not _legacy_mutations_enabled():
        raise RuntimeError("Legacy Airtable mutations are disabled")


def _create_legacy(table: _LazyTable, fields: dict[str, object]) -> dict[str, object]:
    _require_legacy_mutations_enabled()
    return _flatten(table.create(fields))


def _update_legacy(
    table: _LazyTable, record_id: str, fields: dict[str, object]
) -> dict[str, object]:
    _require_legacy_mutations_enabled()
    return _flatten(table.update(record_id, fields))


def _delete_legacy(table: _LazyTable, record_id: str) -> bool:
    _require_legacy_mutations_enabled()
    table.delete(record_id)
    return True


# Approved Members
def get_all_members() -> list[dict[str, object]]:
    return _read_all(members_table)


def get_member_by_id(record_id: str) -> dict[str, object] | None:
    try:
        return _flatten(members_table.get(record_id))
    except Exception:
        return None


def create_member(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(members_table, fields)


def update_member(record_id: str, fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _update_legacy(members_table, record_id, fields)


def delete_member(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(members_table, record_id)


def search_members(query: str) -> list[dict[str, object]]:
    return _read_all(members_table, case_insensitive_substring("FullName", query))


def get_members_by_filter(field: str, value: str) -> list[dict[str, object]]:
    return _read_all(members_table, exact_match(field, value))


# Pending Submissions
def get_all_pending() -> list[dict[str, object]]:
    return _read_all(pending_table)


def get_pending_by_status(status: str = "Pending") -> list[dict[str, object]]:
    return _read_all(pending_table, exact_match("Status", status))


def create_pending(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(pending_table, fields)


def update_pending(record_id: str, fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _update_legacy(pending_table, record_id, fields)


def delete_pending(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(pending_table, record_id)


# Comments
def get_comments_for_member(member_record_id: str) -> list[dict[str, object]]:
    return _read_all(comments_table, exact_match("MemberRecordId", member_record_id))


def get_all_comments() -> list[dict[str, object]]:
    return _read_all(comments_table)


def create_comment(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(comments_table, fields)


def delete_comment(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(comments_table, record_id)


# Stories
def get_all_stories() -> list[dict[str, object]]:
    return _read_all(stories_table)


def get_family_stories() -> list[dict[str, object]]:
    formula = str(OR(EQ(Field("MemberRecordId"), ""), EQ(Field("MemberRecordId"), BLANK())))
    return _read_all(stories_table, formula)


def get_stories_for_member(member_record_id: str) -> list[dict[str, object]]:
    return _read_all(stories_table, exact_match("MemberRecordId", member_record_id))


def create_story(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(stories_table, fields)


def update_story(record_id: str, fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _update_legacy(stories_table, record_id, fields)


def delete_story(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(stories_table, record_id)


# Photo Albums
def get_all_albums() -> list[dict[str, object]]:
    return _read_all(albums_table)


def get_albums_for_member(member_record_id: str) -> list[dict[str, object]]:
    return _read_all(albums_table, exact_match("MemberRecordId", member_record_id))


def create_album(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(albums_table, fields)


def update_album(record_id: str, fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _update_legacy(albums_table, record_id, fields)


def delete_album(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(albums_table, record_id)


# Approved Emails
def get_approved_emails() -> list[dict[str, object]]:
    return _read_all(approved_emails_table)


def is_email_approved(email: str) -> bool:
    return bool(_read_all(approved_emails_table, case_insensitive_exact("Email", email)))


def add_approved_email(fields: dict[str, object]) -> dict[str, object]:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _create_legacy(approved_emails_table, fields)


def remove_approved_email(record_id: str) -> bool:
    """Legacy, feature-gated mutation export for existing v1 routes."""
    return _delete_legacy(approved_emails_table, record_id)
