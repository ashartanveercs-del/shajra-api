import hashlib
import json
from collections.abc import Mapping

from domain.dates import PartialDate
from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.models import (
    FamilyUnit,
    GraphSnapshot,
    ParentChildLink,
    Person,
    UnresolvedRelationship,
    _validate_mapping_keys,
)


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_checksum(snapshot: GraphSnapshot) -> str:
    if not isinstance(snapshot, GraphSnapshot):
        raise TypeError("semantic_checksum requires a GraphSnapshot")
    _validate_mapping_keys(
        snapshot.people,
        snapshot.family_units,
        snapshot.links,
        snapshot.unresolved,
    )

    return sha256_json(
        {
            "people": _people_value(snapshot.people),
            "family_units": _family_units_value(snapshot.family_units),
            "links": _links_value(snapshot.links),
            "unresolved": _unresolved_value(snapshot.unresolved),
        }
    )


def _partial_date_value(value: PartialDate | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"value": value.value, "precision": value.precision.value}


def _people_value(people: Mapping[PersonId, Person]) -> dict[str, object]:
    value: dict[str, object] = {}
    for person_id, person in sorted(people.items(), key=lambda item: str(item[0])):
        value[str(person_id)] = {
            "person_id": str(person.person_id),
            "full_name": person.full_name,
            "gender": person.gender.value,
            "birth": _partial_date_value(person.birth),
            "death": _partial_date_value(person.death),
            "is_alive": person.is_alive,
            "primary_family_unit_id": (
                str(person.primary_family_unit_id)
                if person.primary_family_unit_id is not None
                else None
            ),
            "archived": person.archived,
        }
    return value


def _family_units_value(
    family_units: Mapping[FamilyUnitId, FamilyUnit],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for family_unit_id, family_unit in sorted(
        family_units.items(), key=lambda item: str(item[0])
    ):
        value[str(family_unit_id)] = {
            "family_unit_id": str(family_unit.family_unit_id),
            "kind": family_unit.kind.value,
            "adult_ids": _family_adult_ids(family_unit),
            "status": family_unit.status.value,
            "start": _partial_date_value(family_unit.start),
            "end": _partial_date_value(family_unit.end),
            "distinct_union_confirmed": family_unit.distinct_union_confirmed,
        }
    return value


def _links_value(links: Mapping[LinkId, ParentChildLink]) -> dict[str, object]:
    value: dict[str, object] = {}
    for link_id, link in sorted(links.items(), key=lambda item: str(item[0])):
        value[str(link_id)] = {
            "link_id": str(link.link_id),
            "parent_id": str(link.parent_id),
            "child_id": str(link.child_id),
            "role": link.role.value,
            "relationship_type": link.relationship_type.value,
            "family_unit_id": (
                str(link.family_unit_id) if link.family_unit_id is not None else None
            ),
        }
    return value


def _unresolved_value(
    unresolved: Mapping[UnresolvedRelationshipId, UnresolvedRelationship],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for unresolved_id, annotation in sorted(
        unresolved.items(), key=lambda item: str(item[0])
    ):
        value[str(unresolved_id)] = {
            "unresolved_id": str(annotation.unresolved_id),
            "subject_person_id": str(annotation.subject_person_id),
            "kind": annotation.kind.value,
            "unresolved_name": annotation.unresolved_name,
        }
    return value


def _family_adult_ids(family_unit: FamilyUnit) -> list[str]:
    adult_ids = [str(family_unit.adult_a_id)]
    if family_unit.adult_b_id is not None:
        adult_ids.append(str(family_unit.adult_b_id))
    return sorted(adult_ids)
