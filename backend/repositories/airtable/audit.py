"""Append-only Airtable audit transitions for graph operations."""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from domain.ids import OperationId
from repositories.airtable.graph import RepositoryCorruptionError
from repositories.protocols import (
    AuditOperation,
    AuditOperationState,
    GraphCommit,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_write_set_from_json,
)


_ALLOWED_TRANSITIONS = {
    AuditOperationState.PENDING: frozenset(
        {
            AuditOperationState.COMMITTING,
            AuditOperationState.COMMITTED,
            AuditOperationState.FAILED,
        }
    ),
    AuditOperationState.COMMITTING: frozenset({AuditOperationState.COMMITTED}),
    AuditOperationState.COMMITTED: frozenset(),
    AuditOperationState.FAILED: frozenset(),
}
_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "airtablerecordid",
        "contact",
        "contacts",
        "credential",
        "credentials",
        "email",
        "mobile",
        "password",
        "personalaccesstoken",
        "phone",
        "phonenumber",
        "recordid",
        "secret",
        "sourcerecordid",
        "token",
        "whatsapp",
    }
)
_SENSITIVE_KEY_SUFFIXES = tuple(_SENSITIVE_KEYS)


@dataclass(frozen=True, slots=True)
class _AuditTransition:
    operation: AuditOperation
    created_at: datetime
    updated_at: datetime


