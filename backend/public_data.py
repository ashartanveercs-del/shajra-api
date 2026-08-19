"""Shared normalization and public-response privacy helpers."""

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


_PRIVATE_CONTACT_KEYS = frozenset(
    {
        "contactemail",
        "contactnumber",
        "email",
        "emailaddress",
        "mobile",
        "mobilenumber",
        "phone",
        "phonenumber",
        "telephone",
        "telephonenumber",
        "whatsapp",
        "whatsappnumber",
    }
)


def normalize_name(value: object) -> str:
    """Normalize a human name without changing token boundaries."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value))
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks.casefold().strip())


def unique_member_by_name(
    name: object,
    members: list[dict[str, Any]],
    *,
    gender: str | None = None,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    """Return one exact normalized match, never a partial or ambiguous guess."""
    normalized = normalize_name(name)
    if not normalized:
        return None
    expected_gender = (gender or "").casefold()
    candidates = [
        member
        for member in members
        if normalize_name(member.get("FullName")) == normalized
        and not (exclude_id and str(member.get("id")) == exclude_id)
        and not (
            expected_gender
            and member.get("Gender")
            and str(member["Gender"]).casefold() != expected_gender
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def exact_relationship_ids(
    members: list[dict[str, Any]],
    *,
    father_name: object,
    mother_name: object,
    spouse_name: object,
    subject_gender: object,
) -> dict[str, str]:
    """Resolve raw relationship names to exact, unique member IDs."""
    normalized_gender = str(subject_gender or "").casefold()
    spouse_gender = ""
    if normalized_gender == "male":
        spouse_gender = "Female"
    elif normalized_gender == "female":
        spouse_gender = "Male"

    father = unique_member_by_name(father_name, members, gender="Male")
    mother = unique_member_by_name(mother_name, members, gender="Female")
    spouse = unique_member_by_name(spouse_name, members, gender=spouse_gender)
    return {
        "FatherRecordId": str(father.get("id", "")) if father else "",
        "MotherRecordId": str(mother.get("id", "")) if mother else "",
        "SpouseRecordId": str(spouse.get("id", "")) if spouse else "",
    }


def redact_public(value: Any) -> Any:
    """Recursively copy a response while removing private contact fields."""
    if isinstance(value, Mapping):
        return {
            key: redact_public(item)
            for key, item in value.items()
            if not _is_private_contact_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [redact_public(item) for item in value]
    return value


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_private_contact_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return normalized in _PRIVATE_CONTACT_KEYS or any(
        token in normalized
        for token in ("email", "phone", "whatsapp", "mobile", "telephone", "contactnumber")
    )
