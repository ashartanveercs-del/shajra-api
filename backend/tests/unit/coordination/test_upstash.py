from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from coordination.protocols import (
    ConfirmedCommitReceipt,
    CoordinationAdminResult,
    CoordinationEvidence,
    CoordinationError,
    GraphLease,
    Lease,
    LeaseReleaseResult,
    ReconciledHeadReceipt,
)
from coordination.serialization import (
    CanonicalInput,
    RedisKeyBuilder,
    deserialize_commit_reservation,
    deserialize_confirmed_commit_receipt,
    deserialize_coordination_evidence,
    deserialize_reconciled_head_receipt,
    serialize_confirmed_commit_receipt,
    serialize_admin_result_receipt,
    serialize_lease_acquisition_receipt,
    serialize_generic_lock,
    serialize_graph_lock,
    serialize_lease_operation_receipt,
    serialize_reconciled_head_receipt,
    coordination_state_sha256,
)


class LeaseEvalStub:
    """Deterministic local Redis-time model for production adapter tests."""

    def __init__(self, keys: RedisKeyBuilder, now_ms: int = 100_000) -> None:
        self.keys = keys
        self.now_ms = now_ms
        self.applied_pttl_loss_ms = 0
        self.leases: dict[str, Lease | GraphLease] = {}
        self.lease_expiry: dict[str, int] = {}
        self.receipts: dict[str, str] = {}
        self.receipt_expiry: dict[str, int | None] = {}
        self.scalars: dict[str, str] = {}
        self.reservations: set[str] = set()
        self.reservation_values: dict[str, str] = {}
        self.proofs: dict[str, str] = {}
        self.revocations: dict[str, str] = {}
        self.revocation_expiry: dict[str, int | None] = {}
        self.rate_counters: dict[str, str] = {}
        self.rate_counter_expiry: dict[str, int | None] = {}
        self.calls: list[tuple[str, list[str], list[str], bool]] = []

    def initialize_graph(self, scope: str, revision: int, fencing_floor: int) -> None:
        self.scalars[self.keys.graph_confirmed_revision(scope)] = str(revision)
        self.scalars[self.keys.graph_fence(scope)] = str(fencing_floor)
        proof = ReconciledHeadReceipt(
            scope,
            revision,
            "a" * 64,
            None if revision == 0 else "b" * 64,
            "c" * 64,
            self.keys.admin_nonce_hmac("initial-proof"),
        )
        self.proofs[self.keys.graph_last_confirmation(scope)] = (
            serialize_reconciled_head_receipt(proof, self.keys)
        )

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds
        self._expire()

    def _expire(self) -> None:
        for key, expiry in list(self.lease_expiry.items()):
            if expiry <= self.now_ms:
                self.lease_expiry.pop(key)
                self.leases.pop(key, None)
        for key, expiry in list(self.receipt_expiry.items()):
            if expiry is not None and expiry <= self.now_ms:
                self.receipt_expiry.pop(key)
                self.receipts.pop(key, None)
        for key, expiry in list(self.revocation_expiry.items()):
            if expiry is not None and expiry <= self.now_ms:
                self.revocation_expiry.pop(key)
                self.revocations.pop(key, None)
        for key, expiry in list(self.rate_counter_expiry.items()):
            if expiry is not None and expiry <= self.now_ms:
                self.rate_counter_expiry.pop(key)
                self.rate_counters.pop(key, None)

    def eval(
        self,
        script: str,
        keys: list[str],
        args: list[str],
        *,
        nonce_idempotent: bool,
    ) -> list[object]:
        self._expire()
        self.calls.append((script, list(keys), list(args), nonce_idempotent))
        if "shajra:generic-acquire:v1" in script:
            return self._acquire("GENERIC", keys, args)
        if "shajra:graph-acquire:v1" in script:
            return self._acquire("GRAPH_COMMIT", keys, args)
        if "shajra:lease-renew:v1" in script:
            return self._renew(keys, args)
        if "shajra:lease-release:v1" in script:
            return self._release(keys, args)
        if "shajra:lease-assert:v1" in script:
            return self._assert_owned(keys, args)
        if "shajra:commit-authorize:v1" in script:
            return self._authorize(keys, args)
        if "shajra:coordination-status:v1" in script:
            return self._status(keys, args)
        if "shajra:commit-confirm:v1" in script:
            return self._confirm(keys, args)
        if "shajra:coordination-inspect:v1" in script:
            return self._admin_inspect(keys, args)
        if "shajra:coordination-admin:v1" in script:
            return self._admin_transition(keys, args)
        if "shajra:revocation-revoke:v1" in script:
            return self._revoke(keys, args)
        if "shajra:revocation-check:v1" in script:
            return self._is_revoked(keys, args)
        if "shajra:rate-time:v1" in script:
            if keys or args:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            return ["OK", "TIME", str(self.now_ms)]
        if "shajra:rate-consume:v1" in script:
            return self._rate_consume(keys, args)
        raise AssertionError("unexpected script")

    def _receipt_replay(
        self, receipt_key: str, input_sha256: str, replay_code: str
    ) -> list[object] | None:
        raw = self.receipts.get(receipt_key)
        if raw is None:
            return None
        try:
            stored_input = json.loads(raw)["input_sha256"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        if stored_input != input_sha256:
            return ["ERR", "NONCE_REUSE_CONFLICT"]
        return ["OK", replay_code, raw]

    def _acquire(
        self, domain: str, script_keys: list[str], args: list[str]
    ) -> list[object]:
        expected_key_count = 2 if domain == "GENERIC" else 6
        expected_arg_counts = {5} if domain == "GENERIC" else {5, 10}
        if (
            len(script_keys) != expected_key_count
            or len(args) not in expected_arg_counts
        ):
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        request = CanonicalInput(args[0], args[1])
        scope, acquisition_id, ttl_ms = args[2], args[3], int(args[4])
        if domain == "GENERIC":
            lock_key, receipt_key = script_keys
            confirmed_key = fence_key = reservation_key = proof_key = None
        else:
            (
                lock_key,
                fence_key,
                confirmed_key,
                reservation_key,
                proof_key,
                receipt_key,
            ) = script_keys

        replay = self._receipt_replay(receipt_key, request.sha256, "LEASE_REPLAYED")
        if replay is not None:
            return replay
        if ttl_ms < 1 or ttl_ms > 300_000:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        if domain == "GRAPH_COMMIT":
            assert proof_key is not None
            core_keys = script_keys[:5]
            if len(args) == 5:
                status = self._status(core_keys, [scope])
                return ["OK", "GRAPH_PREFLIGHT", *status[2:]]
            normalized_expected = tuple(
                None if value == "__SHAJRA_MISSING_V1__" else value
                for value in args[5:]
            )
            if self._core_raw(core_keys) != normalized_expected:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            confirmed = self.scalars.get(confirmed_key)
            fence = self.scalars.get(fence_key)
            proof = self.proofs.get(proof_key)
            if confirmed is None or fence is None or proof is None:
                return ["ERR", "COORDINATION_UNINITIALIZED"]
            if self.reservations.intersection({reservation_key}):
                try:
                    deserialize_commit_reservation(
                        self.reservation_values[reservation_key], self.keys, scope
                    )
                except (CoordinationError, KeyError):
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
                return ["ERR", "COMMIT_RECOVERY_REQUIRED"]
            committed_revision = json.loads(request.text)["committed_revision"]
            if confirmed != committed_revision:
                return ["ERR", "COORDINATION_REVISION_MISMATCH"]
        current_lock = self.leases.get(lock_key)
        if current_lock is not None:
            try:
                if domain == "GENERIC":
                    if type(current_lock) is not Lease or current_lock.scope != scope:
                        return ["ERR", "COORDINATION_STATE_CORRUPT"]
                    serialize_generic_lock(current_lock, self.keys)
                else:
                    if (
                        type(current_lock) is not GraphLease
                        or current_lock.scope != scope
                        or current_lock.base_revision
                        != int(self.scalars[confirmed_key])
                        or current_lock.fencing_token != int(self.scalars[fence_key])
                    ):
                        return ["ERR", "COORDINATION_STATE_CORRUPT"]
                    serialize_graph_lock(current_lock, self.keys)
            except (CoordinationError, KeyError, ValueError):
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            return ["ERR", "LOCK_UNAVAILABLE"]

        applied_ttl_ms = ttl_ms - self.applied_pttl_loss_ms
        if applied_ttl_ms < 1 or applied_ttl_ms > ttl_ms:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        expires = self.now_ms + applied_ttl_ms
        if domain == "GENERIC":
            lease: Lease | GraphLease = Lease(
                scope, acquisition_id, expires, applied_ttl_ms, expires - 5_000
            )
        else:
            assert fence_key is not None
            current_fence = int(self.scalars[fence_key])
            if current_fence >= 2**63 - 1:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            fencing_token = current_fence + 1
            self.scalars[fence_key] = str(fencing_token)
            lease = GraphLease(
                scope,
                acquisition_id,
                fencing_token,
                int(json.loads(request.text)["committed_revision"]),
                expires,
                applied_ttl_ms,
                expires - 5_000,
            )
        receipt_expiry = self.now_ms + 60_000
        raw = serialize_lease_acquisition_receipt(request, lease, receipt_expiry)
        self.leases[lock_key] = lease
        self.lease_expiry[lock_key] = expires
        self.receipts[receipt_key] = raw
        self.receipt_expiry[receipt_key] = receipt_expiry
        return ["OK", "LEASE_ACQUIRED", raw]

    def _renew(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 2 or len(args) != 6:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        lock_key, receipt_key = script_keys
        request = CanonicalInput(args[0], args[1])
        replay = self._receipt_replay(
            receipt_key, request.sha256, "LEASE_RENEW_REPLAYED"
        )
        if replay is not None:
            return replay
        scope, acquisition_id, ttl_ms = args[2], args[3], int(args[4])
        current = self.leases.get(lock_key)
        if (
            current is None
            or current.scope != scope
            or current.acquisition_id != acquisition_id
        ):
            return ["ERR", "LEASE_LOST"]
        expected_lock = (
            serialize_graph_lock(current, self.keys)
            if isinstance(current, GraphLease)
            else serialize_generic_lock(current, self.keys)
        )
        if args[5] != expected_lock:
            return ["ERR", "LEASE_LOST"]
        if ttl_ms < 1 or ttl_ms > 300_000:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        applied_ttl_ms = ttl_ms - self.applied_pttl_loss_ms
        if applied_ttl_ms < 1 or applied_ttl_ms > ttl_ms:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        expires = self.now_ms + applied_ttl_ms
        renewed = replace(
            current,
            expires_at_ms=expires,
            ttl_ms=applied_ttl_ms,
            renew_deadline_ms=expires - 5_000,
        )
        receipt_expiry = self.now_ms + 60_000
        raw = serialize_lease_operation_receipt(request, renewed, receipt_expiry)
        self.leases[lock_key] = renewed
        self.lease_expiry[lock_key] = expires
        self.receipts[receipt_key] = raw
        self.receipt_expiry[receipt_key] = receipt_expiry
        return ["OK", "LEASE_RENEWED", raw]

    def _release(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 2 or len(args) != 5:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        lock_key, receipt_key = script_keys
        request = CanonicalInput(args[0], args[1])
        replay = self._receipt_replay(
            receipt_key, request.sha256, "LEASE_RELEASE_REPLAYED"
        )
        if replay is not None:
            return replay
        scope, acquisition_id = args[2], args[3]
        current = self.leases.get(lock_key)
        if (
            current is None
            or current.scope != scope
            or current.acquisition_id != acquisition_id
        ):
            return ["ERR", "LEASE_LOST"]
        expected_lock = (
            serialize_graph_lock(current, self.keys)
            if isinstance(current, GraphLease)
            else serialize_generic_lock(current, self.keys)
        )
        if args[4] != expected_lock:
            return ["ERR", "LEASE_LOST"]
        result = LeaseReleaseResult("LEASE_RELEASED", acquisition_id, self.now_ms)
        receipt_expiry = self.now_ms + 60_000
        raw = serialize_lease_operation_receipt(request, result, receipt_expiry)
        self.leases.pop(lock_key)
        self.lease_expiry.pop(lock_key)
        self.receipts[receipt_key] = raw
        self.receipt_expiry[receipt_key] = receipt_expiry
        return ["OK", "LEASE_RELEASED", raw]

    def _assert_owned(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 1:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        current = self.leases.get(script_keys[0])
        if (
            current is None
            or current.scope != args[0]
            or current.acquisition_id != args[1]
        ):
            return ["ERR", "LEASE_LOST"]
        expected_lock = (
            serialize_graph_lock(current, self.keys)
            if isinstance(current, GraphLease)
            else serialize_generic_lock(current, self.keys)
        )
        if len(args) != 3 or args[2] != expected_lock:
            return ["ERR", "LEASE_LOST"]
        return ["OK", "LEASE_OWNED"]

    def _authorize(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 5 or len(args) not in {3, 8}:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        lock_key, fence_key, confirmed_key, reservation_key, proof_key = script_keys
        scope, proposed_raw, expected_lock = args[:3]
        current_raw = self.reservation_values.get(reservation_key)
        if current_raw is not None:
            try:
                deserialize_commit_reservation(current_raw, self.keys, scope)
            except CoordinationError:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            if current_raw == proposed_raw:
                return ["OK", "RESERVATION_REPLAYED", current_raw]
            return ["ERR", "RESERVATION_CONFLICT"]
        if len(args) == 3:
            status = self._status(script_keys, [scope])
            return ["OK", "AUTHORIZATION_PREFLIGHT", *status[2:]]
        normalized_expected = tuple(
            None if value == "__SHAJRA_MISSING_V1__" else value for value in args[3:]
        )
        if self._core_raw(script_keys) != normalized_expected:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        if self.scalars.get(fence_key) is None or self.proofs.get(proof_key) is None:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        current = self.leases.get(lock_key)
        if current is None or not isinstance(current, GraphLease):
            return ["ERR", "LEASE_LOST"]
        if serialize_graph_lock(current, self.keys) != expected_lock:
            return ["ERR", "LEASE_LOST"]
        proposed = deserialize_commit_reservation(proposed_raw, self.keys, scope)
        if (
            self.scalars.get(confirmed_key) != str(proposed.commit.revision - 1)
            or proposed.commit.revision != current.base_revision + 1
            or proposed.commit.fencing_token != current.fencing_token
        ):
            return ["ERR", "RESERVATION_CONFLICT"]
        self.reservations.add(reservation_key)
        self.reservation_values[reservation_key] = proposed_raw
        return ["OK", "RESERVATION_CREATED", proposed_raw]

    def _status(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 5 or len(args) != 1:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        lock_key, fence_key, confirmed_key, reservation_key, proof_key = script_keys
        lease = self.leases.get(lock_key)
        lock_raw = ""
        pttl = "-2"
        if isinstance(lease, GraphLease):
            lock_raw = serialize_graph_lock(lease, self.keys)
            pttl = str(self.lease_expiry[lock_key] - self.now_ms)
        return [
            "OK",
            "STATUS",
            self.scalars.get(confirmed_key, ""),
            self.scalars.get(fence_key, ""),
            lock_raw,
            pttl,
            self.reservation_values.get(reservation_key, ""),
            self.proofs.get(proof_key, ""),
        ]

    def _confirm(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 5 or len(args) != 14:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        _lock_key, _fence_key, confirmed_key, reservation_key, proof_key = script_keys
        (
            scope,
            operation_id,
            revision,
            fence,
            permit_id,
            commit_digest,
            commit_json,
            _nonce_hmac,
            expected_previous,
            *expected_raw,
        ) = args
        confirmed = int(self.scalars.get(confirmed_key, "-1"))
        proof_raw = self.proofs.get(proof_key)
        confirmed_proof = None
        if proof_raw:
            try:
                confirmed_proof = deserialize_confirmed_commit_receipt(
                    proof_raw, self.keys, scope
                )
            except CoordinationError:
                try:
                    deserialize_reconciled_head_receipt(proof_raw, self.keys, scope)
                except CoordinationError:
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
        if confirmed_proof is not None:
            proof = confirmed_proof
            if (
                str(proof.permit.operation_id) == operation_id
                and str(proof.permit.revision) == revision
                and str(proof.permit.fencing_token) == fence
                and proof.permit.permit_id == permit_id
                and proof.commit_sha256 == commit_digest
                and json.loads(commit_json)
                == json.loads(
                    __import__("repositories").canonical_graph_commit_json(proof.commit)
                )
            ):
                return ["OK", "CONFIRMATION_REPLAYED", proof_raw]
        current_raw = self._core_raw(script_keys)
        normalized_expected = tuple(
            None if value == "__SHAJRA_MISSING_V1__" else value
            for value in expected_raw
        )
        if current_raw != normalized_expected:
            return ["ERR", "CONFIRMATION_CONFLICT"]
        if int(revision) <= confirmed:
            return ["ERR", "CONFIRMATION_PROOF_EVICTED", str(confirmed)]
        reservation_raw = self.reservation_values.get(reservation_key)
        if reservation_raw is None:
            return ["ERR", "CONFIRMATION_CONFLICT"]
        reservation = deserialize_commit_reservation(reservation_raw, self.keys, scope)
        if (
            str(reservation.commit.operation_id) != operation_id
            or str(reservation.commit.revision) != revision
            or str(reservation.commit.fencing_token) != fence
            or reservation.commit.permit_id != permit_id
            or reservation.commit_sha256 != commit_digest
            or self.scalars[confirmed_key] != expected_previous
            or expected_previous != str(reservation.commit.revision - 1)
        ):
            return ["ERR", "CONFIRMATION_CONFLICT"]
        proof = ConfirmedCommitReceipt(
            reservation.scope,
            reservation.permit,
            reservation.commit,
            reservation.commit_sha256,
            reservation.staged_write_receipt,
        )
        proof_raw = serialize_confirmed_commit_receipt(proof, self.keys)
        self.scalars[confirmed_key] = revision
        self.reservations.discard(reservation_key)
        self.reservation_values.pop(reservation_key)
        self.proofs[proof_key] = proof_raw
        return ["OK", "CONFIRMED", proof_raw]

    def _core_raw(self, script_keys: list[str]) -> tuple[str | None, ...]:
        lock_key, fence_key, confirmed_key, reservation_key, proof_key = script_keys
        lease = self.leases.get(lock_key)
        lock_raw = (
            serialize_graph_lock(lease, self.keys)
            if isinstance(lease, GraphLease)
            else None
        )
        return (
            lock_raw,
            self.scalars.get(fence_key),
            self.scalars.get(confirmed_key),
            self.reservation_values.get(reservation_key),
            self.proofs.get(proof_key),
        )

    def _admin_inspect(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 5 or len(args) != 1:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        return [
            "OK",
            "INSPECTION",
            *(
                value if value is not None else ""
                for value in self._core_raw(script_keys)
            ),
        ]

    def _admin_transition(
        self, script_keys: list[str], args: list[str]
    ) -> list[object]:
        if len(script_keys) != 6 or len(args) != 17:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        core_keys = script_keys[:5]
        receipt_key = script_keys[5]
        (
            request_text,
            request_sha,
            evidence_raw,
            expected_state_sha,
            scope,
            request_nonce,
            proof_raw,
            proposed_state_sha,
            proposed_revision,
            proposed_floor,
            method,
            *remaining_args,
        ) = args
        expected_raw_args = remaining_args[:5]
        inspected_state_sha = remaining_args[5]
        request = CanonicalInput(request_text, request_sha)
        replay = self._receipt_replay(
            receipt_key,
            request.sha256,
            "ADMIN_INITIALIZED" if method == "initialize" else "ADMIN_RECONCILED",
        )
        if replay is not None:
            return replay
        if inspected_state_sha != expected_state_sha:
            return ["ERR", "ADMIN_STATE_CHANGED"]
        current_raw = self._core_raw(core_keys)
        expected_raw = tuple(
            None if value == "__SHAJRA_MISSING_V1__" else value
            for value in expected_raw_args
        )
        if expected_raw != current_raw:
            return ["ERR", "ADMIN_STATE_CHANGED"]
        if coordination_state_sha256(current_raw) != expected_state_sha:
            return ["ERR", "ADMIN_STATE_CHANGED"]
        if current_raw[0] is not None or current_raw[3] is not None:
            return ["ERR", "ADMIN_BUSY"]
        if method == "initialize" and any(value is not None for value in current_raw):
            return ["ERR", "ADMIN_STATE_CHANGED"]
        if method == "reconcile" and not any(
            value is not None for value in current_raw
        ):
            return ["ERR", "ADMIN_STATE_CHANGED"]
        if method not in {"initialize", "reconcile"}:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        evidence = deserialize_coordination_evidence(evidence_raw)
        if evidence.scope != scope:
            return ["ERR", "ADMIN_EVIDENCE_INVALID"]
        valid_floor = (
            int(current_raw[1]) if current_raw[1] and current_raw[1].isdigit() else 0
        )
        valid_revision = (
            int(current_raw[2]) if current_raw[2] and current_raw[2].isdigit() else 0
        )
        if (
            evidence.committed_head_revision < valid_revision
            or evidence.fencing_floor < valid_floor
            or proposed_revision != str(evidence.committed_head_revision)
            or proposed_floor != str(evidence.fencing_floor)
        ):
            return ["ERR", "ADMIN_EVIDENCE_INVALID"]
        proof = __import__(
            "coordination.serialization",
            fromlist=["deserialize_reconciled_head_receipt"],
        ).deserialize_reconciled_head_receipt(proof_raw, self.keys, scope)
        if (
            proof.revision != evidence.committed_head_revision
            or proof.evidence_sha256 != evidence.evidence_sha256
            or proof.admin_request_nonce_hmac
            != self.keys.admin_nonce_hmac(request_nonce)
        ):
            return ["ERR", "ADMIN_EVIDENCE_INVALID"]
        _lock_key, fence_key, confirmed_key, _reservation_key, proof_key = core_keys
        self.scalars[fence_key] = proposed_floor
        self.scalars[confirmed_key] = proposed_revision
        self.proofs[proof_key] = proof_raw
        post_raw = self._core_raw(core_keys)
        actual_state_sha = coordination_state_sha256(post_raw)
        if actual_state_sha != proposed_state_sha:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        code = "ADMIN_INITIALIZED" if method == "initialize" else "ADMIN_RECONCILED"
        result = CoordinationAdminResult(
            code,
            expected_state_sha,
            actual_state_sha,
            int(proposed_revision),
            int(proposed_floor),
        )
        receipt_expiry = self.now_ms + 60_000
        raw = serialize_admin_result_receipt(
            request,
            method,
            self.keys,
            scope,
            request_nonce,
            evidence.evidence_sha256,
            expected_state_sha,
            result,
            receipt_expiry,
        )
        self.receipts[receipt_key] = raw
        self.receipt_expiry[receipt_key] = receipt_expiry
        return ["OK", code, raw]

    def _revoke(self, script_keys: list[str], args: list[str]) -> list[object]:
        from coordination.protocols import RevocationResult
        from coordination.serialization import (
            deserialize_revocation_entry,
            deserialize_revocation_receipt,
            serialize_revocation_entry,
            serialize_revocation_receipt,
        )

        if len(script_keys) != 2 or len(args) != 6:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        entry_key, nonce_key = script_keys
        request = CanonicalInput(args[0], args[1])
        replay = self._receipt_replay(nonce_key, request.sha256, "REVOKED")
        if replay is not None:
            if replay[0] == "OK":
                raw = replay[2]
                try:
                    receipt = deserialize_revocation_receipt(raw, request)
                except CoordinationError:
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
                stored_expiry = receipt.receipt_expires_at_ms
                ttl_expiry = self.receipt_expiry.get(nonce_key)
                if ttl_expiry is not None and ttl_expiry > stored_expiry:
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
                self.receipt_expiry[nonce_key] = stored_expiry
                replay[1] = receipt.result.code
            return replay
        jti, token_expiry_s, leeway_s = args[2], int(args[3]), int(args[4])
        expires_at_ms = token_expiry_s * 1_000 + leeway_s * 1_000
        jti_hmac = self.keys.revocation_jti_hmac(jti)
        code = "REVOKED"
        revoked = True
        if self.now_ms >= expires_at_ms:
            code = "TOKEN_ALREADY_EXPIRED"
            revoked = False
        elif entry_key in self.revocations:
            try:
                entry = deserialize_revocation_entry(self.revocations[entry_key])
            except CoordinationError:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            if entry.jti_hmac != jti_hmac or entry.expires_at_ms != expires_at_ms:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            current_expiry = self.revocation_expiry.get(entry_key)
            if current_expiry is not None and current_expiry > expires_at_ms:
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            self.revocation_expiry[entry_key] = expires_at_ms
            code = "ALREADY_REVOKED"
        else:
            if args[5] != serialize_revocation_entry(jti_hmac, expires_at_ms):
                return ["ERR", "COORDINATION_STATE_CORRUPT"]
            self.revocations[entry_key] = args[5]
            self.revocation_expiry[entry_key] = expires_at_ms
        result = RevocationResult(code, revoked, self.now_ms, expires_at_ms)
        receipt_expiry = max(expires_at_ms, self.now_ms + 60_000)
        raw = serialize_revocation_receipt(request, result, jti_hmac, receipt_expiry)
        self.receipts[nonce_key] = raw
        self.receipt_expiry[nonce_key] = receipt_expiry
        return ["OK", code, raw]

    def _is_revoked(self, script_keys: list[str], args: list[str]) -> list[object]:
        if len(script_keys) != 1 or len(args) != 4:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        entry_key = script_keys[0]
        expected_entry, token_expiry_s, leeway_s, _jti_hmac = (
            args[0],
            int(args[1]),
            int(args[2]),
            args[3],
        )
        expires_at_ms = token_expiry_s * 1_000 + leeway_s * 1_000
        if self.now_ms >= expires_at_ms:
            return [
                "OK",
                "TOKEN_ALREADY_EXPIRED",
                "false",
                str(self.now_ms),
                str(expires_at_ms),
            ]
        raw = self.revocations.get(entry_key)
        if raw is None:
            return ["OK", "NOT_REVOKED", "false", str(self.now_ms), str(expires_at_ms)]
        if raw != expected_entry:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        current_expiry = self.revocation_expiry.get(entry_key)
        if current_expiry is not None and current_expiry > expires_at_ms:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        self.revocation_expiry[entry_key] = expires_at_ms
        return ["OK", "REVOKED", "true", str(self.now_ms), str(expires_at_ms)]

    def _rate_consume(self, script_keys: list[str], args: list[str]) -> list[object]:
        from coordination.protocols import RateLimitPolicyId, RateLimitResult
        from coordination.serialization import (
            deserialize_rate_receipt,
            serialize_rate_receipt,
        )

        if len(script_keys) != 2 or len(args) != 7:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        counter_key, nonce_key = script_keys
        request = CanonicalInput(args[0], args[1])
        replay = self._receipt_replay(nonce_key, request.sha256, "RATE_LIMIT_ALLOWED")
        if replay is not None:
            if replay[0] == "OK":
                raw = replay[2]
                try:
                    receipt = deserialize_rate_receipt(raw, request)
                except CoordinationError:
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
                stored_expiry = receipt.receipt_expires_at_ms
                ttl_expiry = self.receipt_expiry.get(nonce_key)
                if ttl_expiry is not None and ttl_expiry > stored_expiry:
                    return ["ERR", "COORDINATION_STATE_CORRUPT"]
                self.receipt_expiry[nonce_key] = stored_expiry
                replay[1] = (
                    "RATE_LIMIT_ALLOWED"
                    if receipt.result.allowed
                    else "RATE_LIMIT_DENIED"
                )
            return replay
        policy = RateLimitPolicyId(args[2])
        window_start, window_ms, limit = int(args[4]), int(args[5]), int(args[6])
        actual_start = (self.now_ms // window_ms) * window_ms
        if actual_start != window_start:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        reset = window_start + window_ms
        raw_count = self.rate_counters.get(counter_key)
        if raw_count is None:
            count = 0
        elif (
            not raw_count.isdigit()
            or raw_count.startswith("0")
            or int(raw_count) > 2**63 - 1
        ):
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        else:
            count = int(raw_count)
        if count == 2**63 - 1:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        current_expiry = self.rate_counter_expiry.get(counter_key)
        if current_expiry is not None and current_expiry > reset:
            return ["ERR", "COORDINATION_STATE_CORRUPT"]
        count += 1
        self.rate_counters[counter_key] = str(count)
        self.rate_counter_expiry[counter_key] = reset
        allowed = count <= limit
        result = RateLimitResult(
            policy,
            allowed,
            limit,
            count,
            max(limit - count, 0),
            self.now_ms,
            reset,
            0 if allowed else reset - self.now_ms,
        )
        receipt_expiry = reset + 60_000
        raw = serialize_rate_receipt(request, result, receipt_expiry)
        self.receipts[nonce_key] = raw
        self.receipt_expiry[nonce_key] = receipt_expiry
        return [
            "OK",
            "RATE_LIMIT_ALLOWED" if allowed else "RATE_LIMIT_DENIED",
            raw,
        ]


def _managers(now_ms: int = 100_000):
    from coordination.upstash import UpstashCommitCoordinator, UpstashLeaseManager

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms)
    return (
        keys,
        redis,
        UpstashLeaseManager(redis, keys),
        UpstashCommitCoordinator(redis, keys),
    )


@pytest.mark.parametrize(
    "script_name",
    (
        "GENERIC_ACQUIRE_LUA",
        "GRAPH_ACQUIRE_LUA",
        "LEASE_RENEW_LUA",
        "LEASE_RELEASE_LUA",
        "LEASE_ASSERT_LUA",
        "AUTHORIZE_COMMIT_LUA",
        "COORDINATION_STATUS_LUA",
        "CONFIRM_COMMIT_LUA",
        "COORDINATION_INSPECT_LUA",
        "COORDINATION_ADMIN_LUA",
        "REVOCATION_REVOKE_LUA",
        "REVOCATION_CHECK_LUA",
        "RATE_TIME_LUA",
        "RATE_CONSUME_LUA",
    ),
)
def test_every_lua_entry_point_rejects_wrong_key_argument_shape_before_mutation(
    script_name,
):
    upstash = __import__("coordination.upstash", fromlist=[script_name])
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    script = getattr(upstash, script_name)
    invalid_keys = ["unexpected"] if script_name == "RATE_TIME_LUA" else []

    result = redis.eval(script, invalid_keys, [], nonce_idempotent=False)

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert not redis.leases
    assert not redis.receipts
    assert not redis.scalars


def test_runtime_graph_acquire_requires_initialized_actual_head_and_never_mutates_absent_state():
    keys, redis, _generic, graph = _managers()
    before = (dict(redis.scalars), dict(redis.leases), dict(redis.receipts))

    with pytest.raises(CoordinationError) as raised:
        graph.acquire("family", 0, "acq-1")

    assert raised.value.code == "COORDINATION_UNINITIALIZED"
    assert (redis.scalars, redis.leases, redis.receipts) == before
    assert "if-match" not in str(redis.calls[-1][2]).lower()
    assert keys.graph_fence("family") not in redis.scalars


def test_graph_acquire_uses_actual_initialized_head_and_checks_contention_before_incr():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=4, fencing_floor=8)

    with pytest.raises(CoordinationError) as mismatch:
        graph.acquire("family", 3, "acq-mismatch")
    assert mismatch.value.code == "COORDINATION_REVISION_MISMATCH"
    assert redis.scalars[keys.graph_fence("family")] == "8"

    lease = graph.acquire("family", 4, "acq-1")
    assert lease.fencing_token == 9
    with pytest.raises(CoordinationError) as contention:
        graph.acquire("family", 4, "acq-2")
    assert contention.value.code == "LOCK_UNAVAILABLE"
    assert redis.scalars[keys.graph_fence("family")] == "9"


@pytest.mark.parametrize("domain", ("generic", "graph"))
def test_acquire_rejects_malformed_contending_lock_without_mutation(domain):
    keys, redis, generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    if domain == "generic":
        lease = generic.acquire("scope", "owner")
        lock_key = keys.generic_lock("scope")
        redis.leases[lock_key] = replace(lease, ttl_ms=0)
        manager_call = lambda: generic.acquire("scope", "contender")
    else:
        lease = graph.acquire("family", 0, "owner")
        lock_key = keys.graph_lock("family")
        redis.leases[lock_key] = replace(lease, base_revision=1)
        manager_call = lambda: graph.acquire("family", 0, "contender")
    fence_before = redis.scalars[keys.graph_fence("family")]

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        manager_call()

    assert redis.scalars[keys.graph_fence("family")] == fence_before
    assert redis.leases[lock_key].acquisition_id == "owner"


def test_graph_acquire_rejects_unsafe_ttl_before_incrementing_fence():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=8)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        graph.acquire("family", 0, "acq", ttl_ms=300_001)

    assert redis.scalars[keys.graph_fence("family")] == "8"
    assert keys.graph_lock("family") not in redis.leases


def test_graph_acquire_rejects_signed_64_fence_overflow_without_mutation():
    keys, redis, _generic, graph = _managers()
    maximum = 2**63 - 1
    redis.initialize_graph("family", revision=0, fencing_floor=maximum)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        graph.acquire("family", 0, "acq")

    assert redis.scalars[keys.graph_fence("family")] == str(maximum)
    assert keys.graph_lock("family") not in redis.leases


def test_graph_acquire_rejects_malformed_reservation_without_incrementing_fence():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=8)
    reservation_key = keys.graph_reservation("family")
    redis.reservations.add(reservation_key)
    redis.reservation_values[reservation_key] = '{"broken":true}'

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        graph.acquire("family", 0, "acq")

    assert redis.scalars[keys.graph_fence("family")] == "8"
    assert keys.graph_lock("family") not in redis.leases


def test_acquire_receipt_replays_original_timing_and_fence_after_expiry_and_new_owner():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=4, fencing_floor=8)
    original = graph.acquire("family", 4, "acq-original")
    original_receipt_key = keys.graph_acquisition_result("family", "acq-original")
    original_raw = redis.receipts[original_receipt_key]

    redis.advance(16_000)
    newer = graph.acquire("family", 4, "acq-new")
    fence_after_new = redis.scalars[keys.graph_fence("family")]
    replayed = graph.acquire("family", 4, "acq-original")

    assert newer.fencing_token == original.fencing_token + 1
    assert replayed == original
    assert redis.scalars[keys.graph_fence("family")] == fence_after_new
    assert redis.receipts[original_receipt_key] == original_raw
    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        graph.assert_owned(replayed)
    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        graph.renew(replayed, "renew-old")


def test_acquisition_nonce_changed_ttl_or_graph_revision_conflicts_before_lock_state():
    _keys, redis, generic, graph = _managers()
    redis.initialize_graph("family", revision=4, fencing_floor=8)
    generic.acquire("search", "generic-acq")
    graph.acquire("family", 4, "graph-acq")
    redis.advance(16_000)

    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        generic.acquire("search", "generic-acq", ttl_ms=14_999)
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        graph.acquire("family", 5, "graph-acq")


def test_generic_and_graph_domains_are_distinct_and_use_redis_timing_contract():
    keys, redis, generic, graph = _managers(now_ms=1_000_000)
    redis.initialize_graph("scope", revision=0, fencing_floor=3)

    generic_lease = generic.acquire("scope", "same-acq")
    graph_lease = graph.acquire("scope", 0, "same-acq")

    assert type(generic_lease) is Lease
    assert type(graph_lease) is GraphLease
    assert generic_lease.ttl_ms == graph_lease.ttl_ms == 15_000
    assert generic_lease.expires_at_ms == graph_lease.expires_at_ms == 1_015_000
    assert generic_lease.renew_deadline_ms == graph_lease.renew_deadline_ms == 1_010_000
    assert keys.generic_lock("scope") in redis.leases
    assert keys.graph_lock("scope") in redis.leases


@pytest.mark.parametrize("domain", ("generic", "graph"))
def test_acquire_and_renew_report_applied_redis_pttl(domain):
    _keys, redis, generic, graph = _managers(now_ms=1_000_000)
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    manager = generic if domain == "generic" else graph
    redis.applied_pttl_loss_ms = 7

    lease = (
        manager.acquire("scope", "acq")
        if domain == "generic"
        else manager.acquire("family", 0, "acq")
    )

    assert lease.ttl_ms == 14_993
    assert lease.expires_at_ms == 1_014_993
    assert lease.renew_deadline_ms == 1_009_993

    redis.advance(100)
    redis.applied_pttl_loss_ms = 11
    renewed = manager.renew(lease, "renew")

    assert renewed.ttl_ms == 14_989
    assert renewed.expires_at_ms == 1_015_089
    assert renewed.renew_deadline_ms == 1_010_089


@pytest.mark.parametrize("domain", ("generic", "graph"))
def test_renew_receipt_replays_original_result_without_extending_twice(domain):
    keys, redis, generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    manager = generic if domain == "generic" else graph
    lease = (
        manager.acquire("scope", "acq")
        if domain == "generic"
        else manager.acquire("family", 0, "acq")
    )
    redis.advance(1_000)
    renewed = manager.renew(lease, "renew-nonce")
    lock_key = (
        keys.generic_lock("scope") if domain == "generic" else keys.graph_lock("family")
    )
    first_expiry = redis.lease_expiry[lock_key]
    redis.advance(2_000)

    replayed = manager.renew(lease, "renew-nonce")

    assert replayed == renewed
    assert redis.lease_expiry[lock_key] == first_expiry
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        manager.renew(lease, "renew-nonce", ttl_ms=14_999)


def test_operation_nonce_conflicts_when_method_or_complete_lease_changes():
    _keys, _redis, generic, _graph = _managers()
    lease = generic.acquire("scope", "acq")
    generic.renew(lease, "operation-nonce")

    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        generic.release(lease, "operation-nonce")
    changed = replace(
        lease,
        expires_at_ms=lease.expires_at_ms + 1,
        renew_deadline_ms=lease.renew_deadline_ms + 1,
    )
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        generic.renew(changed, "operation-nonce")


@pytest.mark.parametrize("domain", ("generic", "graph"))
def test_release_receipt_replays_original_result_without_deleting_new_owner(domain):
    keys, redis, generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    manager = generic if domain == "generic" else graph
    scope = "scope" if domain == "generic" else "family"
    lease = manager.acquire(scope, *(("acq",) if domain == "generic" else (0, "acq")))
    released = manager.release(lease, "release-nonce")
    newer = manager.acquire(
        scope, *(("new-acq",) if domain == "generic" else (0, "new-acq"))
    )
    replayed = manager.release(lease, "release-nonce")
    lock_key = (
        keys.generic_lock(scope) if domain == "generic" else keys.graph_lock(scope)
    )

    assert released.code == "LEASE_RELEASED"
    assert replayed.code == "LEASE_RELEASE_REPLAYED"
    assert replayed.released_at_ms == released.released_at_ms
    assert redis.leases[lock_key] == newer


def test_fresh_acquisition_ids_are_random_uuid4_values_and_not_intentionally_reused():
    from coordination.upstash import new_acquisition_id

    values = {new_acquisition_id() for _ in range(20)}

    assert len(values) == 20
    assert all(len(value) == 36 and value.count("-") == 4 for value in values)


def _commit_values(lease: GraphLease, revision: int, suffix: str = "1"):
    from domain.ids import OperationId
    from repositories import (
        GraphCommit,
        GraphWriteSet,
        StagedWriteReceipt,
        canonical_graph_write_set_json,
    )
    from repositories.protocols import graph_write_set_sha256

    operation_id = OperationId(f"op_{suffix}")
    write_set = GraphWriteSet()
    staged = StagedWriteReceipt(
        operation_id,
        revision,
        lease.fencing_token,
        canonical_graph_write_set_json(write_set),
        graph_write_set_sha256(write_set),
    )
    commit = GraphCommit(
        operation_id,
        revision,
        lease.fencing_token,
        f"cpr_{suffix}",
        (suffix[-1] if suffix[-1] in "abcdef" else "d") * 64,
        datetime(2026, 8, 7, 10, 0, revision, tzinfo=UTC),
    )
    return commit, staged


def test_authorization_persists_complete_immutable_recovery_data_and_replays_after_expiry():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    lease = graph.acquire("family", 0, "acq")
    commit, staged = _commit_values(lease, 1)

    permit = graph.authorize_commit(lease, commit, staged, "authorize-nonce")
    reservation_key = keys.graph_reservation("family")
    raw = redis.reservation_values[reservation_key]
    persisted = deserialize_commit_reservation(raw, keys, "family")
    redis.advance(16_000)
    replayed = graph.authorize_commit(lease, commit, staged, "authorize-nonce")

    assert replayed == permit == persisted.permit
    assert persisted.commit == commit
    assert persisted.staged_write_receipt == staged
    assert reservation_key in redis.reservations
    with pytest.raises(CoordinationError, match="COMMIT_RECOVERY_REQUIRED"):
        graph.acquire("family", 0, "new-acq")


def test_expired_original_lease_cannot_authorize_after_a_fresh_acquisition():
    _keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    original = graph.acquire("family", 0, "old")
    commit, staged = _commit_values(original, 1)
    redis.advance(16_000)
    graph.acquire("family", 0, "new")

    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        graph.authorize_commit(original, commit, staged, "authorize-old")


def test_authorization_rejects_generic_wrong_lease_identity_and_changed_staged_content():
    from domain.ids import PersonId
    from repositories import (
        GraphWriteSet,
        StagedWriteReceipt,
        canonical_graph_write_set_json,
    )
    from repositories.protocols import graph_write_set_sha256

    _keys, redis, generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    generic_lease = generic.acquire("family", "generic")
    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        graph.authorize_commit(generic_lease, None, None, "nonce")  # type: ignore[arg-type]

    lease = graph.acquire("family", 0, "graph")
    commit, staged = _commit_values(lease, 1)
    wrong_lease = replace(lease, fencing_token=lease.fencing_token + 1)
    with pytest.raises(CoordinationError):
        graph.authorize_commit(wrong_lease, commit, staged, "wrong")

    graph.authorize_commit(lease, commit, staged, "authorize")
    changed_set = GraphWriteSet(person_tombstones=(PersonId("per_changed"),))
    changed_staged = StagedWriteReceipt(
        staged.operation_id,
        staged.revision,
        staged.fencing_token,
        canonical_graph_write_set_json(changed_set),
        graph_write_set_sha256(changed_set),
    )
    with pytest.raises(CoordinationError, match="RESERVATION_CONFLICT"):
        graph.authorize_commit(lease, commit, changed_staged, "changed")


def test_authorization_rejects_malformed_retained_reservation_without_mutation():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    lease = graph.acquire("family", 0, "acq")
    commit, staged = _commit_values(lease, 1)
    graph.authorize_commit(lease, commit, staged, "authorize")
    reservation_key = keys.graph_reservation("family")
    malformed = json.loads(redis.reservation_values[reservation_key])
    malformed["extra"] = True
    malformed_raw = json.dumps(malformed, sort_keys=True, separators=(",", ":"))
    redis.reservation_values[reservation_key] = malformed_raw

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        graph.authorize_commit(lease, commit, staged, "authorize")

    assert redis.reservation_values[reservation_key] == malformed_raw


def test_get_status_uses_one_coherent_snapshot_and_validates_ready_and_committing():
    from coordination.serialization import coordination_state_sha256

    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    calls_before = len(redis.calls)
    ready = graph.get_status("family")

    assert len(redis.calls) == calls_before + 1
    assert ready.mode == "READY"
    assert ready.confirmed_revision == 0
    assert ready.fencing_floor == 1
    assert ready.active_reservation is None
    assert isinstance(ready.last_confirmation_proof, ReconciledHeadReceipt)
    expected_raw = (
        None,
        "1",
        "0",
        None,
        redis.proofs[keys.graph_last_confirmation("family")],
    )
    assert ready.state_sha256 == coordination_state_sha256(expected_raw)

    lease = graph.acquire("family", 0, "acq")
    commit, staged = _commit_values(lease, 1)
    graph.authorize_commit(lease, commit, staged, "authorize")
    committing = graph.get_status("family")

    assert committing.mode == "COMMITTING"
    assert committing.active_reservation is not None
    assert committing.active_reservation.commit == commit


@pytest.mark.parametrize(
    "corruption",
    (
        "missing-fence",
        "bad-proof",
        "reservation-gap",
        "orphan-proof",
        "proof-fence-gap",
        "lock-fence-gap",
        "reservation-fence-ahead",
        "lock-revision-gap",
    ),
)
def test_get_status_maps_partial_malformed_or_invariant_breaking_state_to_corrupt(
    corruption,
):
    keys, redis, _generic, graph = _managers()
    if corruption == "orphan-proof":
        redis.proofs[keys.graph_last_confirmation("family")] = "not-json"
    else:
        redis.initialize_graph("family", revision=0, fencing_floor=1)
    if corruption == "missing-fence":
        redis.scalars.pop(keys.graph_fence("family"))
    elif corruption == "bad-proof":
        redis.proofs[keys.graph_last_confirmation("family")] = '{"bad":true}'
    elif corruption == "reservation-gap":
        lease = graph.acquire("family", 0, "acq")
        commit, staged = _commit_values(lease, 1)
        graph.authorize_commit(lease, commit, staged, "authorize")
        redis.scalars[keys.graph_confirmed_revision("family")] = "4"
    elif corruption == "proof-fence-gap":
        lease = graph.acquire("family", 0, "acq")
        commit, staged = _commit_values(lease, 1)
        permit = graph.authorize_commit(lease, commit, staged, "authorize")
        graph.confirm_commit(permit, commit, "confirm")
        redis.advance(16_000)
        redis.scalars[keys.graph_fence("family")] = "1"
    elif corruption == "lock-fence-gap":
        lease = graph.acquire("family", 0, "acq")
        redis.scalars[keys.graph_fence("family")] = str(lease.fencing_token + 1)
    elif corruption == "reservation-fence-ahead":
        lease = graph.acquire("family", 0, "acq")
        commit, staged = _commit_values(lease, 1)
        graph.authorize_commit(lease, commit, staged, "authorize")
        redis.advance(16_000)
        redis.scalars[keys.graph_fence("family")] = str(lease.fencing_token - 1)
    elif corruption == "lock-revision-gap":
        lease = graph.acquire("family", 0, "acq")
        redis.initialize_graph("family", revision=1, fencing_floor=lease.fencing_token)

    before = (
        dict(redis.scalars),
        dict(redis.leases),
        dict(redis.reservation_values),
        dict(redis.proofs),
    )
    with pytest.raises(CoordinationError) as raised:
        graph.get_status("family")

    assert raised.value.code == "COORDINATION_STATE_CORRUPT"
    assert (
        redis.scalars,
        redis.leases,
        redis.reservation_values,
        redis.proofs,
    ) == before


def test_confirmation_advances_once_replays_before_new_reservation_and_reports_eviction():
    _keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    first_lease = graph.acquire("family", 0, "acq-1")
    first_commit, first_staged = _commit_values(first_lease, 1, "1")
    first_permit = graph.authorize_commit(
        first_lease, first_commit, first_staged, "authorize-1"
    )

    confirmed = graph.confirm_commit(first_permit, first_commit, "confirm-1")
    replayed = graph.confirm_commit(first_permit, first_commit, "confirm-1")
    assert confirmed.code == "CONFIRMED"
    assert replayed.code == "CONFIRMATION_REPLAYED"
    assert replayed.requested_permit == first_permit
    assert replayed.confirmed_revision == 1

    redis.advance(16_000)
    second_lease = graph.acquire("family", 1, "acq-2")
    second_commit, second_staged = _commit_values(second_lease, 2, "2")
    second_permit = graph.authorize_commit(
        second_lease, second_commit, second_staged, "authorize-2"
    )
    replay_with_new_reservation = graph.confirm_commit(
        first_permit, first_commit, "confirm-1"
    )
    assert replay_with_new_reservation.code == "CONFIRMATION_REPLAYED"

    graph.confirm_commit(second_permit, second_commit, "confirm-2")
    evicted = graph.confirm_commit(first_permit, first_commit, "confirm-1")
    assert evicted.code == "CONFIRMATION_PROOF_EVICTED"
    assert evicted.confirmed_revision == 2


def test_new_confirmation_requires_exact_previous_revision_without_mutation():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=1, fencing_floor=1)
    lease = graph.acquire("family", 1, "acq")
    commit, staged = _commit_values(lease, 2)
    permit = graph.authorize_commit(lease, commit, staged, "authorize")
    redis.scalars[keys.graph_confirmed_revision("family")] = "0"
    reservation_before = dict(redis.reservation_values)

    with pytest.raises(CoordinationError, match="CONFIRMATION_CONFLICT"):
        graph.confirm_commit(permit, commit, "confirm")

    assert redis.reservation_values == reservation_before
    assert redis.scalars[keys.graph_confirmed_revision("family")] == "0"


def test_confirmation_rejects_malformed_prior_proof_without_advancing():
    keys, redis, _generic, graph = _managers()
    redis.initialize_graph("family", revision=0, fencing_floor=1)
    lease = graph.acquire("family", 0, "acq")
    commit, staged = _commit_values(lease, 1)
    permit = graph.authorize_commit(lease, commit, staged, "authorize")
    proof_key = keys.graph_last_confirmation("family")
    malformed = json.loads(redis.proofs[proof_key])
    malformed["extra"] = True
    malformed_raw = json.dumps(malformed, sort_keys=True, separators=(",", ":"))
    redis.proofs[proof_key] = malformed_raw

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        graph.confirm_commit(permit, commit, "confirm")

    assert redis.scalars[keys.graph_confirmed_revision("family")] == "0"
    assert keys.graph_reservation("family") in redis.reservation_values
    assert redis.proofs[proof_key] == malformed_raw


def _evidence(
    scope: str = "family",
    revision: int = 0,
    max_token: int = 0,
    floor: int = 1,
) -> CoordinationEvidence:
    from coordination.serialization import coordination_evidence_sha256

    semantic = "a" * 64
    head_digest = None if revision == 0 else "b" * 64
    digest = coordination_evidence_sha256(
        scope, revision, semantic, head_digest, max_token, floor
    )
    return CoordinationEvidence(
        scope,
        revision,
        semantic,
        head_digest,
        max_token,
        floor,
        digest,
    )


def _admin(redis: LeaseEvalStub, keys: RedisKeyBuilder):
    from coordination.upstash import UpstashCoordinationAdmin

    return UpstashCoordinationAdmin(redis, keys)


def test_admin_initialize_requires_exact_absent_cas_and_replays_before_state_check():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")
    evidence = _evidence()

    assert absent.mode == "UNINITIALIZED"
    result = admin.initialize(evidence, absent.state_sha256, "admin-nonce")
    state_after = admin.inspect("family")
    replayed = admin.initialize(evidence, absent.state_sha256, "admin-nonce")

    assert result.code == "ADMIN_INITIALIZED"
    assert replayed == result
    assert state_after.mode == "READY"
    assert state_after.confirmed_revision == 0
    assert state_after.fencing_floor == 1
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        admin.initialize(
            _evidence(revision=1, max_token=1, floor=2),
            absent.state_sha256,
            "admin-nonce",
        )


def test_admin_reconcile_does_not_initialize_truly_absent_state():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")

    with pytest.raises(CoordinationError, match="ADMIN_STATE_CHANGED"):
        admin.reconcile(_evidence(), absent.state_sha256, "reconcile-absent")

    assert admin.inspect("family").mode == "UNINITIALIZED"


def test_admin_transition_binds_expected_digest_to_inspected_snapshot():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")
    admin.initialize(_evidence(), absent.state_sha256, "initialize")
    current = admin.inspect("family")
    redis.calls.clear()

    with pytest.raises(CoordinationError, match="ADMIN_STATE_CHANGED"):
        admin.reconcile(_evidence(), "d" * 64, "stale")

    assert len(redis.calls) == 2
    assert "shajra:coordination-inspect:v1" in redis.calls[0][0]
    assert "shajra:coordination-admin:v1" in redis.calls[1][0]
    assert redis.calls[1][2][-1] == current.state_sha256


def test_admin_replay_after_receipt_expiry_cannot_mutate_with_stale_expected_digest():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")
    evidence = _evidence()
    result = admin.initialize(evidence, absent.state_sha256, "admin-nonce")
    redis.advance(60_001)

    with pytest.raises(CoordinationError, match="ADMIN_STATE_CHANGED"):
        admin.initialize(evidence, absent.state_sha256, "admin-nonce")

    assert admin.inspect("family").state_sha256 == result.state_sha256


def test_admin_reconcile_requires_idle_state_and_never_decreases_revision_or_fence():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    initial = admin.inspect("family")
    admin.initialize(
        _evidence(revision=1, max_token=2, floor=3), initial.state_sha256, "init"
    )
    lease_manager = __import__(
        "coordination.upstash", fromlist=["UpstashCommitCoordinator"]
    ).UpstashCommitCoordinator(redis, keys)
    lease_manager.acquire("family", 1, "active")

    with pytest.raises(CoordinationError, match="ADMIN_BUSY"):
        admin.reconcile(
            _evidence(revision=2, max_token=3, floor=4),
            admin.inspect("family").state_sha256,
            "busy",
        )
    redis.advance(16_000)
    idle = admin.inspect("family")
    with pytest.raises(CoordinationError, match="ADMIN_EVIDENCE_INVALID"):
        admin.reconcile(
            _evidence(revision=0, max_token=0, floor=1), idle.state_sha256, "lower"
        )

    advanced = admin.reconcile(
        _evidence(revision=2, max_token=3, floor=4), idle.state_sha256, "advance"
    )
    assert advanced.confirmed_revision == 2
    assert advanced.fencing_floor == 4


def test_admin_reconcile_fresh_nonce_changes_state_digest_even_when_head_is_retained():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")
    evidence = _evidence(revision=1, max_token=1, floor=2)
    admin.initialize(evidence, absent.state_sha256, "init")
    first = admin.inspect("family")

    result = admin.reconcile(evidence, first.state_sha256, "fresh-reconcile")
    second = admin.inspect("family")

    assert result.confirmed_revision == first.confirmed_revision
    assert result.fencing_floor == first.fencing_floor
    assert second.state_sha256 != first.state_sha256
    assert isinstance(second.last_confirmation_proof, ReconciledHeadReceipt)
    assert second.last_confirmation_proof.admin_request_nonce_hmac == (
        keys.admin_nonce_hmac("fresh-reconcile")
    )


def test_admin_corrupt_repair_uses_raw_digest_and_preserves_valid_scalar_bounds():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    redis.scalars[keys.graph_confirmed_revision("family")] = "5"
    redis.scalars[keys.graph_fence("family")] = "malformed"
    redis.proofs[keys.graph_last_confirmation("family")] = "not-json"
    corrupt = admin.inspect("family")

    assert corrupt.mode == "CORRUPT"
    assert corrupt.confirmed_revision == 5
    assert corrupt.fencing_floor is None
    repaired = admin.reconcile(
        _evidence(revision=5, max_token=9, floor=10),
        corrupt.state_sha256,
        "repair",
    )

    assert repaired.confirmed_revision == 5
    assert repaired.fencing_floor == 10
    assert admin.inspect("family").mode == "READY"


def test_inspection_state_digest_excludes_lease_and_admin_result_receipts():
    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys)
    admin = _admin(redis, keys)
    absent = admin.inspect("family")
    redis.receipts[keys.graph_acquisition_result("family", "acq")] = "opaque"
    redis.receipts[keys.graph_admin_result("family", "nonce")] = "opaque"

    assert admin.inspect("family").state_sha256 == absent.state_sha256


def test_revocation_entry_envelope_is_canonical_strict_and_digest_bound():
    from coordination.serialization import (
        deserialize_revocation_entry,
        serialize_revocation_entry,
    )

    raw = serialize_revocation_entry("a" * 64, 230_000)
    entry = deserialize_revocation_entry(raw)

    assert entry.jti_hmac == "a" * 64
    assert entry.expires_at_ms == 230_000
    assert json.loads(raw)["expires_at_ms"] == "230000"
    for malformed in (
        raw.replace('"version":1', '"version":1,"version":1'),
        raw.replace(',"version":1', ',"extra":true,"version":1'),
        raw.replace('"230000"', '"0230000"'),
        raw.replace("a" * 64, "z" * 64),
    ):
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            deserialize_revocation_entry(malformed)


def test_revocation_uses_server_leeway_time_and_exact_nonce_replay():
    from coordination.upstash import UpstashRevocationStore

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    store = UpstashRevocationStore(redis, keys, leeway_seconds=30)

    first = store.revoke("jti-1", 200, "nonce")
    entry_key = keys.revocation_entry("jti-1")
    first_entry = redis.revocations[entry_key]
    redis.advance(10_000)
    replayed = store.revoke("jti-1", 200, "nonce")
    checked = store.is_revoked("jti-1", 200)

    assert first.code == "REVOKED"
    assert first.server_time_ms == 100_000
    assert first.expires_at_ms == 230_000
    assert replayed == first
    assert redis.revocations[entry_key] == first_entry
    assert checked.code == "REVOKED"
    assert checked.server_time_ms == 110_000
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        store.revoke("changed-jti", 200, "nonce")
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        store.revoke("jti-1", 201, "nonce")
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        UpstashRevocationStore(redis, keys, leeway_seconds=31).revoke(
            "jti-1", 200, "nonce"
        )


def test_revocation_expired_token_retains_receipt_without_creating_entry():
    from coordination.upstash import UpstashRevocationStore

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=250_000)
    store = UpstashRevocationStore(redis, keys, leeway_seconds=30)

    result = store.revoke("expired", 200, "nonce-expired")

    assert result.code == "TOKEN_ALREADY_EXPIRED"
    assert result.revoked is False
    assert keys.revocation_entry("expired") not in redis.revocations
    assert redis.receipt_expiry[keys.revocation_nonce("nonce-expired")] == 310_000


def test_revocation_repairs_short_missing_ttls_and_rejects_overlong_or_malformed_state():
    from coordination.upstash import UpstashRevocationStore

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    store = UpstashRevocationStore(redis, keys, leeway_seconds=30)
    store.revoke("jti", 200, "nonce")
    entry_key = keys.revocation_entry("jti")
    nonce_key = keys.revocation_nonce("nonce")
    redis.revocation_expiry[entry_key] = None
    assert store.is_revoked("jti", 200).revoked is True
    assert redis.revocation_expiry[entry_key] == 230_000
    redis.receipt_expiry[nonce_key] = None
    assert store.revoke("jti", 200, "nonce").code == "REVOKED"
    assert redis.receipt_expiry[nonce_key] == 230_000

    redis.revocation_expiry[entry_key] = 230_001
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        store.is_revoked("jti", 200)
    redis.revocation_expiry[entry_key] = 230_000
    redis.revocations[entry_key] = '{"broken":true}'
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        store.is_revoked("jti", 200)


def test_revocation_validates_retained_receipt_before_ttl_repair():
    from coordination.upstash import UpstashRevocationStore

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    store = UpstashRevocationStore(redis, keys, leeway_seconds=30)
    store.revoke("jti", 200, "nonce")
    nonce_key = keys.revocation_nonce("nonce")
    malformed = json.loads(redis.receipts[nonce_key])
    malformed["extra"] = True
    redis.receipts[nonce_key] = json.dumps(
        malformed, sort_keys=True, separators=(",", ":")
    )
    redis.receipt_expiry[nonce_key] = None

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        store.revoke("jti", 200, "nonce")

    assert redis.receipt_expiry[nonce_key] is None


def test_revocation_outage_fails_closed_for_revoke_and_check():
    from coordination.upstash import UpstashRevocationStore

    class Outage:
        def eval(self, *args, **kwargs):
            raise CoordinationError("COORDINATION_UNAVAILABLE")

    store = UpstashRevocationStore(
        Outage(), RedisKeyBuilder("test", "secret"), leeway_seconds=30
    )
    with pytest.raises(CoordinationError, match="COORDINATION_UNAVAILABLE"):
        store.revoke("jti", 200, "nonce")
    with pytest.raises(CoordinationError, match="COORDINATION_UNAVAILABLE"):
        store.is_revoked("jti", 200)


def test_rate_policy_table_is_exact_typed_and_keeps_comment_story_buckets_distinct():
    from coordination.protocols import RateLimitPolicyId
    from coordination.upstash import RATE_LIMIT_POLICIES

    assert {
        policy: (value.limit, value.window_ms, value.subject_kind)
        for policy, value in RATE_LIMIT_POLICIES.items()
    } == {
        RateLimitPolicyId.LOGIN: (5, 900_000, "IP"),
        RateLimitPolicyId.SUBMIT: (5, 3_600_000, "IP"),
        RateLimitPolicyId.UPLOAD: (10, 3_600_000, "IP"),
        RateLimitPolicyId.COMMENT: (20, 3_600_000, "IDENTITY"),
        RateLimitPolicyId.STORY: (20, 3_600_000, "IDENTITY"),
        RateLimitPolicyId.SEARCH: (60, 60_000, "IP"),
        RateLimitPolicyId.EMAIL_VERIFICATION: (10, 3_600_000, "IP"),
    }
    keys = RedisKeyBuilder("test", "secret")
    assert keys.rate_counter(
        RateLimitPolicyId.COMMENT, "IDENTITY", "usr_1", 0
    ) != keys.rate_counter(RateLimitPolicyId.STORY, "IDENTITY", "usr_1", 0)


def test_rate_limit_exact_window_boundary_n_n_plus_one_and_typed_subjects():
    from coordination.protocols import (
        IdentityRateLimitSubject,
        IpRateLimitSubject,
        RateLimitPolicyId,
    )
    from coordination.upstash import UpstashRateLimiter

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=900_000)
    limiter = UpstashRateLimiter(redis, keys)
    subject = IpRateLimitSubject("IP", "203.0.113.8")
    results = [
        limiter.consume(RateLimitPolicyId.LOGIN, subject, f"nonce-{index}")
        for index in range(1, 7)
    ]

    assert [item.allowed for item in results] == [True] * 5 + [False]
    assert results[-1].observed_count == 6
    assert results[-1].remaining == 0
    assert results[-1].reset_at_ms == 1_800_000
    assert results[-1].retry_after_ms == 900_000
    redis.advance(900_000)
    boundary = limiter.consume(RateLimitPolicyId.LOGIN, subject, "new-window")
    assert boundary.allowed is True
    assert boundary.observed_count == 1
    with pytest.raises(CoordinationError):
        limiter.consume(
            RateLimitPolicyId.LOGIN,
            IdentityRateLimitSubject("IDENTITY", "usr_1"),
            "wrong-kind",
        )
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        limiter.consume(
            RateLimitPolicyId.LOGIN,
            IpRateLimitSubject("IDENTITY", "203.0.113.8"),  # type: ignore[arg-type]
            "wrong-ip-tag",
        )
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        limiter.consume(
            RateLimitPolicyId.UPLOAD,
            IdentityRateLimitSubject("IP", "usr_1"),  # type: ignore[arg-type]
            "wrong-identity-tag",
        )


