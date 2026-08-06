"""Deterministic, no-network append-only repository for local tests."""

import hmac
from collections import defaultdict
from dataclasses import dataclass

from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.models import (
    FamilyUnit,
    GraphSnapshot,
    GraphState,
    ParentChildLink,
    Person,
    UnresolvedRelationship,
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
    validate_graph_commit,
)


class RepositoryCorruptionError(RuntimeError):
    """Append-only records disagree under a single logical identity."""


@dataclass(frozen=True, slots=True)
class _EntityRow:
    kind: str
    logical_id: str
    revision: int
    operation_id: str
    fencing_token: int
    tombstone: bool
    entity: Person | FamilyUnit | ParentChildLink | UnresolvedRelationship | None


class InMemoryGraphRepository:
    def __init__(self, scope: str = "memory:shajra") -> None:
        self.scope = scope
        self._entity_rows: list[_EntityRow] = []
        self._commits: list[GraphCommit] = []
        self._staged_receipts: set[StagedWriteReceipt] = set()

    @property
    def commit_count(self) -> int:
        return len(self._logical_commits())

    def stage(
        self, write_set: GraphWriteSet, context: WriteContext
    ) -> StagedWriteReceipt:
        self._entity_rows.extend(self._rows_for_write_set(write_set, context))
        receipt = StagedWriteReceipt(
            operation_id=context.operation_id,
            revision=context.revision,
            fencing_token=context.fencing_token,
            write_set_json=canonical_graph_write_set_json(write_set),
            write_set_sha256=graph_write_set_sha256(write_set),
        )
        self._staged_receipts.add(receipt)
        return receipt

    def verify_staged(self, receipt: StagedWriteReceipt) -> None:
        if receipt not in self._staged_receipts:
            raise ValueError("staged write receipt is not present")

    def append_commit(self, commit: GraphCommit, permit: CommitPermit) -> GraphState:
        commit = validate_graph_commit(commit)
        self._validate_permit(commit, permit)
        commits = self._logical_commits()
        existing = commits.get(commit.revision)
        if existing is not None:
            if canonical_graph_commit_json(existing) == canonical_graph_commit_json(
                commit
            ):
                return self._state_for_commit(existing)
            raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")

        head_revision = max(commits, default=0)
        if commit.revision != head_revision + 1:
            raise ValueError("commit revision must be sequential")
        self._commits.append(commit)
        return self._state_for_commit(commit)

    def load_committed(self, revision: int | None = None) -> GraphSnapshot:
        if revision is not None and revision < 0:
            raise ValueError("revision must not be negative")
        commits = self._logical_commits()
        if not commits:
            if revision not in (None, 0):
                raise ValueError("committed revision does not exist")
            return GraphSnapshot(self._initial_state(), {}, {}, {}, {})
        if revision is not None and revision not in commits:
            raise ValueError("committed revision does not exist")
        head_commit = commits[max(commits) if revision is None else revision]
        authorized_rows = self._authorized_rows(commits, head_commit.revision)
        people, family_units, links, unresolved = self._materialize(authorized_rows)
        return GraphSnapshot(
            self._state_for_commit(head_commit), people, family_units, links, unresolved
        )

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
        actual_digest = graph_commit_sha256(commit)
        if not hmac.compare_digest(actual_digest, permit.commit_sha256):
            raise ValueError("permit digest does not match commit")

    def _logical_commits(self) -> dict[int, GraphCommit]:
        commits_by_revision: dict[int, dict[str, GraphCommit]] = defaultdict(dict)
        for commit in self._commits:
            try:
                normalized = validate_graph_commit(commit)
                canonical = canonical_graph_commit_json(normalized)
            except ValueError as error:
                raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION") from error
            commits_by_revision[normalized.revision][canonical] = normalized
        logical: dict[int, GraphCommit] = {}
        for revision, candidates in commits_by_revision.items():
            if len(candidates) != 1:
                raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")
            logical[revision] = next(iter(candidates.values()))
        if sorted(logical) != list(range(1, len(logical) + 1)):
            raise RepositoryCorruptionError("COMMIT_LOG_CORRUPTION")
        return logical

    def _authorized_rows(
        self, commits: dict[int, GraphCommit], revision: int
    ) -> list[_EntityRow]:
        versions: dict[tuple[str, str, int, str, int], _EntityRow] = {}
        for row in self._entity_rows:
            commit = commits.get(row.revision)
            if (
                commit is None
                or row.revision > revision
                or row.operation_id != str(commit.operation_id)
                or row.fencing_token != commit.fencing_token
            ):
                continue
            version_key = (
                row.kind,
                row.logical_id,
                row.revision,
                row.operation_id,
                row.fencing_token,
            )
            previous = versions.get(version_key)
            if previous is not None and previous != row:
                raise RepositoryCorruptionError("ENTITY_VERSION_CORRUPTION")
            versions[version_key] = row

        latest: dict[tuple[str, str], _EntityRow] = {}
        for row in versions.values():
            logical_key = (row.kind, row.logical_id)
            previous = latest.get(logical_key)
            if previous is None or row.revision > previous.revision:
                latest[logical_key] = row
        return list(latest.values())

    def _materialize(
        self, rows: list[_EntityRow]
    ) -> tuple[
        dict[PersonId, Person],
        dict[FamilyUnitId, FamilyUnit],
        dict[LinkId, ParentChildLink],
        dict[UnresolvedRelationshipId, UnresolvedRelationship],
    ]:
        people: dict[PersonId, Person] = {}
        family_units: dict[FamilyUnitId, FamilyUnit] = {}
        links: dict[LinkId, ParentChildLink] = {}
        unresolved: dict[UnresolvedRelationshipId, UnresolvedRelationship] = {}
        for row in rows:
            if row.tombstone:
                continue
            if isinstance(row.entity, Person):
                people[row.entity.person_id] = row.entity
            elif isinstance(row.entity, FamilyUnit):
                family_units[row.entity.family_unit_id] = row.entity
            elif isinstance(row.entity, ParentChildLink):
                links[row.entity.link_id] = row.entity
            elif isinstance(row.entity, UnresolvedRelationship):
                unresolved[row.entity.unresolved_id] = row.entity
        return people, family_units, links, unresolved

    def _rows_for_write_set(
        self, write_set: GraphWriteSet, context: WriteContext
    ) -> list[_EntityRow]:
        rows: list[_EntityRow] = []
        for kind, upserts, tombstones, attribute in (
            (
                "person",
                write_set.person_upserts,
                write_set.person_tombstones,
                "person_id",
            ),
            (
                "family_unit",
                write_set.family_unit_upserts,
                write_set.family_unit_tombstones,
                "family_unit_id",
            ),
            (
                "parent_child_link",
                write_set.parent_child_link_upserts,
                write_set.parent_child_link_tombstones,
                "link_id",
            ),
            (
                "unresolved",
                write_set.unresolved_upserts,
                write_set.unresolved_tombstones,
                "unresolved_id",
            ),
        ):
            for entity in upserts:
                rows.append(
                    self._row(
                        kind, str(getattr(entity, attribute)), context, False, entity
                    )
                )
            for logical_id in tombstones:
                rows.append(self._row(kind, str(logical_id), context, True, None))
        return rows

    @staticmethod
    def _row(
        kind: str,
        logical_id: str,
        context: WriteContext,
        tombstone: bool,
        entity: Person | FamilyUnit | ParentChildLink | UnresolvedRelationship | None,
    ) -> _EntityRow:
        return _EntityRow(
            kind=kind,
            logical_id=logical_id,
            revision=context.revision,
            operation_id=str(context.operation_id),
            fencing_token=context.fencing_token,
            tombstone=tombstone,
            entity=entity,
        )

    @staticmethod
    def _initial_state() -> GraphState:
        return GraphState(0, None, 0, "")

    @staticmethod
    def _state_for_commit(commit: GraphCommit) -> GraphState:
        return GraphState(
            commit.revision,
            commit.operation_id,
            commit.fencing_token,
            commit.semantic_checksum,
        )
