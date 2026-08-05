"""Pure mappings between Airtable rows, legacy rows, and graph-domain values."""

from dataclasses import dataclass
from typing import Mapping, Sequence

from domain.dates import PartialDate
from domain.ids import (
    FamilyUnitId,
    LinkId,
    OperationId,
    PersonId,
    UnresolvedRelationshipId,
    migrated_family_unit_id,
    migrated_link_id,
    migrated_person_id,
    migrated_unresolved_relationship_id,
)
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    Gender,
    GraphSnapshot,
    GraphState,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
    UnionStatus,
)
from repositories.protocols import WriteContext

Row = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RepositoryTombstone:
    entity_id: str
    revision: int
    operation_id: OperationId
    fencing_token: int


@dataclass(frozen=True, slots=True)
class LegacyPerson:
    person: Person
    source_record_id: str


@dataclass(frozen=True, slots=True)
class LegacySnapshot:
    people: tuple[LegacyPerson, ...]
    family_units: tuple[FamilyUnit, ...]
    parent_child_links: tuple[ParentChildLink, ...]
    unresolved: tuple[UnresolvedRelationship, ...]

    def public_values(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "PersonId": str(legacy_person.person.person_id),
                "FullName": legacy_person.person.full_name,
                "Gender": legacy_person.person.gender.value,
                "Birth": _date_value(legacy_person.person.birth),
                "Death": _date_value(legacy_person.person.death),
                "IsAlive": _is_alive_value(legacy_person.person.is_alive),
                "Archived": legacy_person.person.archived,
            }
            for legacy_person in self.people
        )


def _date_value(value: PartialDate | None) -> str:
    return "" if value is None else value.value