@pytest.mark.parametrize(
    ("policy_name", "subject"),
    (
        ("LOGIN", ("IP", "203.0.113.1")),
        ("SUBMIT", ("IP", "203.0.113.2")),
        ("UPLOAD", ("IP", "203.0.113.5")),
        ("COMMENT", ("IDENTITY", "usr_comment")),
        ("STORY", ("IDENTITY", "usr_story")),
        ("SEARCH", ("IP", "203.0.113.3")),
        ("EMAIL_VERIFICATION", ("IP", "203.0.113.4")),
    ),
)
def test_every_server_owned_rate_policy_accepts_only_its_typed_subject(
    policy_name, subject
):
    from coordination.protocols import (
        IdentityRateLimitSubject,
        IpRateLimitSubject,
        RateLimitPolicyId,
    )
    from coordination.upstash import UpstashRateLimiter

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    limiter = UpstashRateLimiter(redis, keys)
    kind, value = subject
    typed_subject = (
        IpRateLimitSubject("IP", value)
        if kind == "IP"
        else IdentityRateLimitSubject("IDENTITY", value)
    )

    result = limiter.consume(RateLimitPolicyId[policy_name], typed_subject, policy_name)

    assert result.allowed is True
    assert result.observed_count == 1


def test_rate_nonce_replay_does_not_double_charge_and_changed_input_conflicts():
    from coordination.protocols import IpRateLimitSubject, RateLimitPolicyId
    from coordination.upstash import UpstashRateLimiter

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    limiter = UpstashRateLimiter(redis, keys)
    subject = IpRateLimitSubject("IP", "203.0.113.8")
    first = limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce")
    counter_key = next(iter(redis.rate_counters))
    first_count = redis.rate_counters[counter_key]
    replayed = limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce")

    assert replayed == first
    assert redis.rate_counters[counter_key] == first_count
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        limiter.consume(
            RateLimitPolicyId.SEARCH,
            IpRateLimitSubject("IP", "203.0.113.9"),
            "nonce",
        )
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        limiter.consume(RateLimitPolicyId.LOGIN, subject, "nonce")
    redis.advance(20_000)
    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce")


