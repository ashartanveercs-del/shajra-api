"""Repository-owned persistence values with no coordination dependency."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from domain.dates import PartialDate
from domain.ids import (
    FamilyUnitId,
    LinkId,
    OperationId,
    PersonId,
    UnresolvedRelationshipId,
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


@dataclass(frozen=True, slots=True)
class WriteContext:
    operation_id: OperationId
    revision: int
    fencing_token: int
    actor_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class GraphWriteSet:
    person_upserts: tuple[Person, ...] = ()
    person_tombstones: tuple[PersonId, ...] = ()
    family_unit_upserts: tuple[FamilyUnit, ...] = ()
    family_unit_tombstones: tuple[FamilyUnitId, ...] = ()
    parent_child_link_upserts: tuple[ParentChildLink, ...] = ()
    parent_child_link_tombstones: tuple[LinkId, ...] = ()
    unresolved_upserts: tuple[UnresolvedRelationship, ...] = ()
    unresolved_tombstones: tuple[UnresolvedRelationshipId, ...] = ()


@dataclass(frozen=True, slots=True)
class StagedWriteReceipt:
    operation_id: OperationId
    revision: int
    fencing_token: int
    write_set_json: str
    write_set_sha256: str


@dataclass(frozen=True, slots=True)
class GraphCommit:
    operation_id: OperationId
    revision: int
    fencing_token: int
    permit_id: str
    semantic_checksum: str
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class CommitPermit:
    scope: str
    operation_id: OperationId
    revision: int
    fencing_token: int
    permit_id: str
    commit_sha256: str


class AuditOperationState(StrEnum):
    PENDING = "PENDING"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AuditOperation:
    operation_id: OperationId
    idempotency_key: str
    state: AuditOperationState
    actor_id: str
    request_id: str
    source_reference: str | None
    commands_json: str
    before_snapshot_json: str
    after_snapshot_json: str
    inverse_write_set_json: str
    commit_scope: str
    graph_commit_json: str
    commit_sha256: str


class GraphRepository(Protocol):
    def load_committed(self, revision: int | None = None) -> GraphSnapshot: ...

    def stage(
        self, write_set: GraphWriteSet, context: WriteContext
    ) -> StagedWriteReceipt: ...

    def verify_staged(self, receipt: StagedWriteReceipt) -> None: ...

    def append_commit(
        self, commit: GraphCommit, permit: CommitPermit
    ) -> GraphState: ...


class AuditRepository(Protocol):
    def find_by_idempotency_key(self, key: str) -> AuditOperation | None: ...

    def create_pending(self, operation: AuditOperation) -> None: ...

    def transition(
        self, operation_id: OperationId, state: AuditOperationState
    ) -> None: ...


def canonical_graph_commit_json(commit: GraphCommit) -> str:
    if commit.committed_at.tzinfo is None or commit.committed_at.utcoffset() is None:
        raise ValueError("committed_at must be timezone-aware")
    value = {
        "operation_id": str(commit.operation_id),
        "revision": commit.revision,
        "fencing_token": commit.fencing_token,
        "permit_id": commit.permit_id,
        "semantic_checksum": commit.semantic_checksum,
        "committed_at": commit.committed_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def graph_commit_sha256(commit: GraphCommit) -> str:
    return hashlib.sha256(
        canonical_graph_commit_json(commit).encode("ascii")
    ).hexdigest()


def canonical_graph_write_set_json(write_set: GraphWriteSet) -> str:
    value = {
        "person_upserts": _sorted_values(
            write_set.person_upserts, _person_value, "person_id"
        ),
        "person_tombstones": sorted(map(str, write_set.person_tombstones)),
        "family_unit_upserts": _sorted_values(
            write_set.family_unit_upserts, _family_unit_value, "family_unit_id"
        ),
        "family_unit_tombstones": sorted(map(str, write_set.family_unit_tombstones)),
        "parent_child_link_upserts": _sorted_values(
            write_set.parent_child_link_upserts, _link_value, "link_id"
        ),
        "parent_child_link_tombstones": sorted(
            map(str, write_set.parent_child_link_tombstones)
        ),
        "unresolved_upserts": _sorted_values(
            write_set.unresolved_upserts, _unresolved_value, "unresolved_id"
        ),
        "unresolved_tombstones": sorted(map(str, write_set.unresolved_tombstones)),
    }
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def graph_write_set_sha256(write_set: GraphWriteSet) -> str:
    return hashlib.sha256(
        canonical_graph_write_set_json(write_set).encode("ascii")
    ).hexdigest()


def graph_write_set_from_json(value: str) -> GraphWriteSet:
    parsed = json.loads(value)
    fields = (
        "person_upserts",
        "person_tombstones",
        "family_unit_upserts",
        "family_unit_tombstones",
        "parent_child_link_upserts",
        "parent_child_link_tombstones",
        "unresolved_upserts",
        "unresolved_tombstones",
    )
    root = _object(parsed, fields, "GraphWriteSet")

    people = tuple(_person_from_value(item) for item in _list(root, "person_upserts"))
    person_tombstones = tuple(
        PersonId(_logical_id(item, "per", "person_tombstones"))
        for item in _list(root, "person_tombstones")
    )
    family_units = tuple(
        _family_unit_from_value(item) for item in _list(root, "family_unit_upserts")
    )
    family_tombstones = tuple(
        FamilyUnitId(_logical_id(item, "fam", "family_unit_tombstones"))
        for item in _list(root, "family_unit_tombstones")
    )
    links = tuple(
        _link_from_value(item) for item in _list(root, "parent_child_link_upserts")
    )
    link_tombstones = tuple(
        LinkId(_logical_id(item, "lnk", "parent_child_link_tombstones"))
        for item in _list(root, "parent_child_link_tombstones")
    )
    unresolved = tuple(
        _unresolved_from_value(item) for item in _list(root, "unresolved_upserts")
    )
    unresolved_tombstones = tuple(
        UnresolvedRelationshipId(_logical_id(item, "unr", "unresolved_tombstones"))
        for item in _list(root, "unresolved_tombstones")
    )

    _require_unique_ids(
        "person",
        (str(item.person_id) for item in people),
        map(str, person_tombstones),
    )
    _require_unique_ids(
        "family unit",
        (str(item.family_unit_id) for item in family_units),
        map(str, family_tombstones),
    )
    _require_unique_ids(
        "parent-child link",
        (str(item.link_id) for item in links),
        map(str, link_tombstones),
    )
    _require_unique_ids(
        "unresolved relationship",
        (str(item.unresolved_id) for item in unresolved),
        map(str, unresolved_tombstones),
    )
    return GraphWriteSet(
        person_upserts=people,
        person_tombstones=person_tombstones,
        family_unit_upserts=family_units,
        family_unit_tombstones=family_tombstones,
        parent_child_link_upserts=links,
        parent_child_link_tombstones=link_tombstones,
        unresolved_upserts=unresolved,
        unresolved_tombstones=unresolved_tombstones,
    )


def _person_from_value(value: object) -> Person:
    fields = (
        "archived",
        "birth",
        "death",
        "full_name",
        "gender",
        "is_alive",
        "person_id",
        "primary_family_unit_id",
        "version_revision",
    )
    item = _object(value, fields, "person upsert")
    primary_family_id = item["primary_family_unit_id"]
    return Person(
        PersonId(_logical_id(item["person_id"], "per", "person_id")),
        _text(item["full_name"], "full_name"),
        Gender(_text(item["gender"], "gender")),
        _partial_date_from_value(item["birth"], "birth"),
        _partial_date_from_value(item["death"], "death"),
        _optional_bool(item["is_alive"], "is_alive"),
        (
            FamilyUnitId(
                _logical_id(
                    primary_family_id,
                    "fam",
                    "primary_family_unit_id",
                )
            )
            if primary_family_id is not None
            else None
        ),
        _bool(item["archived"], "archived"),
        _integer(item["version_revision"], "version_revision"),
    )


def _family_unit_from_value(value: object) -> FamilyUnit:
    fields = (
        "adult_a_id",
        "adult_b_id",
        "created_revision",
        "distinct_union_confirmed",
        "end",
        "family_unit_id",
        "kind",
        "start",
        "status",
    )
    item = _object(value, fields, "family unit upsert")
    adult_b_id = item["adult_b_id"]
    return FamilyUnit(
        FamilyUnitId(_logical_id(item["family_unit_id"], "fam", "family_unit_id")),
        FamilyUnitKind(_text(item["kind"], "kind")),
        PersonId(_logical_id(item["adult_a_id"], "per", "adult_a_id")),
        (
            PersonId(_logical_id(adult_b_id, "per", "adult_b_id"))
            if adult_b_id is not None
            else None
        ),
        UnionStatus(_text(item["status"], "status")),
        _partial_date_from_value(item["start"], "start"),
        _partial_date_from_value(item["end"], "end"),
        _bool(item["distinct_union_confirmed"], "distinct_union_confirmed"),
        _integer(item["created_revision"], "created_revision"),
    )


def _link_from_value(value: object) -> ParentChildLink:
    fields = (
        "child_id",
        "created_revision",
        "family_unit_id",
        "link_id",
        "parent_id",
        "relationship_type",
        "role",
    )
    item = _object(value, fields, "parent-child link upsert")
    family_unit_id = item["family_unit_id"]
    return ParentChildLink(
        LinkId(_logical_id(item["link_id"], "lnk", "link_id")),
        PersonId(_logical_id(item["parent_id"], "per", "parent_id")),
        PersonId(_logical_id(item["child_id"], "per", "child_id")),
        ParentRole(_text(item["role"], "role")),
        RelationshipType(_text(item["relationship_type"], "relationship_type")),
        (
            FamilyUnitId(_logical_id(family_unit_id, "fam", "family_unit_id"))
            if family_unit_id is not None
            else None
        ),
        _integer(item["created_revision"], "created_revision"),
    )


def _unresolved_from_value(value: object) -> UnresolvedRelationship:
    fields = (
        "created_revision",
        "kind",
        "subject_person_id",
        "unresolved_id",
        "unresolved_name",
    )
    item = _object(value, fields, "unresolved relationship upsert")
    return UnresolvedRelationship(
        UnresolvedRelationshipId(
            _logical_id(item["unresolved_id"], "unr", "unresolved_id")
        ),
        PersonId(_logical_id(item["subject_person_id"], "per", "subject_person_id")),
        UnresolvedRelationshipKind(_text(item["kind"], "kind")),
        _text(item["unresolved_name"], "unresolved_name"),
        _integer(item["created_revision"], "created_revision"),
    )


def _partial_date_from_value(value: object, field: str) -> PartialDate | None:
    if value is None:
        return None
    item = _object(value, ("precision", "value"), field)
    precision = _text(item["precision"], f"{field}.precision")
    parsed = PartialDate.parse(_text(item["value"], f"{field}.value"))
    if parsed.precision.value != precision:
        raise ValueError(f"{field} precision does not match value")
    return parsed


def _object(
    value: object, expected_fields: tuple[str, ...], label: str
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(expected_fields):
        raise ValueError(f"{label} must contain exactly the canonical fields")
    return value


def _list(value: dict[str, object], field: str) -> list[object]:
    items = value[field]
    if not isinstance(items, list):
        raise ValueError(f"{field} must be a list")
    return items


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _logical_id(value: object, prefix: str, field: str) -> str:
    text = _text(value, field)
    if not text.startswith(f"{prefix}_") or len(text) == len(prefix) + 1:
        raise ValueError(f"{field} must use the {prefix}_ logical-ID prefix")
    return text


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, field)


def _require_unique_ids(label: str, *groups) -> None:
    values = [item for group in groups for item in group]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} logical ID")


def _sorted_values(
    items: tuple[object, ...], serializer, id_field: str
) -> list[dict[str, object]]:
    values = [serializer(item) for item in items]
    return sorted(values, key=lambda value: str(value[id_field]))


def _partial_date_value(value: PartialDate | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"precision": value.precision.value, "value": value.value}


def _person_value(person: Person) -> dict[str, object]:
    return {
        "archived": person.archived,
        "birth": _partial_date_value(person.birth),
        "death": _partial_date_value(person.death),
        "full_name": person.full_name,
        "gender": person.gender.value,
        "is_alive": person.is_alive,
        "person_id": str(person.person_id),
        "primary_family_unit_id": (
            str(person.primary_family_unit_id)
            if person.primary_family_unit_id is not None
            else None
        ),
        "version_revision": person.version_revision,
    }


def _family_unit_value(family_unit: FamilyUnit) -> dict[str, object]:
    return {
        "adult_a_id": str(family_unit.adult_a_id),
        "adult_b_id": (
            str(family_unit.adult_b_id) if family_unit.adult_b_id is not None else None
        ),
        "created_revision": family_unit.created_revision,
        "distinct_union_confirmed": family_unit.distinct_union_confirmed,
        "end": _partial_date_value(family_unit.end),
        "family_unit_id": str(family_unit.family_unit_id),
        "kind": family_unit.kind.value,
        "start": _partial_date_value(family_unit.start),
        "status": family_unit.status.value,
    }


def _link_value(link: ParentChildLink) -> dict[str, object]:
    return {
        "child_id": str(link.child_id),
        "created_revision": link.created_revision,
        "family_unit_id": (
            str(link.family_unit_id) if link.family_unit_id is not None else None
        ),
        "link_id": str(link.link_id),
        "parent_id": str(link.parent_id),
        "relationship_type": link.relationship_type.value,
        "role": link.role.value,
    }


def _unresolved_value(annotation: UnresolvedRelationship) -> dict[str, object]:
    return {
        "created_revision": annotation.created_revision,
        "kind": annotation.kind.value,
        "subject_person_id": str(annotation.subject_person_id),
        "unresolved_id": str(annotation.unresolved_id),
        "unresolved_name": annotation.unresolved_name,
    }