def _is_alive_value(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _parse_date(value: object) -> PartialDate | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Date values must be strings")
    return PartialDate.parse(value)


def _parse_is_alive(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise ValueError("IsAlive must be true, false, or blank")


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    raise ValueError("Checkbox values must be booleans")


def _required_text(row: Row, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_text(row: Row, field: str) -> str | None:
    value = row.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or blank")
    return value


def _integer(row: Row, field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _authorization_row(context: WriteContext, tombstone: bool) -> dict[str, object]:
    return {
        "Revision": context.revision,
        "OperationId": str(context.operation_id),
        "FencingToken": context.fencing_token,
        "IsTombstone": tombstone,
    }


def _tombstone(row: Row, id_field: str) -> RepositoryTombstone:
    return RepositoryTombstone(
        _required_text(row, id_field),
        _integer(row, "Revision"),
        OperationId(_required_text(row, "OperationId")),
        _integer(row, "FencingToken"),
    )


def person_to_row(
    person: Person, context: WriteContext, *, tombstone: bool = False
) -> dict[str, object]:
    row = {"PersonId": str(person.person_id), **_authorization_row(context, tombstone)}
    if tombstone:
        return row
    return {
        "PersonId": str(person.person_id),
        "FullName": person.full_name,
        "Gender": person.gender.value,
        "Birth": _date_value(person.birth),
        "Death": _date_value(person.death),
        "IsAlive": _is_alive_value(person.is_alive),
        "PrimaryFamilyUnitId": (
            "" if person.primary_family_unit_id is None else str(person.primary_family_unit_id)
        ),
        "Archived": person.archived,
        "VersionRevision": person.version_revision,
        **_authorization_row(context, False),
    }


def person_from_row(row: Row) -> Person | RepositoryTombstone:
    if _bool(row.get("IsTombstone")):
        return _tombstone(row, "PersonId")
    primary_family_unit_id = _optional_text(row, "PrimaryFamilyUnitId")
    return Person(
        PersonId(_required_text(row, "PersonId")),
        _required_text(row, "FullName"),
        Gender(_required_text(row, "Gender")),
        _parse_date(row.get("Birth")),
        _parse_date(row.get("Death")),
        _parse_is_alive(row.get("IsAlive")),
        FamilyUnitId(primary_family_unit_id) if primary_family_unit_id else None,
        _bool(row.get("Archived")),
        _integer(row, "VersionRevision"),
    )


def family_unit_to_row(
    family_unit: FamilyUnit, context: WriteContext, *, tombstone: bool = False
) -> dict[str, object]:
    row = {"FamilyUnitId": str(family_unit.family_unit_id), **_authorization_row(context, tombstone)}
    if tombstone:
        return row
    return {
        "FamilyUnitId": str(family_unit.family_unit_id),
        "Kind": family_unit.kind.value,
        "AdultAId": str(family_unit.adult_a_id),
        "AdultBId": "" if family_unit.adult_b_id is None else str(family_unit.adult_b_id),
        "Status": family_unit.status.value,
        "Start": _date_value(family_unit.start),
        "End": _date_value(family_unit.end),
        "DistinctUnionConfirmed": family_unit.distinct_union_confirmed,
        "CreatedRevision": family_unit.created_revision,
        **_authorization_row(context, False),
    }


def family_unit_from_row(row: Row) -> FamilyUnit | RepositoryTombstone:
    if _bool(row.get("IsTombstone")):
        return _tombstone(row, "FamilyUnitId")
    adult_b_id = _optional_text(row, "AdultBId")
    return FamilyUnit(
        FamilyUnitId(_required_text(row, "FamilyUnitId")),
        FamilyUnitKind(_required_text(row, "Kind")),
        PersonId(_required_text(row, "AdultAId")),
        PersonId(adult_b_id) if adult_b_id else None,
        UnionStatus(_required_text(row, "Status")),
        _parse_date(row.get("Start")),
        _parse_date(row.get("End")),
        _bool(row.get("DistinctUnionConfirmed")),
        _integer(row, "CreatedRevision"),
    )


def parent_child_link_to_row(
    link: ParentChildLink, context: WriteContext, *, tombstone: bool = False
) -> dict[str, object]:
    row = {"LinkId": str(link.link_id), **_authorization_row(context, tombstone)}
    if tombstone:
        return row
    return {
        "LinkId": str(link.link_id),
        "ParentId": str(link.parent_id),
        "ChildId": str(link.child_id),
        "Role": link.role.value,
        "RelationshipType": link.relationship_type.value,
        "FamilyUnitId": "" if link.family_unit_id is None else str(link.family_unit_id),
        "CreatedRevision": link.created_revision,
        **_authorization_row(context, False),
    }


def parent_child_link_from_row(row: Row) -> ParentChildLink | RepositoryTombstone:
    if _bool(row.get("IsTombstone")):
        return _tombstone(row, "LinkId")
    family_unit_id = _optional_text(row, "FamilyUnitId")
    return ParentChildLink(
        LinkId(_required_text(row, "LinkId")),
        PersonId(_required_text(row, "ParentId")),
        PersonId(_required_text(row, "ChildId")),
        ParentRole(_required_text(row, "Role")),
        RelationshipType(_required_text(row, "RelationshipType")),
        FamilyUnitId(family_unit_id) if family_unit_id else None,
        _integer(row, "CreatedRevision"),
    )


def unresolved_to_row(
    annotation: UnresolvedRelationship, context: WriteContext, *, tombstone: bool = False
) -> dict[str, object]:
    row = {"UnresolvedId": str(annotation.unresolved_id), **_authorization_row(context, tombstone)}
    if tombstone:
        return row
    return {
        "UnresolvedId": str(annotation.unresolved_id),
        "SubjectPersonId": str(annotation.subject_person_id),
        "Kind": annotation.kind.value,
        "UnresolvedName": annotation.unresolved_name,
        "CreatedRevision": annotation.created_revision,
        **_authorization_row(context, False),
    }


def unresolved_from_row(row: Row) -> UnresolvedRelationship | RepositoryTombstone:
    if _bool(row.get("IsTombstone")):
        return _tombstone(row, "UnresolvedId")
    return UnresolvedRelationship(
        UnresolvedRelationshipId(_required_text(row, "UnresolvedId")),
        PersonId(_required_text(row, "SubjectPersonId")),
        UnresolvedRelationshipKind(_required_text(row, "Kind")),
        _required_text(row, "UnresolvedName"),
        _integer(row, "CreatedRevision"),
    )


def snapshot_from_rows(
    state: GraphState,
    *,
    person_rows: Sequence[Row] = (),
    family_unit_rows: Sequence[Row] = (),
    parent_child_link_rows: Sequence[Row] = (),
    unresolved_rows: Sequence[Row] = (),
) -> GraphSnapshot:
    people = _domain_values(person_rows, person_from_row, Person)
    family_units = _domain_values(family_unit_rows, family_unit_from_row, FamilyUnit)
    links = _domain_values(parent_child_link_rows, parent_child_link_from_row, ParentChildLink)
    unresolved = _domain_values(unresolved_rows, unresolved_from_row, UnresolvedRelationship)
    return GraphSnapshot(
        state,
        {person.person_id: person for person in people},
        {family_unit.family_unit_id: family_unit for family_unit in family_units},
        {link.link_id: link for link in links},
        {annotation.unresolved_id: annotation for annotation in unresolved},
    )


def _domain_values(rows: Sequence[Row], mapper, expected_type):
    values = []
    for row in rows:
        value = mapper(row)
        if isinstance(value, RepositoryTombstone):
            continue
        if not isinstance(value, expected_type):
            raise TypeError("Unexpected repository row value")
        values.append(value)
    return values


def map_linked_record_id(
    value: object, record_id_map: Mapping[str, PersonId], field: str
) -> PersonId | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        record_ids = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        record_ids = value
    else:
        raise ValueError(f"{field} must be a linked-record list")
    if not record_ids:
        return None
    if len(record_ids) != 1:
        raise ValueError(f"{field} must contain at most one record")
    return record_id_map.get(record_ids[0])


def legacy_snapshot_from_records(records: Sequence[Mapping[str, object]]) -> LegacySnapshot:
    legacy_people = tuple(_legacy_person(record) for record in records)
    record_id_map = {
        legacy_person.source_record_id: legacy_person.person.person_id
        for legacy_person in legacy_people
    }
    family_units: dict[FamilyUnitId, FamilyUnit] = {}
    links: list[ParentChildLink] = []
    unresolved: list[UnresolvedRelationship] = []

    for record, legacy_person in zip(records, legacy_people, strict=True):
        fields = _record_fields(record)
        source_record_id = legacy_person.source_record_id
        for record_field, name_field, role, kind in (
            ("FatherRecordId", "FatherName", ParentRole.FATHER, UnresolvedRelationshipKind.FATHER),
            ("MotherRecordId", "MotherName", ParentRole.MOTHER, UnresolvedRelationshipKind.MOTHER),
        ):
            parent_id = map_linked_record_id(fields.get(record_field), record_id_map, record_field)
            if parent_id is not None:
                links.append(
                    ParentChildLink(
                        migrated_link_id(f"ApprovedMembers:{record_field}", source_record_id),
                        parent_id,
                        legacy_person.person.person_id,
                        role,
                        RelationshipType.UNKNOWN,
                        None,
                    )
                )
            else:
                annotation = _legacy_unresolved(
                    fields, source_record_id, legacy_person.person.person_id, name_field, kind
                )
                if annotation is not None:
                    unresolved.append(annotation)

        spouse_id = map_linked_record_id(fields.get("SpouseRecordId"), record_id_map, "SpouseRecordId")
        if spouse_id is not None:
            spouse_source_record_id = _source_record_id_for_person(record_id_map, spouse_id)
            family_unit = _legacy_family_unit(
                source_record_id, spouse_source_record_id, legacy_person.person.person_id, spouse_id
            )
            family_units[family_unit.family_unit_id] = family_unit
        else:
            annotation = _legacy_unresolved(
                fields,
                source_record_id,
                legacy_person.person.person_id,
                "SpouseName",
                UnresolvedRelationshipKind.PARTNER,
            )
            if annotation is not None:
                unresolved.append(annotation)

    return LegacySnapshot(
        legacy_people,
        tuple(family_units.values()),
        tuple(links),
        tuple(unresolved),
    )


def _record_fields(record: Mapping[str, object]) -> Mapping[str, object]:
    fields = record.get("fields", {})
    if not isinstance(fields, Mapping):
        raise ValueError("Legacy Airtable record fields must be a mapping")
    return fields


def _legacy_person(record: Mapping[str, object]) -> LegacyPerson:
    source_record_id = record.get("id")
    if not isinstance(source_record_id, str) or not source_record_id:
        raise ValueError("Legacy Airtable records require an id")
    fields = _record_fields(record)
    return LegacyPerson(
        Person(
            migrated_person_id("ApprovedMembers", source_record_id),
            _legacy_name(fields.get("FullName")),
            _legacy_gender(fields.get("Gender")),
            _parse_date(fields.get("DateOfBirth")),
            _parse_date(fields.get("DateOfDeath")),
            _legacy_is_alive(fields.get("IsAlive")),
        ),
        source_record_id,
    )


def _legacy_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "Unknown"
    return value.strip()


def _legacy_gender(value: object) -> Gender:
    if not isinstance(value, str):
        return Gender.UNKNOWN
    return {"male": Gender.MALE, "female": Gender.FEMALE}.get(value.lower(), Gender.UNKNOWN)


def _legacy_is_alive(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    return _parse_is_alive(str(value).lower())


def _legacy_unresolved(
    fields: Mapping[str, object],
    source_record_id: str,
    subject_person_id: PersonId,
    name_field: str,
    kind: UnresolvedRelationshipKind,
) -> UnresolvedRelationship | None:
    name = fields.get(name_field)
    if not isinstance(name, str) or not name.strip():
        return None
    return UnresolvedRelationship(
        migrated_unresolved_relationship_id(
            "ApprovedMembers", source_record_id, f"{name_field}#0"
        ),
        subject_person_id,
        kind,
        name,
    )


def _source_record_id_for_person(
    record_id_map: Mapping[str, PersonId], person_id: PersonId
) -> str:
    return next(record_id for record_id, mapped_person_id in record_id_map.items() if mapped_person_id == person_id)


def _legacy_family_unit(
    first_source_id: str,
    second_source_id: str,
    first_person_id: PersonId,
    second_person_id: PersonId,
) -> FamilyUnit:
    source_pair = "|".join(sorted((first_source_id, second_source_id)))
    adults = sorted((first_person_id, second_person_id), key=str)
    return FamilyUnit(
        migrated_family_unit_id("ApprovedMembers:SpouseRecordId", source_pair),
        FamilyUnitKind.UNION,
        adults[0],
        adults[1],
    )