def test_rate_limit_repairs_ttls_and_rejects_malformed_or_signed_64_overflow():
    from coordination.protocols import IpRateLimitSubject, RateLimitPolicyId
    from coordination.upstash import UpstashRateLimiter

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    limiter = UpstashRateLimiter(redis, keys)
    subject = IpRateLimitSubject("IP", "203.0.113.8")
    limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce-1")
    counter_key = next(iter(redis.rate_counters))
    nonce_key = keys.rate_nonce("nonce-1")
    redis.rate_counter_expiry[counter_key] = None
    limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce-2")
    assert redis.rate_counter_expiry[counter_key] == 120_000
    redis.receipt_expiry[nonce_key] = None
    limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce-1")
    assert redis.receipt_expiry[nonce_key] == 180_000

    redis.rate_counters[counter_key] = str(2**63 - 1)
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        limiter.consume(RateLimitPolicyId.SEARCH, subject, "overflow")
    redis.rate_counters[counter_key] = "not-decimal"
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        limiter.consume(RateLimitPolicyId.SEARCH, subject, "malformed")


def test_rate_limit_validates_retained_receipt_before_ttl_repair():
    from coordination.protocols import IpRateLimitSubject, RateLimitPolicyId
    from coordination.upstash import UpstashRateLimiter

    keys = RedisKeyBuilder("test", "secret")
    redis = LeaseEvalStub(keys, now_ms=100_000)
    limiter = UpstashRateLimiter(redis, keys)
    subject = IpRateLimitSubject("IP", "203.0.113.8")
    limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce")
    nonce_key = keys.rate_nonce("nonce")
    malformed = json.loads(redis.receipts[nonce_key])
    malformed["extra"] = True
    redis.receipts[nonce_key] = json.dumps(
        malformed, sort_keys=True, separators=(",", ":")
    )
    redis.receipt_expiry[nonce_key] = None

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        limiter.consume(RateLimitPolicyId.SEARCH, subject, "nonce")

    assert redis.receipt_expiry[nonce_key] is None


