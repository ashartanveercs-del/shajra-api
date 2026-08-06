"""Append-only Airtable graph versions with commit-bound visibility."""

import hashlib
import hmac
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeAlias, cast

from domain.checksum import semantic_checksum
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
from repositories.airtable.mappers import (
    LiveEntityVersion,
    RepositoryTombstone,
    family_unit_from_row,
    family_unit_to_row,
    parent_child_link_from_row,
    parent_child_link_to_row,
    person_from_row,
    person_to_row,
    unresolved_from_row,
    unresolved_to_row,
)
from repositories.protocols import (
    CommitPermit,
    GraphCommit,
    GraphWriteSet,
    StagedWriteReceipt,
    WriteContext,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_commit_sha256,
    graph_write_set_sha256,
)


Entity = Person | FamilyUnit | ParentChildLink | UnresolvedRelationship
Version: TypeAlias = LiveEntityVersion[Any] | RepositoryTombstone
RowMapper: TypeAlias = Callable[[Mapping[str, object]], Version]


class RepositoryCorruptionError(RuntimeError):
    """Airtable rows disagree under one append-only logical identity."""


class AirtableGraphRepository:
    def __init__(self, client: Any, *, scope: str) -> None:
        if not scope:
            raise ValueError("repository scope must not be empty")
        self._client = client
        self.scope = scope
        self._verified_receipts: set[StagedWriteReceipt] = set()

    def stage(
        self, write_set: GraphWriteSet, context: WriteContext
    ) -> StagedWriteReceipt:
        self._verified_receipts = {
            receipt
            for receipt in self._verified_receipts
            if self._receipt_identity(receipt) != self._context_identity(context)
        }
        rows = self._rows_for_write_set(write_set, context)
        for table_name in self._entity_table_names():
            self._client.table(table_name).batch_create(rows[table_name])
        return StagedWriteReceipt(
            operation_id=context.operation_id,
            revision=context.revision,
            fencing_token=context.fencing_token,
            write_set_json=canonical_graph_write_set_json(write_set),
            write_set_sha256=graph_write_set_sha256(write_set),
        )

    def verify_staged(self, receipt: StagedWriteReceipt) -> None:
        try:
            encoded = receipt.write_set_json.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("staged receipt JSON must be ASCII") from error
        actual_receipt_digest = hashlib.sha256(encoded).hexdigest()
        if not hmac.compare_digest(actual_receipt_digest, receipt.write_set_sha256):
            raise ValueError("staged receipt digest does not match receipt JSON")

        actual_write_set = self._write_set_for_receipt(receipt)
        actual_json = canonical_graph_write_set_json(actual_write_set)
        if not hmac.compare_digest(actual_json, receipt.write_set_json):
            raise ValueError("staged write receipt does not match Airtable rows")
        self._verified_receipts.add(receipt)

    def append_commit(self, commit: GraphCommit, permit: CommitPermit) -> GraphState:
        self._validate_permit(commit, permit)
        verified_receipt = next(
            (
                receipt
                for receipt in self._verified_receipts
                if self._receipt_identity(receipt) == self._commit_identity(commit)
            ),
            None,
        )
        if verified_receipt is None:
            raise ValueError("staged write receipt has not been verified")
        self.verify_staged(verified_receipt)

        commits = self._logical_commits()
        existing = commits.get(commit.revision)
        if existing is not None:
            if canonical_graph_commit_json(existing) != canonical_graph_commit_json(
                commit
            ):
                raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")
            return self._state_for_commit(existing)

        head_revision = max(commits, default=0)
        if commit.revision != head_revision + 1:
            raise ValueError("commit revision must be sequential")

        proposed_commits = {**commits, commit.revision: commit}
        proposed = self._snapshot_for(proposed_commits, commit.revision)
        if not hmac.compare_digest(
            semantic_checksum(proposed), commit.semantic_checksum
        ):
            raise RepositoryCorruptionError("COMMIT_CHECKSUM_MISMATCH")

        commit_table = self._client.table("GraphCommits")
        create_error: Exception | None = None
        try:
            commit_table.batch_create([self._commit_row(commit)])
        except Exception as error:  # noqa: BLE001 - resolve ambiguous writes by readback.
            create_error = error

        committed = self._logical_commits().get(commit.revision)
        if committed is None:
            if create_error is not None:
                raise create_error
            raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")
        if canonical_graph_commit_json(committed) != canonical_graph_commit_json(
            commit
        ):
            raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")

        state = self._state_for_commit(committed)
        self._update_state_cache(state, committed.committed_at)
        return state

    def load_committed(self, revision: int | None = None) -> GraphSnapshot:
        if revision is not None and revision < 0:
            raise ValueError("revision must not be negative")
        commits = self._logical_commits()
        requested = max(commits, default=0) if revision is None else revision
        eligible = [item for item in commits if item <= requested]
        if not eligible:
            return GraphSnapshot(GraphState(0, None, 0, ""), {}, {}, {}, {})

        head_revision = max(eligible)
        snapshot = self._snapshot_for(commits, head_revision)
        expected = commits[head_revision].semantic_checksum
        if not hmac.compare_digest(semantic_checksum(snapshot), expected):
            raise RepositoryCorruptionError("COMMIT_CHECKSUM_MISMATCH")
        return snapshot

    def _validate_permit(self, commit: GraphCommit, permit: CommitPermit) -> None:
        if permit.scope != self.scope:
            raise ValueError("permit scope does not match repository scope")
        if (
            permit.operation_id != commit.operation_id
            or permit.revision != commit.revision
            or permit.fencing_token != commit.fencing_token
            or permit.permit_id != commit.permit_id
        ):
            raise ValueError("permit does not match commit identity")
        if not hmac.compare_digest(graph_commit_sha256(commit), permit.commit_sha256):
            raise ValueError("permit digest does not match commit")

    def _logical_commits(self) -> dict[int, GraphCommit]:
        candidates: dict[int, dict[str, GraphCommit]] = defaultdict(dict)
        for record in self._client.table("GraphCommits").all():
            commit = self._commit_from_row(self._fields(record))
            candidates[commit.revision][canonical_graph_commit_json(commit)] = commit

        logical: dict[int, GraphCommit] = {}
        for revision in sorted(candidates):
            if len(candidates[revision]) != 1:
                raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")
            logical[revision] = next(iter(candidates[revision].values()))
        return logical

    def _snapshot_for(
        self, commits: Mapping[int, GraphCommit], revision: int
    ) -> GraphSnapshot:
        head = commits[revision]
        materialized: dict[str, dict[str, Entity]] = {
            table_name: {} for table_name in self._entity_table_names()
        }
        for table_name, id_attribute, mapper in self._entity_specs():
            versions = self._authorized_versions(
                table_name, id_attribute, mapper, commits, revision
            )
            for logical_id, version in versions.items():
                if isinstance(version, LiveEntityVersion):
                    materialized[table_name][logical_id] = cast(Entity, version.entity)

        people = cast(dict[str, Person], materialized["PersonVersions"])
        family_units = cast(dict[str, FamilyUnit], materialized["FamilyUnits"])
        links = cast(dict[str, ParentChildLink], materialized["ParentChildLinks"])
        unresolved = cast(
            dict[str, UnresolvedRelationship],
            materialized["UnresolvedRelationships"],
        )
        return GraphSnapshot(
            self._state_for_commit(head),
            {person.person_id: person for person in people.values()},
            {family.family_unit_id: family for family in family_units.values()},
            {link.link_id: link for link in links.values()},
            {item.unresolved_id: item for item in unresolved.values()},
        )

    def _authorized_versions(
        self,
        table_name: str,
        id_attribute: str,
        mapper: RowMapper,
        commits: Mapping[int, GraphCommit],
        revision: int,
    ) -> dict[str, Version]:
        logical_versions: dict[tuple[str, int, str, int], Version] = {}
        for record in self._client.table(table_name).all():
            version = mapper(self._fields(record))
            commit = commits.get(version.revision)
            if (
                commit is None
                or version.revision > revision
                or version.operation_id != commit.operation_id
                or version.fencing_token != commit.fencing_token
            ):
                continue
            logical_id = self._version_id(version, id_attribute)
            key = (
                logical_id,
                version.revision,
                str(version.operation_id),
                version.fencing_token,
            )
            previous = logical_versions.get(key)
            if previous is not None and previous != version:
                raise RepositoryCorruptionError("ENTITY_VERSION_CORRUPTION")
            logical_versions[key] = version

        latest: dict[str, Version] = {}
        for (logical_id, row_revision, _, _), version in logical_versions.items():
            previous = latest.get(logical_id)
            if previous is None or row_revision > previous.revision:
                latest[logical_id] = version
        return latest

    def _write_set_for_receipt(self, receipt: StagedWriteReceipt) -> GraphWriteSet:
        values: dict[str, tuple[list[Entity], list[str]]] = {}
        for table_name, id_attribute, mapper in self._entity_specs():
            versions: dict[str, Version] = {}
            for record in self._client.table(table_name).all():
                version = mapper(self._fields(record))
                if (
                    version.revision != receipt.revision
                    or version.operation_id != receipt.operation_id
                    or version.fencing_token != receipt.fencing_token
                ):
                    continue
                logical_id = self._version_id(version, id_attribute)
                previous = versions.get(logical_id)
                if previous is not None and previous != version:
                    raise RepositoryCorruptionError("ENTITY_VERSION_CORRUPTION")
                versions[logical_id] = version
            upserts: list[Entity] = []
            tombstones: list[str] = []
            for logical_id in sorted(versions):
                version = versions[logical_id]
                if isinstance(version, RepositoryTombstone):
                    tombstones.append(logical_id)
                else:
                    upserts.append(cast(Entity, version.entity))
            values[table_name] = (upserts, tombstones)

        return GraphWriteSet(
            person_upserts=tuple(cast(list[Person], values["PersonVersions"][0])),
            person_tombstones=tuple(
                PersonId(item) for item in values["PersonVersions"][1]
            ),
            family_unit_upserts=tuple(cast(list[FamilyUnit], values["FamilyUnits"][0])),
            family_unit_tombstones=tuple(
                FamilyUnitId(item) for item in values["FamilyUnits"][1]
            ),
            parent_child_link_upserts=tuple(
                cast(list[ParentChildLink], values["ParentChildLinks"][0])
            ),
            parent_child_link_tombstones=tuple(
                LinkId(item) for item in values["ParentChildLinks"][1]
            ),
            unresolved_upserts=tuple(
                cast(
                    list[UnresolvedRelationship],
                    values["UnresolvedRelationships"][0],
                )
            ),
            unresolved_tombstones=tuple(
                UnresolvedRelationshipId(item)
                for item in values["UnresolvedRelationships"][1]
            ),
        )

    @staticmethod
    def _version_id(version: Version, id_attribute: str) -> str:
        if isinstance(version, RepositoryTombstone):
            return version.entity_id
        return str(getattr(version.entity, id_attribute))

    @staticmethod
    def _fields(record: object) -> Mapping[str, object]:
        if not isinstance(record, Mapping):
            raise RepositoryCorruptionError("AIRTABLE_ROW_CORRUPTION")
        fields = record.get("fields", record)
        if not isinstance(fields, Mapping):
            raise RepositoryCorruptionError("AIRTABLE_ROW_CORRUPTION")
        return cast(Mapping[str, object], fields)

    @staticmethod
    def _commit_from_row(row: Mapping[str, object]) -> GraphCommit:
        try:
            operation_id = OperationId(
                AirtableGraphRepository._required_text(row, "OperationId")
            )
            committed_at = datetime.fromisoformat(
                AirtableGraphRepository._required_text(row, "CommittedAt").replace(
                    "Z", "+00:00"
                )
            )
            commit = GraphCommit(
                operation_id=operation_id,
                revision=AirtableGraphRepository._integer(row, "Revision"),
                fencing_token=AirtableGraphRepository._integer(row, "FencingToken"),
                permit_id=AirtableGraphRepository._required_text(row, "PermitId"),
                semantic_checksum=AirtableGraphRepository._required_text(
                    row, "SemanticChecksum"
                ),
                committed_at=committed_at,
            )
            canonical_graph_commit_json(commit)
            return commit
        except (TypeError, ValueError) as error:
            raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION") from error

    @staticmethod
    def _commit_row(commit: GraphCommit) -> dict[str, object]:
        return {
            "Revision": commit.revision,
            "OperationId": str(commit.operation_id),
            "FencingToken": commit.fencing_token,
            "PermitId": commit.permit_id,
            "SemanticChecksum": commit.semantic_checksum,
            "CommittedAt": commit.committed_at.isoformat(),
        }

    def _update_state_cache(self, state: GraphState, committed_at: datetime) -> None:
        row = {
            "StateKey": "graph",
            "Revision": state.revision,
            "HeadOperationId": str(state.head_operation_id),
            "FencingToken": state.fencing_token,
            "SemanticChecksum": state.semantic_checksum,
            "UpdatedAt": committed_at.isoformat(),
        }
        try:
            self._client.table("GraphState").batch_upsert(
                [row], key_fields=["StateKey"]
            )
        except Exception:  # noqa: BLE001 - GraphState is a best-effort cache.
            return

    @staticmethod
    def _rows_for_write_set(
        write_set: GraphWriteSet, context: WriteContext
    ) -> dict[str, list[dict[str, object]]]:
        return {
            "PersonVersions": [
                *(
                    person_to_row(person, context)
                    for person in write_set.person_upserts
                ),
                *(
                    AirtableGraphRepository._tombstone_row("PersonId", item, context)
                    for item in write_set.person_tombstones
                ),
            ],
            "FamilyUnits": [
                *(
                    family_unit_to_row(family, context)
                    for family in write_set.family_unit_upserts
                ),
                *(
                    AirtableGraphRepository._tombstone_row(
                        "FamilyUnitId", item, context
                    )
                    for item in write_set.family_unit_tombstones
                ),
            ],
            "ParentChildLinks": [
                *(
                    parent_child_link_to_row(link, context)
                    for link in write_set.parent_child_link_upserts
                ),
                *(
                    AirtableGraphRepository._tombstone_row("LinkId", item, context)
                    for item in write_set.parent_child_link_tombstones
                ),
            ],
            "UnresolvedRelationships": [
                *(
                    unresolved_to_row(item, context)
                    for item in write_set.unresolved_upserts
                ),
                *(
                    AirtableGraphRepository._tombstone_row(
                        "UnresolvedId", item, context
                    )
                    for item in write_set.unresolved_tombstones
                ),
            ],
        }

    @staticmethod
    def _tombstone_row(
        id_field: str, logical_id: object, context: WriteContext
    ) -> dict[str, object]:
        return {
            id_field: str(logical_id),
            "Revision": context.revision,
            "OperationId": str(context.operation_id),
            "FencingToken": context.fencing_token,
            "IsTombstone": True,
        }

    @staticmethod
    def _entity_specs() -> Sequence[tuple[str, str, RowMapper]]:
        return (
            ("PersonVersions", "person_id", person_from_row),
            ("FamilyUnits", "family_unit_id", family_unit_from_row),
            ("ParentChildLinks", "link_id", parent_child_link_from_row),
            ("UnresolvedRelationships", "unresolved_id", unresolved_from_row),
        )

    @staticmethod
    def _entity_table_names() -> tuple[str, ...]:
        return (
            "PersonVersions",
            "FamilyUnits",
            "ParentChildLinks",
            "UnresolvedRelationships",
        )

    @staticmethod
    def _required_text(row: Mapping[str, object], field: str) -> str:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _integer(row: Mapping[str, object], field: str) -> int:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        return value

    @staticmethod
    def _receipt_identity(receipt: StagedWriteReceipt) -> tuple[OperationId, int, int]:
        return receipt.operation_id, receipt.revision, receipt.fencing_token

    @staticmethod
    def _context_identity(context: WriteContext) -> tuple[OperationId, int, int]:
        return context.operation_id, context.revision, context.fencing_token

    @staticmethod
    def _commit_identity(commit: GraphCommit) -> tuple[OperationId, int, int]:
        return commit.operation_id, commit.revision, commit.fencing_token

    @staticmethod
    def _state_for_commit(commit: GraphCommit) -> GraphState:
        return GraphState(
            commit.revision,
            commit.operation_id,
            commit.fencing_token,
            commit.semantic_checksum,
        )