class AirtableAuditRepository:
    def __init__(self, client: Any, *, scope: str, clock=None) -> None:
        if not scope:
            raise ValueError("audit repository scope must not be empty")
        self._client = client
        self.scope = scope
        self._clock = clock or (lambda: datetime.now(UTC))

    def find_by_idempotency_key(self, key: str) -> AuditOperation | None:
        matching = [
            transition
            for transition in self._transitions()
            if transition.operation.idempotency_key == key
        ]
        if not matching:
            return None
        operation_ids = {transition.operation.operation_id for transition in matching}
        if len(operation_ids) != 1:
            raise RepositoryCorruptionError("IDEMPOTENCY_KEY_CONFLICT")
        return self._latest(matching).operation

    def create_pending(self, operation: AuditOperation) -> None:
        if operation.state is not AuditOperationState.PENDING:
            raise ValueError("audit operation must start in PENDING")
        if operation.commit_scope != self.scope:
            raise ValueError("audit operation scope does not match repository scope")
        canonical = self._canonical_operation(operation)
        transitions = self._transitions()

        same_key = [
            item
            for item in transitions
            if item.operation.idempotency_key == canonical.idempotency_key
        ]
        if same_key:
            operation_ids = {item.operation.operation_id for item in same_key}
            if operation_ids != {canonical.operation_id}:
                raise ValueError("IDEMPOTENCY_KEY_CONFLICT")
            existing = self._latest(same_key).operation
            if self._immutable_value(existing) != self._immutable_value(canonical):
                raise ValueError("IDEMPOTENCY_KEY_CONFLICT")
            return

        if any(
            item.operation.operation_id == canonical.operation_id
            for item in transitions
        ):
            raise ValueError("AUDIT_OPERATION_CONFLICT")

        now = self._aware_now()
        self._append(_AuditTransition(canonical, now, now))

    def transition(self, operation_id: OperationId, state: AuditOperationState) -> None:
        matching = [
            item
            for item in self._transitions()
            if item.operation.operation_id == operation_id
        ]
        if not matching:
            raise ValueError("audit operation does not exist")
        latest = self._latest(matching)
        if latest.operation.state is state:
            return
        if state not in _ALLOWED_TRANSITIONS[latest.operation.state]:
            raise ValueError("invalid audit state transition")

        now = max(self._aware_now(), latest.updated_at + timedelta(microseconds=1))
        self._append(
            _AuditTransition(
                replace(latest.operation, state=state),
                latest.created_at,
                now,
            )
        )

    def _transitions(self) -> list[_AuditTransition]:
        transitions: list[_AuditTransition] = []
        for record in self._client.table("ChangeLog").all():
            fields = self._fields(record)
            if fields.get("CommitScope") != self.scope:
                continue
            transitions.append(self._transition_from_row(fields))
        return transitions

    def _latest(self, transitions: list[_AuditTransition]) -> _AuditTransition:
        immutable_values = {
            self._immutable_value(transition.operation) for transition in transitions
        }
        if len(immutable_values) != 1:
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")
        if len({transition.created_at for transition in transitions}) != 1:
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")

        by_time: dict[datetime, dict[str, _AuditTransition]] = {}
        for transition in transitions:
            canonical = self._transition_value(transition)
            by_time.setdefault(transition.updated_at, {})[canonical] = transition
        if any(len(candidates) != 1 for candidates in by_time.values()):
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")

        ordered = [
            next(iter(by_time[updated_at].values())) for updated_at in sorted(by_time)
        ]
        current = ordered[0]
        if current.operation.state is not AuditOperationState.PENDING:
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")
        for candidate in ordered[1:]:
            if candidate.operation.state is current.operation.state:
                current = candidate
                continue
            if (
                candidate.operation.state
                not in _ALLOWED_TRANSITIONS[current.operation.state]
            ):
                raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")
            current = candidate
        return current

    def _append(self, transition: _AuditTransition) -> None:
        self._client.table("ChangeLog").batch_create(
            [self._row_for_transition(transition)]
        )

    def _row_for_transition(self, transition: _AuditTransition) -> dict[str, object]:
        operation = transition.operation
        commit = self._commit_from_json(operation.graph_commit_json)
        return {
            "OperationId": str(operation.operation_id),
            "IdempotencyKey": operation.idempotency_key,
            "State": operation.state.value,
            "ActorId": operation.actor_id,
            "RequestId": operation.request_id,
            "SourceReference": operation.source_reference or "",
            "ExpectedRevision": commit.revision - 1,
            "ResultRevision": commit.revision,
            "FencingToken": commit.fencing_token,
            "CommandsJson": operation.commands_json,
            "BeforeSnapshotJson": operation.before_snapshot_json,
            "AfterSnapshotJson": operation.after_snapshot_json,
            "InverseWriteSetJson": operation.inverse_write_set_json,
            "CommitScope": operation.commit_scope,
            "GraphCommitJson": operation.graph_commit_json,
            "CommitSha256": operation.commit_sha256,
            "CreatedAt": self._isoformat(transition.created_at),
            "UpdatedAt": self._isoformat(transition.updated_at),
        }

    def _transition_from_row(self, row: Mapping[str, object]) -> _AuditTransition:
        try:
            operation = AuditOperation(
                operation_id=OperationId(self._required_text(row, "OperationId")),
                idempotency_key=self._required_text(row, "IdempotencyKey"),
                state=AuditOperationState(self._required_text(row, "State")),
                actor_id=self._required_text(row, "ActorId"),
                request_id=self._required_text(row, "RequestId"),
                source_reference=self._optional_text(row, "SourceReference"),
                commands_json=self._required_text(row, "CommandsJson"),
                before_snapshot_json=self._required_text(row, "BeforeSnapshotJson"),
                after_snapshot_json=self._required_text(row, "AfterSnapshotJson"),
                inverse_write_set_json=self._canonical_inverse_write_set(
                    self._required_text(row, "InverseWriteSetJson")
                ),
                commit_scope=self._required_text(row, "CommitScope"),
                graph_commit_json=self._canonical_commit_json(
                    self._required_text(row, "GraphCommitJson")
                ),
                commit_sha256=self._required_text(row, "CommitSha256"),
            )
            canonical = self._canonical_operation(operation)
            commit = self._commit_from_json(canonical.graph_commit_json)
            if (
                self._integer(row, "ExpectedRevision") != commit.revision - 1
                or self._integer(row, "ResultRevision") != commit.revision
                or self._integer(row, "FencingToken") != commit.fencing_token
            ):
                raise ValueError("audit commit metadata does not match GraphCommitJson")
            created_at = self._timestamp(row, "CreatedAt")
            updated_at = self._timestamp(row, "UpdatedAt")
            if created_at > updated_at:
                raise ValueError("audit CreatedAt must not follow UpdatedAt")
            return _AuditTransition(
                canonical,
                created_at,
                updated_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION") from error

    def _canonical_operation(self, operation: AuditOperation) -> AuditOperation:
        if not operation.idempotency_key:
            raise ValueError("idempotency key must not be empty")
        if operation.commit_scope != self.scope:
            raise ValueError("audit operation scope does not match repository scope")

        graph_commit_json = self._canonical_commit_json(operation.graph_commit_json)
        commit = self._commit_from_json(graph_commit_json)
        if commit.operation_id != operation.operation_id:
            raise ValueError("audit operation does not match graph commit")
        digest = hashlib.sha256(graph_commit_json.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, operation.commit_sha256):
            raise ValueError("audit commit digest does not match graph commit")

        return replace(
            operation,
            commands_json=self._canonical_redacted_json(operation.commands_json),
            before_snapshot_json=self._canonical_redacted_json(
                operation.before_snapshot_json
            ),
            after_snapshot_json=self._canonical_redacted_json(
                operation.after_snapshot_json
            ),
            inverse_write_set_json=self._canonical_inverse_write_set(
                operation.inverse_write_set_json
            ),
            graph_commit_json=graph_commit_json,
            commit_sha256=digest,
        )

    @staticmethod
    def _canonical_commit_json(value: str) -> str:
        return canonical_graph_commit_json(
            AirtableAuditRepository._commit_from_json(value)
        )

    @staticmethod
    def _commit_from_json(value: str) -> GraphCommit:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != {
            "committed_at",
            "fencing_token",
            "operation_id",
            "permit_id",
            "revision",
            "semantic_checksum",
        }:
            raise ValueError("GraphCommitJson is malformed")
        committed_at = parsed["committed_at"]
        if not isinstance(committed_at, str):
            raise ValueError("GraphCommitJson committed_at is malformed")
        revision = parsed["revision"]
        fencing_token = parsed["fencing_token"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
        ):
            raise ValueError("GraphCommitJson integer fields are malformed")
        operation_id = parsed["operation_id"]
        permit_id = parsed["permit_id"]
        checksum = parsed["semantic_checksum"]
        if not all(
            isinstance(item, str) and item
            for item in (operation_id, permit_id, checksum)
        ):
            raise ValueError("GraphCommitJson text fields are malformed")
        return GraphCommit(
            operation_id=OperationId(operation_id),
            revision=revision,
            fencing_token=fencing_token,
            permit_id=permit_id,
            semantic_checksum=checksum,
            committed_at=datetime.fromisoformat(committed_at.replace("Z", "+00:00")),
        )

    @staticmethod
    def _canonical_inverse_write_set(value: str) -> str:
        return canonical_graph_write_set_json(graph_write_set_from_json(value))

    @staticmethod
    def _canonical_redacted_json(value: str) -> str:
        parsed = json.loads(value)
        redacted = AirtableAuditRepository._redact(parsed)
        return json.dumps(
            redacted,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @staticmethod
    def _redact(value: object) -> object:
        if isinstance(value, dict):
            result: dict[str, object] = {}
            for key, item in value.items():
                normalized_key = "".join(
                    character for character in key.lower() if character.isalnum()
                )
                if normalized_key.endswith(_SENSITIVE_KEY_SUFFIXES):
                    continue
                result[key] = AirtableAuditRepository._redact(item)
            return result
        if isinstance(value, list):
            return [AirtableAuditRepository._redact(item) for item in value]
        return value

    @staticmethod
    def _fields(record: object) -> Mapping[str, object]:
        if not isinstance(record, Mapping):
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")
        fields = record.get("fields", record)
        if not isinstance(fields, Mapping):
            raise RepositoryCorruptionError("AUDIT_LOG_CORRUPTION")
        return cast(Mapping[str, object], fields)

    @staticmethod
    def _required_text(row: Mapping[str, object], field: str) -> str:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _optional_text(row: Mapping[str, object], field: str) -> str | None:
        value = row.get(field)
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text or blank")
        return value

    @staticmethod
    def _integer(row: Mapping[str, object], field: str) -> int:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        return value

    @staticmethod
    def _timestamp(row: Mapping[str, object], field: str) -> datetime:
        timestamp = datetime.fromisoformat(
            AirtableAuditRepository._required_text(row, field).replace("Z", "+00:00")
        )
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return timestamp.astimezone(UTC)

    def _aware_now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("audit clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    @staticmethod
    def _isoformat(value: datetime) -> str:
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _immutable_value(operation: AuditOperation) -> str:
        value = {
            "actor_id": operation.actor_id,
            "after_snapshot_json": operation.after_snapshot_json,
            "before_snapshot_json": operation.before_snapshot_json,
            "commands_json": operation.commands_json,
            "commit_scope": operation.commit_scope,
            "commit_sha256": operation.commit_sha256,
            "graph_commit_json": operation.graph_commit_json,
            "idempotency_key": operation.idempotency_key,
            "inverse_write_set_json": operation.inverse_write_set_json,
            "operation_id": str(operation.operation_id),
            "request_id": operation.request_id,
            "source_reference": operation.source_reference,
        }
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    @staticmethod
    def _transition_value(transition: _AuditTransition) -> str:
        return json.dumps(
            {
                "created_at": AirtableAuditRepository._isoformat(transition.created_at),
                "operation": AirtableAuditRepository._immutable_value(
                    transition.operation
                ),
                "state": transition.operation.state.value,
                "updated_at": AirtableAuditRepository._isoformat(transition.updated_at),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