def test_rate_limit_outage_fails_closed():
    from coordination.protocols import IpRateLimitSubject, RateLimitPolicyId
    from coordination.upstash import UpstashRateLimiter

    class Outage:
        def eval(self, *args, **kwargs):
            raise CoordinationError("COORDINATION_UNAVAILABLE")

    limiter = UpstashRateLimiter(Outage(), RedisKeyBuilder("test", "secret"))
    with pytest.raises(CoordinationError, match="COORDINATION_UNAVAILABLE"):
        limiter.consume(
            RateLimitPolicyId.LOGIN,
            IpRateLimitSubject("IP", "203.0.113.8"),
            "nonce",
        )


def test_coordination_package_exports_complete_upstash_implementations():
    import coordination

    assert coordination.UpstashLeaseManager.__name__ == "UpstashLeaseManager"
    assert coordination.UpstashCommitCoordinator.__name__ == "UpstashCommitCoordinator"
    assert coordination.UpstashCoordinationAdmin.__name__ == "UpstashCoordinationAdmin"
    assert coordination.UpstashRevocationStore.__name__ == "UpstashRevocationStore"
    assert coordination.UpstashRateLimiter.__name__ == "UpstashRateLimiter"
    assert callable(coordination.new_acquisition_id)


def test_unknown_redis_error_tag_is_collapsed_without_leaking_details():
    from coordination.upstash import UpstashLeaseManager

    class UnknownError:
        def eval(self, *args, **kwargs):
            return ["ERR", "secret-owner@example.com"]

    manager = UpstashLeaseManager(
        UnknownError(), RedisKeyBuilder("test", "do-not-leak-secret")
    )
    with pytest.raises(CoordinationError) as raised:
        manager.acquire("private-scope", "acq")

    assert raised.value.code == "COORDINATION_STATE_CORRUPT"
    assert "secret-owner" not in str(raised.value)
