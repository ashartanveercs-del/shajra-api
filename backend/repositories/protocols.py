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
    GraphSnapshot,
    GraphState,
    ParentChildLink,
    Person,
    UnresolvedRelationship,
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

    def append_commit(self, commit: GraphCommit, permit: CommitPermit) -> GraphState: ...


class AuditRepository(Protocol):
    def find_by_idempotency_key(self, key: str) -> AuditOperation | None: ...

    def create_pending(self, operation: AuditOperation) -> None: ...

    def transition(self, operation_id: OperationId, state: AuditOperationState) -> None: ...


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
    return hashlib.sha256(canonical_graph_commit_json(commit).encode("ascii")).hexdigest()


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
    return hashlib.sha256(canonical_graph_write_set_json(write_set).encode("ascii")).hexdigest()


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
