"""Coordination-owned runtime and operator contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, TypeAlias

from repositories import CommitPermit, GraphCommit, StagedWriteReceipt


class CoordinationError(RuntimeError):
    """Stable fail-closed coordination error without sensitive details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Lease:
    scope: str
    acquisition_id: str
    expires_at_ms: int
    ttl_ms: int
    renew_deadline_ms: int


@dataclass(frozen=True, slots=True)
class GraphLease:
    scope: str
    acquisition_id: str
    fencing_token: int
    base_revision: int
    expires_at_ms: int
    ttl_ms: int
    renew_deadline_ms: int


@dataclass(frozen=True, slots=True)
class CommitReservation:
    scope: str
    state: Literal["COMMITTING"]
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str
    staged_write_receipt: StagedWriteReceipt


@dataclass(frozen=True, slots=True)
class ConfirmedCommitReceipt:
    scope: str
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str
    staged_write_receipt: StagedWriteReceipt


@dataclass(frozen=True, slots=True)
class ReconciledHeadReceipt:
    scope: str
    revision: int
    semantic_checksum: str
    head_commit_sha256: str | None
    evidence_sha256: str
    admin_request_nonce_hmac: str


ConfirmationProof: TypeAlias = ConfirmedCommitReceipt | ReconciledHeadReceipt


@dataclass(frozen=True, slots=True)
class CommitCoordinatorStatus:
    scope: str
    mode: Literal["READY", "COMMITTING"]
    confirmed_revision: int
    fencing_floor: int
    active_reservation: CommitReservation | None
    last_confirmation_proof: ConfirmationProof
    state_sha256: str


@dataclass(frozen=True, slots=True)
class CoordinationEvidence:
    scope: str
    committed_head_revision: int
    committed_head_semantic_checksum: str
    committed_head_commit_sha256: str | None
    max_durable_fencing_token: int
    fencing_floor: int
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class LeaseReleaseResult:
    code: Literal["LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"]
    acquisition_id: str
    released_at_ms: int


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    code: Literal["CONFIRMED", "CONFIRMATION_REPLAYED", "CONFIRMATION_PROOF_EVICTED"]
    requested_permit: CommitPermit
    confirmed_revision: int


@dataclass(frozen=True, slots=True)
class CoordinationInspection:
    scope: str
    mode: Literal["UNINITIALIZED", "READY", "COMMITTING", "CORRUPT"]
    confirmed_revision: int | None
    fencing_floor: int | None
    lock_present: bool
    active_reservation: CommitReservation | None
    last_confirmation_proof: ConfirmationProof | None
    state_sha256: str


@dataclass(frozen=True, slots=True)
class CoordinationAdminResult:
    code: Literal["ADMIN_INITIALIZED", "ADMIN_RECONCILED"]
    previous_state_sha256: str
    state_sha256: str
    confirmed_revision: int
    fencing_floor: int


@dataclass(frozen=True, slots=True)
class RevocationResult:
    code: Literal["REVOKED", "ALREADY_REVOKED", "NOT_REVOKED", "TOKEN_ALREADY_EXPIRED"]
    revoked: bool
    server_time_ms: int
    expires_at_ms: int


class RateLimitPolicyId(StrEnum):
    LOGIN = "login"
    SUBMIT = "submit"
    UPLOAD = "upload"
    COMMENT = "comment"
    STORY = "story"
    SEARCH = "search"
    EMAIL_VERIFICATION = "email-verification"


@dataclass(frozen=True, slots=True)
class IpRateLimitSubject:
    kind: Literal["IP"]
    normalized_ip: str


@dataclass(frozen=True, slots=True)
class IdentityRateLimitSubject:
    kind: Literal["IDENTITY"]
    identity_id: str


RateLimitSubject: TypeAlias = IpRateLimitSubject | IdentityRateLimitSubject


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    policy: RateLimitPolicyId
    allowed: bool
    limit: int
    observed_count: int
    remaining: int
    server_time_ms: int
    reset_at_ms: int
    retry_after_ms: int


class LeaseManager(Protocol):
    def acquire(
        self, scope: str, acquisition_id: str, ttl_ms: int = 15_000
    ) -> Lease: ...

    def renew(
        self, lease: Lease, request_nonce: str, ttl_ms: int = 15_000
    ) -> Lease: ...

    def assert_owned(self, lease: Lease) -> None: ...

    def release(self, lease: Lease, request_nonce: str) -> LeaseReleaseResult: ...


class CommitCoordinator(Protocol):
    def acquire(
        self,
        scope: str,
        committed_revision: int,
        acquisition_id: str,
        ttl_ms: int = 15_000,
    ) -> GraphLease: ...

    def renew(
        self, lease: GraphLease, request_nonce: str, ttl_ms: int = 15_000
    ) -> GraphLease: ...

    def assert_owned(self, lease: GraphLease) -> None: ...

    def authorize_commit(
        self,
        lease: GraphLease,
        commit: GraphCommit,
        staged_write_receipt: StagedWriteReceipt,
        request_nonce: str,
    ) -> CommitPermit: ...

    def get_status(self, scope: str) -> CommitCoordinatorStatus: ...

    def confirm_commit(
        self, permit: CommitPermit, commit: GraphCommit, request_nonce: str
    ) -> ConfirmationResult: ...

    def release(self, lease: GraphLease, request_nonce: str) -> LeaseReleaseResult: ...


class CoordinationAdmin(Protocol):
    def inspect(self, scope: str) -> CoordinationInspection: ...

    def initialize(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult: ...

    def reconcile(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult: ...


class RevocationStore(Protocol):
    def revoke(
        self, jti: str, token_expires_at_s: int, request_nonce: str
    ) -> RevocationResult: ...

    def is_revoked(self, jti: str, token_expires_at_s: int) -> RevocationResult: ...


class RateLimiter(Protocol):
    def consume(
        self,
        policy: RateLimitPolicyId,
        subject: RateLimitSubject,
        request_nonce: str,
    ) -> RateLimitResult: ...
