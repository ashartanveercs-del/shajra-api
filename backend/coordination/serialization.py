"""Canonical coordination envelopes and collision-separated Redis keys."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn, cast

from domain.ids import OperationId
from repositories import (
    CommitPermit,
    GraphCommit,
    StagedWriteReceipt,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_commit_sha256,
)
from repositories.protocols import graph_write_set_from_json, graph_write_set_sha256

from coordination.protocols import (
    CoordinationAdminResult,
    CommitReservation,
    ConfirmedCommitReceipt,
    CoordinationError,
    CoordinationEvidence,
    GraphLease,
    Lease,
    LeaseReleaseResult,
    RateLimitPolicyId,
    RateLimitResult,
    ReconciledHeadReceipt,
    RevocationResult,
)


SIGNED_64_MIN = -(2**63)
SIGNED_64_MAX = 2**63 - 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DEPLOYMENT_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_CANONICAL_DECIMAL_RE = re.compile(r"0|-[1-9][0-9]*|[1-9][0-9]*")
LeaseDomain = Literal["GENERIC", "GRAPH_COMMIT"]


@dataclass(frozen=True, slots=True)
class CanonicalInput:
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class GraphLockEnvelope:
    scope_hmac: str
    acquisition_id_hmac: str
    fencing_token: int
    base_revision: int
    expires_at_ms: int
    ttl_ms: int
    renew_deadline_ms: int


def _corrupt() -> NoReturn:
    raise CoordinationError("COORDINATION_STATE_CORRUPT")


def canonical_ascii_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        encoded.encode("ascii")
        return encoded
    except (TypeError, ValueError, UnicodeError):
        _corrupt()


def sha256_ascii(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        _corrupt()


def canonical_decimal(
    value: int, *, minimum: int = SIGNED_64_MIN, maximum: int = SIGNED_64_MAX
) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        _corrupt()
    return str(value)


def parse_canonical_decimal(
    value: object, *, minimum: int = SIGNED_64_MIN, maximum: int = SIGNED_64_MAX
) -> int:
    if not isinstance(value, str) or not _CANONICAL_DECIMAL_RE.fullmatch(value):
        _corrupt()
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        _corrupt()
    return parsed


def _nonempty_text(value: object, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        _corrupt()
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _corrupt()
    return value


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _corrupt()
        result[key] = value
    return result


def _strict_object(
    raw: str, *, schema: str, fields: frozenset[str]
) -> dict[str, object]:
    try:
        raw.encode("ascii")
        parsed = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _value: _corrupt(),
        )
    except CoordinationError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        _corrupt()
    if not isinstance(parsed, dict) or set(parsed) != fields:
        _corrupt()
    if parsed.get("schema") != schema or type(parsed.get("version")) is not int:
        _corrupt()
    if parsed["version"] != 1 or canonical_ascii_json(parsed) != raw:
        _corrupt()
    return parsed


class RedisKeyBuilder:
    """Build all Redis keys through versioned, HMAC-separated domains."""

    def __init__(self, deployment: str, secret: str) -> None:
        if (
            not isinstance(deployment, str)
            or not 1 <= len(deployment) <= 32
            or not _DEPLOYMENT_RE.fullmatch(deployment)
        ):
            _corrupt()
        if not isinstance(secret, str) or not secret or "\x00" in secret:
            _corrupt()
        self.deployment = deployment
        try:
            self._secret = secret.encode("utf-8")
        except UnicodeEncodeError:
            _corrupt()

    def hmac_hex(self, label: str, value: str) -> str:
        _nonempty_text(label, maximum=64)
        _nonempty_text(value, maximum=4096)
        try:
            payload = label.encode("ascii") + b"\0" + value.encode("utf-8")
        except UnicodeEncodeError:
            _corrupt()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def scope_hmac(self, domain: LeaseDomain, scope: str) -> str:
        label = "generic-scope" if domain == "GENERIC" else "graph-scope"
        return self.hmac_hex(label, scope)

    def acquisition_id_hmac(self, domain: LeaseDomain, acquisition_id: str) -> str:
        label = (
            "generic-acquisition-id" if domain == "GENERIC" else "graph-acquisition-id"
        )
        return self.hmac_hex(label, acquisition_id)

    def operation_nonce_hmac(self, domain: LeaseDomain, request_nonce: str) -> str:
        label = (
            "generic-operation-nonce"
            if domain == "GENERIC"
            else "graph-operation-nonce"
        )
        return self.hmac_hex(label, request_nonce)

    def admin_nonce_hmac(self, request_nonce: str) -> str:
        return self.hmac_hex("graph-admin-nonce", request_nonce)

    def authorization_nonce_hmac(self, request_nonce: str) -> str:
        return self.hmac_hex("graph-authorization-nonce", request_nonce)

    def revocation_jti_hmac(self, jti: str) -> str:
        return self.hmac_hex("revocation-jti", jti)

    def revocation_nonce_hmac(self, request_nonce: str) -> str:
        return self.hmac_hex("revocation-nonce", request_nonce)

    def rate_subject_hmac(self, kind: str, subject: str) -> str:
        if kind not in {"IP", "IDENTITY"}:
            _corrupt()
        _nonempty_text(subject, maximum=4096)
        try:
            payload = (
                b"rate-subject\0"
                + kind.encode("ascii")
                + b"\0"
                + subject.encode("utf-8")
            )
        except UnicodeEncodeError:
            _corrupt()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def rate_nonce_hmac(self, request_nonce: str) -> str:
        return self.hmac_hex("rate-nonce", request_nonce)

    def history_nonce_hmac(self, request_nonce: str) -> str:
        return self.hmac_hex("history-nonce", request_nonce)

    def _scope_tag(self, domain: Literal["graph", "generic"], scope: str) -> str:
        lease_domain: LeaseDomain = "GRAPH_COMMIT" if domain == "graph" else "GENERIC"
        return (
            f"{{sj:v1:{self.deployment}:{domain}:"
            f"{self.scope_hmac(lease_domain, scope)}}}"
        )

    def graph_lock(self, scope: str) -> str:
        return f"{self._scope_tag('graph', scope)}:lock"

    def graph_fence(self, scope: str) -> str:
        return f"{self._scope_tag('graph', scope)}:fence"

    def graph_confirmed_revision(self, scope: str) -> str:
        return f"{self._scope_tag('graph', scope)}:confirmed-revision"

    def graph_reservation(self, scope: str) -> str:
        return f"{self._scope_tag('graph', scope)}:commit-reservation"

    def graph_last_confirmation(self, scope: str) -> str:
        return f"{self._scope_tag('graph', scope)}:last-confirmation"

    def graph_acquisition_result(self, scope: str, acquisition_id: str) -> str:
        suffix = self.acquisition_id_hmac("GRAPH_COMMIT", acquisition_id)
        return f"{self._scope_tag('graph', scope)}:lease-result:acquire:{suffix}"

    def graph_operation_result(self, scope: str, request_nonce: str) -> str:
        suffix = self.operation_nonce_hmac("GRAPH_COMMIT", request_nonce)
        return f"{self._scope_tag('graph', scope)}:lease-result:operation:{suffix}"

    def graph_admin_result(self, scope: str, request_nonce: str) -> str:
        suffix = self.admin_nonce_hmac(request_nonce)
        return f"{self._scope_tag('graph', scope)}:admin-result:{suffix}"

    def generic_lock(self, scope: str) -> str:
        return f"{self._scope_tag('generic', scope)}:lock"

    def generic_acquisition_result(self, scope: str, acquisition_id: str) -> str:
        suffix = self.acquisition_id_hmac("GENERIC", acquisition_id)
        return f"{self._scope_tag('generic', scope)}:lease-result:acquire:{suffix}"

    def generic_operation_result(self, scope: str, request_nonce: str) -> str:
        suffix = self.operation_nonce_hmac("GENERIC", request_nonce)
        return f"{self._scope_tag('generic', scope)}:lease-result:operation:{suffix}"

    def _revocation_tag(self) -> str:
        return f"{{sj:v1:{self.deployment}:revocation}}"

    def revocation_entry(self, jti: str) -> str:
        return f"{self._revocation_tag()}:entry:{self.revocation_jti_hmac(jti)}"

    def revocation_nonce(self, request_nonce: str) -> str:
        return (
            f"{self._revocation_tag()}:nonce:"
            f"{self.revocation_nonce_hmac(request_nonce)}"
        )

    def _rate_tag(self) -> str:
        return f"{{sj:v1:{self.deployment}:rate}}"

    def rate_counter(
        self,
        policy: RateLimitPolicyId,
        subject_kind: str,
        subject: str,
        window_start_ms: int,
    ) -> str:
        if not isinstance(policy, RateLimitPolicyId):
            _corrupt()
        window = canonical_decimal(window_start_ms, minimum=0)
        subject_hmac = self.rate_subject_hmac(subject_kind, subject)
        return f"{self._rate_tag()}:counter:{policy.value}:{subject_hmac}:{window}"

    def rate_nonce(self, request_nonce: str) -> str:
        return f"{self._rate_tag()}:nonce:{self.rate_nonce_hmac(request_nonce)}"

    def _history_tag(self) -> str:
        return f"{{sj:v1:{self.deployment}:history}}"

    def history_entries(self) -> str:
        return f"{self._history_tag()}:entries"

    def history_active(self) -> str:
        return f"{self._history_tag()}:active"

    def history_write_guard(self) -> str:
        return f"{self._history_tag()}:write-guard"

    def history_claim(self, request_nonce: str) -> str:
        return (
            f"{self._history_tag()}:claim:"
            f"{self.history_nonce_hmac(request_nonce)}"
        )

    def history_result(self, request_nonce: str) -> str:
        return (
            f"{self._history_tag()}:result:"
            f"{self.history_nonce_hmac(request_nonce)}"
        )

    def history_context(self, request_nonce: str) -> str:
        return (
            f"{self._history_tag()}:context:"
            f"{self.history_nonce_hmac(request_nonce)}"
        )

    def history_pop_result(self, request_nonce: str) -> str:
        """Backward-compatible name for the durable undo result key."""
        return self.history_result(request_nonce)


def lease_acquire_request(
    keys: RedisKeyBuilder,
    domain: LeaseDomain,
    scope: str,
    acquisition_id: str,
    ttl_ms: int,
    committed_revision: int | None = None,
) -> CanonicalInput:
    if domain not in {"GENERIC", "GRAPH_COMMIT"}:
        _corrupt()
    value: dict[str, object] = {
        "schema": "shajra.lease-acquire-request",
        "version": 1,
        "domain": domain,
        "scope_hmac": keys.scope_hmac(domain, scope),
        "acquisition_id_hmac": keys.acquisition_id_hmac(domain, acquisition_id),
        "requested_ttl_ms": canonical_decimal(ttl_ms, minimum=1),
    }
    if domain == "GRAPH_COMMIT":
        if committed_revision is None:
            _corrupt()
        value["committed_revision"] = canonical_decimal(committed_revision, minimum=0)
    elif committed_revision is not None:
        _corrupt()
    text = canonical_ascii_json(value)
    return CanonicalInput(text, sha256_ascii(text))


_STAGED_FIELDS = frozenset(
    {
        "schema",
        "version",
        "operation_id",
        "revision",
        "fencing_token",
        "write_set_json",
        "write_set_sha256",
    }
)


def serialize_staged_write_receipt(receipt: StagedWriteReceipt) -> str:
    try:
        if not isinstance(receipt.operation_id, str):
            _corrupt()
        operation_id = _nonempty_text(receipt.operation_id)
        if not operation_id.startswith("op_") or operation_id == "op_":
            _corrupt()
        write_set = graph_write_set_from_json(receipt.write_set_json)
        canonical_write_set = canonical_graph_write_set_json(write_set)
        if canonical_write_set != receipt.write_set_json:
            _corrupt()
        digest = graph_write_set_sha256(write_set)
        if not hmac.compare_digest(digest, _digest(receipt.write_set_sha256)):
            _corrupt()
        return canonical_ascii_json(
            {
                "schema": "shajra.staged-write-receipt",
                "version": 1,
                "operation_id": operation_id,
                "revision": canonical_decimal(receipt.revision, minimum=1),
                "fencing_token": canonical_decimal(receipt.fencing_token, minimum=1),
                "write_set_json": canonical_write_set,
                "write_set_sha256": digest,
            }
        )
    except CoordinationError:
        raise
    except (TypeError, ValueError):
        _corrupt()


def deserialize_staged_write_receipt(raw: str) -> StagedWriteReceipt:
    try:
        value = _strict_object(
            raw,
            schema="shajra.staged-write-receipt",
            fields=_STAGED_FIELDS,
        )
        operation_id = _nonempty_text(value["operation_id"])
        if not operation_id.startswith("op_") or operation_id == "op_":
            _corrupt()
        write_set_json = _nonempty_text(value["write_set_json"], maximum=2_000_000)
        write_set = graph_write_set_from_json(write_set_json)
        if canonical_graph_write_set_json(write_set) != write_set_json:
            _corrupt()
        write_set_digest = _digest(value["write_set_sha256"])
        if not hmac.compare_digest(graph_write_set_sha256(write_set), write_set_digest):
            _corrupt()
        return StagedWriteReceipt(
            operation_id=OperationId(operation_id),
            revision=parse_canonical_decimal(value["revision"], minimum=1),
            fencing_token=parse_canonical_decimal(value["fencing_token"], minimum=1),
            write_set_json=write_set_json,
            write_set_sha256=write_set_digest,
        )
    except CoordinationError:
        raise
    except (TypeError, ValueError):
        _corrupt()


_GENERIC_LOCK_FIELDS = frozenset(
    {
        "schema",
        "version",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "expires_at_ms",
        "ttl_ms",
        "renew_deadline_ms",
    }
)
_GRAPH_LOCK_FIELDS = _GENERIC_LOCK_FIELDS | {"fencing_token", "base_revision"}


def _validate_lease_timing(expires_at_ms: int, ttl_ms: int, deadline_ms: int) -> None:
    canonical_decimal(expires_at_ms, minimum=1)
    canonical_decimal(ttl_ms, minimum=1)
    canonical_decimal(deadline_ms, minimum=0)
    if expires_at_ms - deadline_ms != 5_000 or ttl_ms > expires_at_ms:
        _corrupt()


def serialize_generic_lock(lease: Lease, keys: RedisKeyBuilder) -> str:
    _validate_lease_timing(lease.expires_at_ms, lease.ttl_ms, lease.renew_deadline_ms)
    return canonical_ascii_json(
        {
            "schema": "shajra.generic-lock",
            "version": 1,
            "domain": "GENERIC",
            "scope_hmac": keys.scope_hmac("GENERIC", lease.scope),
            "acquisition_id_hmac": keys.acquisition_id_hmac(
                "GENERIC", lease.acquisition_id
            ),
            "expires_at_ms": canonical_decimal(lease.expires_at_ms, minimum=1),
            "ttl_ms": canonical_decimal(lease.ttl_ms, minimum=1),
            "renew_deadline_ms": canonical_decimal(lease.renew_deadline_ms, minimum=0),
        }
    )


def serialize_graph_lock(lease: GraphLease, keys: RedisKeyBuilder) -> str:
    _validate_lease_timing(lease.expires_at_ms, lease.ttl_ms, lease.renew_deadline_ms)
    return canonical_ascii_json(
        {
            "schema": "shajra.graph-lock",
            "version": 1,
            "domain": "GRAPH_COMMIT",
            "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", lease.scope),
            "acquisition_id_hmac": keys.acquisition_id_hmac(
                "GRAPH_COMMIT", lease.acquisition_id
            ),
            "fencing_token": canonical_decimal(lease.fencing_token, minimum=1),
            "base_revision": canonical_decimal(lease.base_revision, minimum=0),
            "expires_at_ms": canonical_decimal(lease.expires_at_ms, minimum=1),
            "ttl_ms": canonical_decimal(lease.ttl_ms, minimum=1),
            "renew_deadline_ms": canonical_decimal(lease.renew_deadline_ms, minimum=0),
        }
    )


def deserialize_generic_lock(
    raw: str, keys: RedisKeyBuilder, scope: str, acquisition_id: str
) -> Lease:
    value = _strict_object(
        raw, schema="shajra.generic-lock", fields=_GENERIC_LOCK_FIELDS
    )
    if (
        value["domain"] != "GENERIC"
        or value["scope_hmac"] != keys.scope_hmac("GENERIC", scope)
        or value["acquisition_id_hmac"]
        != keys.acquisition_id_hmac("GENERIC", acquisition_id)
    ):
        _corrupt()
    expires = parse_canonical_decimal(value["expires_at_ms"], minimum=1)
    ttl = parse_canonical_decimal(value["ttl_ms"], minimum=1)
    deadline = parse_canonical_decimal(value["renew_deadline_ms"], minimum=0)
    _validate_lease_timing(expires, ttl, deadline)
    return Lease(scope, acquisition_id, expires, ttl, deadline)


def deserialize_graph_lock(
    raw: str, keys: RedisKeyBuilder, scope: str, acquisition_id: str
) -> GraphLease:
    value = _strict_object(raw, schema="shajra.graph-lock", fields=_GRAPH_LOCK_FIELDS)
    if (
        value["domain"] != "GRAPH_COMMIT"
        or value["scope_hmac"] != keys.scope_hmac("GRAPH_COMMIT", scope)
        or value["acquisition_id_hmac"]
        != keys.acquisition_id_hmac("GRAPH_COMMIT", acquisition_id)
    ):
        _corrupt()
    expires = parse_canonical_decimal(value["expires_at_ms"], minimum=1)
    ttl = parse_canonical_decimal(value["ttl_ms"], minimum=1)
    deadline = parse_canonical_decimal(value["renew_deadline_ms"], minimum=0)
    _validate_lease_timing(expires, ttl, deadline)
    return GraphLease(
        scope,
        acquisition_id,
        parse_canonical_decimal(value["fencing_token"], minimum=1),
        parse_canonical_decimal(value["base_revision"], minimum=0),
        expires,
        ttl,
        deadline,
    )


def inspect_graph_lock(
    raw: str, keys: RedisKeyBuilder, scope: str
) -> GraphLockEnvelope:
    value = _strict_object(raw, schema="shajra.graph-lock", fields=_GRAPH_LOCK_FIELDS)
    scope_hmac = _digest(value["scope_hmac"])
    acquisition_hmac = _digest(value["acquisition_id_hmac"])
    if value["domain"] != "GRAPH_COMMIT" or scope_hmac != keys.scope_hmac(
        "GRAPH_COMMIT", scope
    ):
        _corrupt()
    expires = parse_canonical_decimal(value["expires_at_ms"], minimum=1)
    ttl = parse_canonical_decimal(value["ttl_ms"], minimum=1)
    deadline = parse_canonical_decimal(value["renew_deadline_ms"], minimum=0)
    _validate_lease_timing(expires, ttl, deadline)
    return GraphLockEnvelope(
        scope_hmac,
        acquisition_hmac,
        parse_canonical_decimal(value["fencing_token"], minimum=1),
        parse_canonical_decimal(value["base_revision"], minimum=0),
        expires,
        ttl,
        deadline,
    )


def _canonical_input(value: dict[str, object]) -> CanonicalInput:
    text = canonical_ascii_json(value)
    return CanonicalInput(text, sha256_ascii(text))


_LEASE_OPERATION_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "method",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "request_nonce_hmac",
        "lock_sha256",
        "requested_ttl_ms",
    }
)


def lease_operation_request(
    keys: RedisKeyBuilder,
    method: Literal["renew", "release"],
    lease: Lease | GraphLease,
    request_nonce: str,
    ttl_ms: int | None = None,
) -> CanonicalInput:
    if method not in {"renew", "release"}:
        _corrupt()
    if type(lease) is Lease:
        domain: LeaseDomain = "GENERIC"
        lock_json = serialize_generic_lock(lease, keys)
    elif type(lease) is GraphLease:
        domain = "GRAPH_COMMIT"
        lock_json = serialize_graph_lock(lease, keys)
    else:
        _corrupt()
    value: dict[str, object] = {
        "schema": "shajra.lease-operation-request",
        "version": 1,
        "method": method,
        "domain": domain,
        "scope_hmac": keys.scope_hmac(domain, lease.scope),
        "acquisition_id_hmac": keys.acquisition_id_hmac(domain, lease.acquisition_id),
        "request_nonce_hmac": keys.operation_nonce_hmac(domain, request_nonce),
        "lock_sha256": sha256_ascii(lock_json),
    }
    if method == "renew":
        if ttl_ms is None:
            _corrupt()
        value["requested_ttl_ms"] = canonical_decimal(ttl_ms, minimum=1)
    elif ttl_ms is not None:
        _corrupt()
    return _canonical_input(value)


_ADMIN_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "method",
        "scope_hmac",
        "evidence_sha256",
        "expected_state_sha256",
    }
)


def coordination_admin_request(
    keys: RedisKeyBuilder,
    method: Literal["initialize", "reconcile"],
    scope: str,
    evidence_sha256: str,
    expected_state_sha256: str,
) -> CanonicalInput:
    if method not in {"initialize", "reconcile"}:
        _corrupt()
    return _canonical_input(
        {
            "schema": "shajra.coordination-admin-request",
            "version": 1,
            "method": method,
            "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", scope),
            "evidence_sha256": _digest(evidence_sha256),
            "expected_state_sha256": _digest(expected_state_sha256),
        }
    )


_REVOCATION_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "jti_hmac",
        "token_expires_at_s",
        "leeway_s",
    }
)


def revocation_request(
    keys: RedisKeyBuilder, jti: str, token_expires_at_s: int, leeway_s: int
) -> CanonicalInput:
    if not 0 <= leeway_s <= 300:
        _corrupt()
    return _canonical_input(
        {
            "schema": "shajra.revocation-request",
            "version": 1,
            "jti_hmac": keys.revocation_jti_hmac(jti),
            "token_expires_at_s": canonical_decimal(token_expires_at_s, minimum=0),
            "leeway_s": canonical_decimal(leeway_s, minimum=0, maximum=300),
        }
    )


_RATE_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "policy_id",
        "subject_kind",
        "subject_hmac",
        "window_start_ms",
        "window_ms",
        "limit",
    }
)


def rate_request(
    keys: RedisKeyBuilder,
    policy: RateLimitPolicyId,
    subject_kind: Literal["IP", "IDENTITY"],
    subject: str,
    window_start_ms: int,
    window_ms: int,
    limit: int,
) -> CanonicalInput:
    if not isinstance(policy, RateLimitPolicyId) or subject_kind not in {
        "IP",
        "IDENTITY",
    }:
        _corrupt()
    return _canonical_input(
        {
            "schema": "shajra.rate-request",
            "version": 1,
            "policy_id": policy.value,
            "subject_kind": subject_kind,
            "subject_hmac": keys.rate_subject_hmac(subject_kind, subject),
            "window_start_ms": canonical_decimal(window_start_ms, minimum=0),
            "window_ms": canonical_decimal(window_ms, minimum=1),
            "limit": canonical_decimal(limit, minimum=1),
        }
    )


def _load_expected_request(
    request: CanonicalInput, *, schema: str, fields: frozenset[str]
) -> dict[str, object]:
    if not isinstance(request, CanonicalInput):
        _corrupt()
    value = _strict_object(request.text, schema=schema, fields=fields)
    if request.sha256 != sha256_ascii(request.text):
        _corrupt()
    return value


_LEASE_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "scope",
        "acquisition_id",
        "expires_at_ms",
        "ttl_ms",
        "renew_deadline_ms",
    }
)
_GRAPH_LEASE_PAYLOAD_FIELDS = _LEASE_PAYLOAD_FIELDS | {
    "fencing_token",
    "base_revision",
}


def _lease_payload(lease: Lease | GraphLease) -> dict[str, object]:
    _validate_lease_timing(lease.expires_at_ms, lease.ttl_ms, lease.renew_deadline_ms)
    value: dict[str, object] = {
        "schema": (
            "shajra.generic-lease" if type(lease) is Lease else "shajra.graph-lease"
        ),
        "version": 1,
        "scope": _nonempty_text(lease.scope),
        "acquisition_id": _nonempty_text(lease.acquisition_id),
        "expires_at_ms": canonical_decimal(lease.expires_at_ms, minimum=1),
        "ttl_ms": canonical_decimal(lease.ttl_ms, minimum=1),
        "renew_deadline_ms": canonical_decimal(lease.renew_deadline_ms, minimum=0),
    }
    if type(lease) is GraphLease:
        value["fencing_token"] = canonical_decimal(lease.fencing_token, minimum=1)
        value["base_revision"] = canonical_decimal(lease.base_revision, minimum=0)
    elif type(lease) is not Lease:
        _corrupt()
    return value


def _exact_nested(
    value: object, *, schema: str, fields: frozenset[str]
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _corrupt()
    if value.get("schema") != schema or type(value.get("version")) is not int:
        _corrupt()
    if value["version"] != 1:
        _corrupt()
    return value


def _parse_lease_payload(value: object) -> Lease | GraphLease:
    if not isinstance(value, dict):
        _corrupt()
    schema = value.get("schema")
    if schema == "shajra.generic-lease":
        item = _exact_nested(value, schema=schema, fields=_LEASE_PAYLOAD_FIELDS)
        graph = False
    elif schema == "shajra.graph-lease":
        item = _exact_nested(value, schema=schema, fields=_GRAPH_LEASE_PAYLOAD_FIELDS)
        graph = True
    else:
        _corrupt()
    scope = _nonempty_text(item["scope"])
    acquisition_id = _nonempty_text(item["acquisition_id"])
    expires = parse_canonical_decimal(item["expires_at_ms"], minimum=1)
    ttl = parse_canonical_decimal(item["ttl_ms"], minimum=1)
    deadline = parse_canonical_decimal(item["renew_deadline_ms"], minimum=0)
    _validate_lease_timing(expires, ttl, deadline)
    if graph:
        return GraphLease(
            scope,
            acquisition_id,
            parse_canonical_decimal(item["fencing_token"], minimum=1),
            parse_canonical_decimal(item["base_revision"], minimum=0),
            expires,
            ttl,
            deadline,
        )
    return Lease(scope, acquisition_id, expires, ttl, deadline)


@dataclass(frozen=True, slots=True)
class LeaseAcquisitionReceipt:
    input_sha256: str
    lease: Lease | GraphLease
    receipt_expires_at_ms: int


_ACQUISITION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "input_sha256",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "requested_ttl_ms",
        "lease",
        "receipt_expires_at_ms",
    }
)
_GRAPH_ACQUISITION_RECEIPT_FIELDS = _ACQUISITION_RECEIPT_FIELDS | {"committed_revision"}


def serialize_lease_acquisition_receipt(
    request: CanonicalInput,
    lease: Lease | GraphLease,
    receipt_expires_at_ms: int,
) -> str:
    expected_fields = (
        _LEASE_ACQUIRE_GRAPH_FIELDS
        if type(lease) is GraphLease
        else _LEASE_ACQUIRE_GENERIC_FIELDS
    )
    request_value = _load_expected_request(
        request,
        schema="shajra.lease-acquire-request",
        fields=expected_fields,
    )
    if type(lease) is GraphLease:
        domain: LeaseDomain = "GRAPH_COMMIT"
        if request_value["domain"] != domain:
            _corrupt()
    else:
        domain = "GENERIC"
        if request_value["domain"] != domain:
            _corrupt()
    lease_payload = _lease_payload(lease)
    expected_receipt_expiry = lease.expires_at_ms - lease.ttl_ms + 60_000
    if receipt_expires_at_ms != expected_receipt_expiry:
        _corrupt()
    value: dict[str, object] = {
        "schema": "shajra.lease-acquisition-result",
        "version": 1,
        "input_sha256": request.sha256,
        "domain": domain,
        "scope_hmac": request_value["scope_hmac"],
        "acquisition_id_hmac": request_value["acquisition_id_hmac"],
        "requested_ttl_ms": request_value["requested_ttl_ms"],
        "lease": lease_payload,
        "receipt_expires_at_ms": canonical_decimal(receipt_expires_at_ms, minimum=1),
    }
    if domain == "GRAPH_COMMIT":
        graph_lease = cast(GraphLease, lease)
        if request_value["committed_revision"] != str(graph_lease.base_revision):
            _corrupt()
        value["committed_revision"] = request_value["committed_revision"]
    return canonical_ascii_json(value)


def deserialize_lease_acquisition_receipt(
    raw: str,
    request: CanonicalInput,
    keys: RedisKeyBuilder,
    scope: str,
    acquisition_id: str,
) -> LeaseAcquisitionReceipt:
    try:
        generic_request: dict[str, object] | None = None
        graph_request: dict[str, object] | None = None
        try:
            generic_request = _load_expected_request(
                request,
                schema="shajra.lease-acquire-request",
                fields=_LEASE_ACQUIRE_GENERIC_FIELDS,
            )
        except CoordinationError:
            graph_request = _load_expected_request(
                request,
                schema="shajra.lease-acquire-request",
                fields=_LEASE_ACQUIRE_GRAPH_FIELDS,
            )
        request_value = (
            generic_request if generic_request is not None else graph_request
        )
        if request_value is None:
            _corrupt()
        if request_value["domain"] == "GENERIC":
            domain: LeaseDomain = "GENERIC"
        elif request_value["domain"] == "GRAPH_COMMIT":
            domain = "GRAPH_COMMIT"
        else:
            _corrupt()
        fields = (
            _GRAPH_ACQUISITION_RECEIPT_FIELDS
            if domain == "GRAPH_COMMIT"
            else _ACQUISITION_RECEIPT_FIELDS
        )
        value = _strict_object(
            raw, schema="shajra.lease-acquisition-result", fields=fields
        )
        repeated = {
            "input_sha256": request.sha256,
            "domain": domain,
            "scope_hmac": request_value["scope_hmac"],
            "acquisition_id_hmac": request_value["acquisition_id_hmac"],
            "requested_ttl_ms": request_value["requested_ttl_ms"],
        }
        if domain == "GRAPH_COMMIT":
            repeated["committed_revision"] = request_value["committed_revision"]
        if any(value[key] != expected for key, expected in repeated.items()):
            _corrupt()
        if value["scope_hmac"] != keys.scope_hmac(domain, scope) or value[
            "acquisition_id_hmac"
        ] != keys.acquisition_id_hmac(domain, acquisition_id):
            _corrupt()
        lease = _parse_lease_payload(value["lease"])
        if lease.scope != scope or lease.acquisition_id != acquisition_id:
            _corrupt()
        if (domain == "GENERIC" and type(lease) is not Lease) or (
            domain == "GRAPH_COMMIT" and type(lease) is not GraphLease
        ):
            _corrupt()
        requested_ttl = parse_canonical_decimal(
            request_value["requested_ttl_ms"], minimum=1
        )
        if lease.ttl_ms > requested_ttl:
            _corrupt()
        receipt_expiry = parse_canonical_decimal(
            value["receipt_expires_at_ms"], minimum=1
        )
        if receipt_expiry != lease.expires_at_ms - lease.ttl_ms + 60_000:
            _corrupt()
        return LeaseAcquisitionReceipt(
            request.sha256,
            lease,
            receipt_expiry,
        )
    except CoordinationError:
        raise
    except (TypeError, ValueError):
        _corrupt()


@dataclass(frozen=True, slots=True)
class LeaseOperationReceipt:
    input_sha256: str
    result: Lease | GraphLease | LeaseReleaseResult
    receipt_expires_at_ms: int


_OPERATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "input_sha256",
        "method",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "request_nonce_hmac",
        "result",
        "receipt_expires_at_ms",
    }
)
_RELEASE_RESULT_FIELDS = frozenset(
    {"schema", "version", "code", "acquisition_id", "released_at_ms"}
)


def _release_payload(result: LeaseReleaseResult) -> dict[str, object]:
    if result.code not in {"LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"}:
        _corrupt()
    return {
        "schema": "shajra.lease-release-result",
        "version": 1,
        "code": result.code,
        "acquisition_id": _nonempty_text(result.acquisition_id),
        "released_at_ms": canonical_decimal(result.released_at_ms, minimum=0),
    }


def _parse_release_payload(value: object) -> LeaseReleaseResult:
    item = _exact_nested(
        value,
        schema="shajra.lease-release-result",
        fields=_RELEASE_RESULT_FIELDS,
    )
    code_value = item["code"]
    if code_value not in {"LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"}:
        _corrupt()
    code = cast(Literal["LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"], code_value)
    return LeaseReleaseResult(
        code,
        _nonempty_text(item["acquisition_id"]),
        parse_canonical_decimal(item["released_at_ms"], minimum=0),
    )


def serialize_lease_operation_receipt(
    request: CanonicalInput,
    result: Lease | GraphLease | LeaseReleaseResult,
    receipt_expires_at_ms: int,
) -> str:
    request_value = _load_operation_request(request)
    method = request_value["method"]
    if method == "renew":
        if type(result) is Lease:
            payload = _lease_payload(result)
        elif type(result) is GraphLease:
            payload = _lease_payload(result)
        else:
            _corrupt()
        expected_receipt_expiry = result.expires_at_ms - result.ttl_ms + 60_000
    else:
        if not isinstance(result, LeaseReleaseResult):
            _corrupt()
        payload = _release_payload(result)
        expected_receipt_expiry = result.released_at_ms + 60_000
    if receipt_expires_at_ms != expected_receipt_expiry:
        _corrupt()
    return canonical_ascii_json(
        {
            "schema": "shajra.lease-operation-result",
            "version": 1,
            "input_sha256": request.sha256,
            "method": method,
            "domain": request_value["domain"],
            "scope_hmac": request_value["scope_hmac"],
            "acquisition_id_hmac": request_value["acquisition_id_hmac"],
            "request_nonce_hmac": request_value["request_nonce_hmac"],
            "result": payload,
            "receipt_expires_at_ms": canonical_decimal(
                receipt_expires_at_ms, minimum=1
            ),
        }
    )


def _load_operation_request(request: CanonicalInput) -> dict[str, object]:
    try:
        value = _load_expected_request(
            request,
            schema="shajra.lease-operation-request",
            fields=_LEASE_OPERATION_REQUEST_FIELDS,
        )
        if value["method"] != "renew":
            _corrupt()
        return value
    except CoordinationError:
        release_fields = _LEASE_OPERATION_REQUEST_FIELDS - {"requested_ttl_ms"}
        value = _load_expected_request(
            request,
            schema="shajra.lease-operation-request",
            fields=release_fields,
        )
        if value["method"] != "release":
            _corrupt()
        return value


def deserialize_lease_operation_receipt(
    raw: str,
    request: CanonicalInput,
    keys: RedisKeyBuilder,
    scope: str,
    acquisition_id: str,
    request_nonce: str,
) -> LeaseOperationReceipt:
    request_value = _load_operation_request(request)
    value = _strict_object(
        raw, schema="shajra.lease-operation-result", fields=_OPERATION_RECEIPT_FIELDS
    )
    if request_value["domain"] == "GENERIC":
        domain: LeaseDomain = "GENERIC"
    elif request_value["domain"] == "GRAPH_COMMIT":
        domain = "GRAPH_COMMIT"
    else:
        _corrupt()
    repeated = (
        "method",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "request_nonce_hmac",
    )
    if value["input_sha256"] != request.sha256 or any(
        value[field] != request_value[field] for field in repeated
    ):
        _corrupt()
    if (
        value["scope_hmac"] != keys.scope_hmac(domain, scope)
        or value["acquisition_id_hmac"]
        != keys.acquisition_id_hmac(domain, acquisition_id)
        or value["request_nonce_hmac"]
        != keys.operation_nonce_hmac(domain, request_nonce)
    ):
        _corrupt()
    if request_value["method"] == "renew":
        operation_result: Lease | GraphLease | LeaseReleaseResult = (
            _parse_lease_payload(value["result"])
        )
        if (
            not isinstance(operation_result, (Lease, GraphLease))
            or operation_result.scope != scope
            or operation_result.acquisition_id != acquisition_id
        ):
            _corrupt()
        if (domain == "GENERIC" and type(operation_result) is not Lease) or (
            domain == "GRAPH_COMMIT" and type(operation_result) is not GraphLease
        ):
            _corrupt()
        requested_ttl = parse_canonical_decimal(
            request_value["requested_ttl_ms"], minimum=1
        )
        if operation_result.ttl_ms > requested_ttl:
            _corrupt()
    else:
        operation_result = _parse_release_payload(value["result"])
        if operation_result.acquisition_id != acquisition_id:
            _corrupt()
    receipt_expiry = parse_canonical_decimal(value["receipt_expires_at_ms"], minimum=1)
    if isinstance(operation_result, LeaseReleaseResult):
        expected_receipt_expiry = operation_result.released_at_ms + 60_000
    else:
        expected_receipt_expiry = (
            operation_result.expires_at_ms - operation_result.ttl_ms + 60_000
        )
    if receipt_expiry != expected_receipt_expiry:
        _corrupt()
    return LeaseOperationReceipt(
        request.sha256,
        operation_result,
        receipt_expiry,
    )


@dataclass(frozen=True, slots=True)
class AdminResultReceipt:
    input_sha256: str
    result: CoordinationAdminResult
    receipt_expires_at_ms: int


_ADMIN_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "input_sha256",
        "method",
        "scope_hmac",
        "request_nonce_hmac",
        "evidence_sha256",
        "expected_state_sha256",
        "result",
        "receipt_expires_at_ms",
    }
)
_ADMIN_RESULT_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "code",
        "previous_state_sha256",
        "state_sha256",
        "confirmed_revision",
        "fencing_floor",
    }
)


def _admin_result_payload(result: CoordinationAdminResult) -> dict[str, object]:
    if result.code not in {"ADMIN_INITIALIZED", "ADMIN_RECONCILED"}:
        _corrupt()
    return {
        "schema": "shajra.coordination-admin-transition",
        "version": 1,
        "code": result.code,
        "previous_state_sha256": _digest(result.previous_state_sha256),
        "state_sha256": _digest(result.state_sha256),
        "confirmed_revision": canonical_decimal(result.confirmed_revision, minimum=0),
        "fencing_floor": canonical_decimal(result.fencing_floor, minimum=1),
    }


def _parse_admin_result_payload(value: object) -> CoordinationAdminResult:
    item = _exact_nested(
        value,
        schema="shajra.coordination-admin-transition",
        fields=_ADMIN_RESULT_PAYLOAD_FIELDS,
    )
    code = item["code"]
    if code not in {"ADMIN_INITIALIZED", "ADMIN_RECONCILED"}:
        _corrupt()
    return CoordinationAdminResult(
        code,
        _digest(item["previous_state_sha256"]),
        _digest(item["state_sha256"]),
        parse_canonical_decimal(item["confirmed_revision"], minimum=0),
        parse_canonical_decimal(item["fencing_floor"], minimum=1),
    )


def serialize_admin_result_receipt(
    request: CanonicalInput,
    method: Literal["initialize", "reconcile"],
    keys: RedisKeyBuilder,
    scope: str,
    request_nonce: str,
    evidence_sha256: str,
    expected_state_sha256: str,
    result: CoordinationAdminResult,
    receipt_expires_at_ms: int,
) -> str:
    request_value = _load_expected_request(
        request,
        schema="shajra.coordination-admin-request",
        fields=_ADMIN_REQUEST_FIELDS,
    )
    expected = {
        "method": method,
        "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", scope),
        "evidence_sha256": _digest(evidence_sha256),
        "expected_state_sha256": _digest(expected_state_sha256),
    }
    if any(request_value[key] != value for key, value in expected.items()):
        _corrupt()
    return canonical_ascii_json(
        {
            "schema": "shajra.coordination-admin-result",
            "version": 1,
            "input_sha256": request.sha256,
            **expected,
            "request_nonce_hmac": keys.admin_nonce_hmac(request_nonce),
            "result": _admin_result_payload(result),
            "receipt_expires_at_ms": canonical_decimal(
                receipt_expires_at_ms, minimum=1
            ),
        }
    )


def deserialize_admin_result_receipt(
    raw: str,
    request: CanonicalInput,
    keys: RedisKeyBuilder,
    scope: str,
    request_nonce: str,
) -> AdminResultReceipt:
    request_value = _load_expected_request(
        request,
        schema="shajra.coordination-admin-request",
        fields=_ADMIN_REQUEST_FIELDS,
    )
    value = _strict_object(
        raw, schema="shajra.coordination-admin-result", fields=_ADMIN_RECEIPT_FIELDS
    )
    repeated = ("method", "scope_hmac", "evidence_sha256", "expected_state_sha256")
    if value["input_sha256"] != request.sha256 or any(
        value[field] != request_value[field] for field in repeated
    ):
        _corrupt()
    if value["scope_hmac"] != keys.scope_hmac("GRAPH_COMMIT", scope) or value[
        "request_nonce_hmac"
    ] != keys.admin_nonce_hmac(request_nonce):
        _corrupt()
    return AdminResultReceipt(
        request.sha256,
        _parse_admin_result_payload(value["result"]),
        parse_canonical_decimal(value["receipt_expires_at_ms"], minimum=1),
    )


@dataclass(frozen=True, slots=True)
class RevocationReceipt:
    input_sha256: str
    result: RevocationResult
    receipt_expires_at_ms: int


_REVOCATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "input_sha256",
        "jti_hmac",
        "token_expires_at_s",
        "leeway_s",
        "code",
        "revoked",
        "server_time_ms",
        "expires_at_ms",
        "receipt_expires_at_ms",
    }
)


def serialize_revocation_receipt(
    request: CanonicalInput,
    result: RevocationResult,
    jti_hmac: str,
    receipt_expires_at_ms: int,
) -> str:
    request_value = _load_expected_request(
        request,
        schema="shajra.revocation-request",
        fields=_REVOCATION_REQUEST_FIELDS,
    )
    if request_value["jti_hmac"] != _digest(jti_hmac):
        _corrupt()
    token_expiry = parse_canonical_decimal(
        request_value["token_expires_at_s"], minimum=0
    )
    leeway = parse_canonical_decimal(request_value["leeway_s"], minimum=0, maximum=300)
    expected_expiry = token_expiry * 1_000 + leeway * 1_000
    canonical_decimal(expected_expiry, minimum=0)
    if result.expires_at_ms != expected_expiry:
        _corrupt()
    if result.code not in {
        "REVOKED",
        "ALREADY_REVOKED",
        "NOT_REVOKED",
        "TOKEN_ALREADY_EXPIRED",
    }:
        _corrupt()
    if result.revoked != (result.code in {"REVOKED", "ALREADY_REVOKED"}):
        _corrupt()
    expected_receipt_expiry = max(expected_expiry, result.server_time_ms + 60_000)
    if receipt_expires_at_ms != expected_receipt_expiry:
        _corrupt()
    return canonical_ascii_json(
        {
            "schema": "shajra.revocation-result",
            "version": 1,
            "input_sha256": request.sha256,
            "jti_hmac": request_value["jti_hmac"],
            "token_expires_at_s": request_value["token_expires_at_s"],
            "leeway_s": request_value["leeway_s"],
            "code": result.code,
            "revoked": result.revoked,
            "server_time_ms": canonical_decimal(result.server_time_ms, minimum=0),
            "expires_at_ms": canonical_decimal(result.expires_at_ms, minimum=0),
            "receipt_expires_at_ms": canonical_decimal(
                receipt_expires_at_ms, minimum=0
            ),
        }
    )


def deserialize_revocation_receipt(
    raw: str, request: CanonicalInput
) -> RevocationReceipt:
    request_value = _load_expected_request(
        request,
        schema="shajra.revocation-request",
        fields=_REVOCATION_REQUEST_FIELDS,
    )
    value = _strict_object(
        raw, schema="shajra.revocation-result", fields=_REVOCATION_RECEIPT_FIELDS
    )
    repeated = ("jti_hmac", "token_expires_at_s", "leeway_s")
    if value["input_sha256"] != request.sha256 or any(
        value[field] != request_value[field] for field in repeated
    ):
        _corrupt()
    code = value["code"]
    if code not in {
        "REVOKED",
        "ALREADY_REVOKED",
        "NOT_REVOKED",
        "TOKEN_ALREADY_EXPIRED",
    } or not isinstance(value["revoked"], bool):
        _corrupt()
    result = RevocationResult(
        code,
        value["revoked"],
        parse_canonical_decimal(value["server_time_ms"], minimum=0),
        parse_canonical_decimal(value["expires_at_ms"], minimum=0),
    )
    token_expiry = parse_canonical_decimal(
        request_value["token_expires_at_s"], minimum=0
    )
    leeway = parse_canonical_decimal(request_value["leeway_s"], minimum=0, maximum=300)
    if result.expires_at_ms != token_expiry * 1_000 + leeway * 1_000:
        _corrupt()
    if result.revoked != (code in {"REVOKED", "ALREADY_REVOKED"}):
        _corrupt()
    receipt_expiry = parse_canonical_decimal(value["receipt_expires_at_ms"], minimum=0)
    if receipt_expiry != max(result.expires_at_ms, result.server_time_ms + 60_000):
        _corrupt()
    return RevocationReceipt(request.sha256, result, receipt_expiry)


@dataclass(frozen=True, slots=True)
class RateReceipt:
    input_sha256: str
    result: RateLimitResult
    receipt_expires_at_ms: int


@dataclass(frozen=True, slots=True)
class RevocationEntry:
    jti_hmac: str
    expires_at_ms: int


_REVOCATION_ENTRY_BODY_FIELDS = frozenset(
    {"schema", "version", "jti_hmac", "expires_at_ms"}
)
_REVOCATION_ENTRY_FIELDS = _REVOCATION_ENTRY_BODY_FIELDS | {"entry_sha256"}


def serialize_revocation_entry(jti_hmac: str, expires_at_ms: int) -> str:
    body = {
        "schema": "shajra.revocation-entry",
        "version": 1,
        "jti_hmac": _digest(jti_hmac),
        "expires_at_ms": canonical_decimal(expires_at_ms, minimum=0),
    }
    return canonical_ascii_json(
        {**body, "entry_sha256": sha256_ascii(canonical_ascii_json(body))}
    )


def deserialize_revocation_entry(raw: str) -> RevocationEntry:
    value = _strict_object(
        raw, schema="shajra.revocation-entry", fields=_REVOCATION_ENTRY_FIELDS
    )
    digest = _digest(value.pop("entry_sha256"))
    if sha256_ascii(canonical_ascii_json(value)) != digest:
        _corrupt()
    return RevocationEntry(
        _digest(value["jti_hmac"]),
        parse_canonical_decimal(value["expires_at_ms"], minimum=0),
    )


_RATE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "input_sha256",
        "policy_id",
        "subject_kind",
        "subject_hmac",
        "window_start_ms",
        "window_ms",
        "limit",
        "allowed",
        "observed_count",
        "remaining",
        "server_time_ms",
        "reset_at_ms",
        "retry_after_ms",
        "receipt_expires_at_ms",
    }
)


def serialize_rate_receipt(
    request: CanonicalInput,
    result: RateLimitResult,
    receipt_expires_at_ms: int,
) -> str:
    request_value = _load_expected_request(
        request, schema="shajra.rate-request", fields=_RATE_REQUEST_FIELDS
    )
    policy = RateLimitPolicyId(_nonempty_text(request_value["policy_id"]))
    window_start = parse_canonical_decimal(request_value["window_start_ms"], minimum=0)
    window = parse_canonical_decimal(request_value["window_ms"], minimum=1)
    limit = parse_canonical_decimal(request_value["limit"], minimum=1)
    reset = window_start + window
    canonical_decimal(reset, minimum=1)
    _validate_rate_result(result, policy, limit, window_start, reset)
    if receipt_expires_at_ms != reset + 60_000:
        _corrupt()
    return canonical_ascii_json(
        {
            "schema": "shajra.rate-result",
            "version": 1,
            "input_sha256": request.sha256,
            "policy_id": policy.value,
            "subject_kind": request_value["subject_kind"],
            "subject_hmac": request_value["subject_hmac"],
            "window_start_ms": request_value["window_start_ms"],
            "window_ms": request_value["window_ms"],
            "limit": request_value["limit"],
            "allowed": result.allowed,
            "observed_count": canonical_decimal(result.observed_count, minimum=1),
            "remaining": canonical_decimal(result.remaining, minimum=0),
            "server_time_ms": canonical_decimal(result.server_time_ms, minimum=0),
            "reset_at_ms": canonical_decimal(result.reset_at_ms, minimum=1),
            "retry_after_ms": canonical_decimal(result.retry_after_ms, minimum=0),
            "receipt_expires_at_ms": canonical_decimal(
                receipt_expires_at_ms, minimum=1
            ),
        }
    )


def _validate_rate_result(
    result: RateLimitResult,
    policy: RateLimitPolicyId,
    limit: int,
    window_start: int,
    reset: int,
) -> None:
    if (
        result.policy is not policy
        or result.limit != limit
        or result.reset_at_ms != reset
        or not window_start <= result.server_time_ms < reset
        or result.observed_count < 1
        or result.remaining != max(limit - result.observed_count, 0)
        or result.allowed != (result.observed_count <= limit)
    ):
        _corrupt()
    expected_retry = 0 if result.allowed else reset - result.server_time_ms
    if result.retry_after_ms != expected_retry:
        _corrupt()


def deserialize_rate_receipt(raw: str, request: CanonicalInput) -> RateReceipt:
    request_value = _load_expected_request(
        request, schema="shajra.rate-request", fields=_RATE_REQUEST_FIELDS
    )
    value = _strict_object(
        raw, schema="shajra.rate-result", fields=_RATE_RECEIPT_FIELDS
    )
    repeated = (
        "policy_id",
        "subject_kind",
        "subject_hmac",
        "window_start_ms",
        "window_ms",
        "limit",
    )
    if value["input_sha256"] != request.sha256 or any(
        value[field] != request_value[field] for field in repeated
    ):
        _corrupt()
    if not isinstance(value["allowed"], bool):
        _corrupt()
    try:
        policy = RateLimitPolicyId(_nonempty_text(value["policy_id"]))
    except ValueError:
        _corrupt()
    limit = parse_canonical_decimal(value["limit"], minimum=1)
    window_start = parse_canonical_decimal(value["window_start_ms"], minimum=0)
    window = parse_canonical_decimal(value["window_ms"], minimum=1)
    reset = window_start + window
    result = RateLimitResult(
        policy,
        value["allowed"],
        limit,
        parse_canonical_decimal(value["observed_count"], minimum=1),
        parse_canonical_decimal(value["remaining"], minimum=0),
        parse_canonical_decimal(value["server_time_ms"], minimum=0),
        parse_canonical_decimal(value["reset_at_ms"], minimum=1),
        parse_canonical_decimal(value["retry_after_ms"], minimum=0),
    )
    _validate_rate_result(result, policy, limit, window_start, reset)
    receipt_expiry = parse_canonical_decimal(value["receipt_expires_at_ms"], minimum=1)
    if receipt_expiry != reset + 60_000:
        _corrupt()
    return RateReceipt(request.sha256, result, receipt_expiry)


_LEASE_ACQUIRE_GENERIC_FIELDS = frozenset(
    {
        "schema",
        "version",
        "domain",
        "scope_hmac",
        "acquisition_id_hmac",
        "requested_ttl_ms",
    }
)
_LEASE_ACQUIRE_GRAPH_FIELDS = _LEASE_ACQUIRE_GENERIC_FIELDS | {"committed_revision"}


_PERMIT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "scope",
        "operation_id",
        "revision",
        "fencing_token",
        "permit_id",
        "commit_sha256",
    }
)


def _permit_payload(permit: CommitPermit) -> dict[str, object]:
    operation_id = _nonempty_text(permit.operation_id)
    permit_id = _nonempty_text(permit.permit_id)
    if not operation_id.startswith("op_") or operation_id == "op_":
        _corrupt()
    if not permit_id.startswith("cpr_") or permit_id == "cpr_":
        _corrupt()
    return {
        "schema": "shajra.commit-permit",
        "version": 1,
        "scope": _nonempty_text(permit.scope),
        "operation_id": operation_id,
        "revision": canonical_decimal(permit.revision, minimum=1),
        "fencing_token": canonical_decimal(permit.fencing_token, minimum=1),
        "permit_id": permit_id,
        "commit_sha256": _digest(permit.commit_sha256),
    }


def _parse_permit_payload(value: object) -> CommitPermit:
    item = _exact_nested(value, schema="shajra.commit-permit", fields=_PERMIT_FIELDS)
    operation_id = _nonempty_text(item["operation_id"])
    permit_id = _nonempty_text(item["permit_id"])
    if not operation_id.startswith("op_") or operation_id == "op_":
        _corrupt()
    if not permit_id.startswith("cpr_") or permit_id == "cpr_":
        _corrupt()
    return CommitPermit(
        _nonempty_text(item["scope"]),
        OperationId(operation_id),
        parse_canonical_decimal(item["revision"], minimum=1),
        parse_canonical_decimal(item["fencing_token"], minimum=1),
        permit_id,
        _digest(item["commit_sha256"]),
    )


_GRAPH_COMMIT_FIELDS = frozenset(
    {
        "operation_id",
        "revision",
        "fencing_token",
        "permit_id",
        "semantic_checksum",
        "committed_at",
    }
)


def _parse_graph_commit_json(raw: str) -> GraphCommit:
    try:
        raw.encode("ascii")
        value = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _value: _corrupt(),
        )
        if not isinstance(value, dict) or set(value) != _GRAPH_COMMIT_FIELDS:
            _corrupt()
        if canonical_ascii_json(value) != raw:
            _corrupt()
        operation_id = _nonempty_text(value["operation_id"])
        permit_id = _nonempty_text(value["permit_id"])
        if not operation_id.startswith("op_") or operation_id == "op_":
            _corrupt()
        if not permit_id.startswith("cpr_") or permit_id == "cpr_":
            _corrupt()
        if (
            type(value["revision"]) is not int
            or type(value["fencing_token"]) is not int
        ):
            _corrupt()
        committed_at_text = _nonempty_text(value["committed_at"])
        if not committed_at_text.endswith("Z"):
            _corrupt()
        committed_at = datetime.fromisoformat(committed_at_text[:-1] + "+00:00")
        commit = GraphCommit(
            OperationId(operation_id),
            value["revision"],
            value["fencing_token"],
            permit_id,
            _digest(value["semantic_checksum"]),
            committed_at,
        )
        if canonical_graph_commit_json(commit) != raw:
            _corrupt()
        return commit
    except CoordinationError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        _corrupt()


def _validate_commit_bundle(
    scope: str,
    permit: CommitPermit,
    commit: GraphCommit,
    commit_digest: str,
    staged: StagedWriteReceipt,
) -> None:
    digest = graph_commit_sha256(commit)
    if (
        permit.scope != scope
        or permit.operation_id != commit.operation_id
        or permit.revision != commit.revision
        or permit.fencing_token != commit.fencing_token
        or permit.permit_id != commit.permit_id
        or permit.commit_sha256 != digest
        or _digest(commit_digest) != digest
        or staged.operation_id != commit.operation_id
        or staged.revision != commit.revision
        or staged.fencing_token != commit.fencing_token
    ):
        _corrupt()


_RESERVATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "state",
        "scope_hmac",
        "permit",
        "commit_json",
        "commit_sha256",
        "staged_write_receipt_json",
        "staged_write_receipt_sha256",
        "authorization_request_nonce_hmac",
    }
)


def serialize_commit_reservation(
    reservation: CommitReservation,
    keys: RedisKeyBuilder,
    authorization_request_nonce: str,
) -> str:
    if reservation.state != "COMMITTING":
        _corrupt()
    _validate_commit_bundle(
        reservation.scope,
        reservation.permit,
        reservation.commit,
        reservation.commit_sha256,
        reservation.staged_write_receipt,
    )
    staged_json = serialize_staged_write_receipt(reservation.staged_write_receipt)
    return canonical_ascii_json(
        {
            "schema": "shajra.commit-reservation",
            "version": 1,
            "state": "COMMITTING",
            "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", reservation.scope),
            "permit": _permit_payload(reservation.permit),
            "commit_json": canonical_graph_commit_json(reservation.commit),
            "commit_sha256": reservation.commit_sha256,
            "staged_write_receipt_json": staged_json,
            "staged_write_receipt_sha256": sha256_ascii(staged_json),
            "authorization_request_nonce_hmac": keys.authorization_nonce_hmac(
                authorization_request_nonce
            ),
        }
    )


def deserialize_commit_reservation(
    raw: str, keys: RedisKeyBuilder, scope: str
) -> CommitReservation:
    value = _strict_object(
        raw, schema="shajra.commit-reservation", fields=_RESERVATION_FIELDS
    )
    if value["state"] != "COMMITTING" or value["scope_hmac"] != keys.scope_hmac(
        "GRAPH_COMMIT", scope
    ):
        _corrupt()
    permit = _parse_permit_payload(value["permit"])
    commit_json = _nonempty_text(value["commit_json"], maximum=100_000)
    commit = _parse_graph_commit_json(commit_json)
    commit_digest = _digest(value["commit_sha256"])
    if graph_commit_sha256(commit) != commit_digest:
        _corrupt()
    staged_json = _nonempty_text(value["staged_write_receipt_json"], maximum=2_000_000)
    if sha256_ascii(staged_json) != _digest(value["staged_write_receipt_sha256"]):
        _corrupt()
    staged = deserialize_staged_write_receipt(staged_json)
    _digest(value["authorization_request_nonce_hmac"])
    _validate_commit_bundle(scope, permit, commit, commit_digest, staged)
    return CommitReservation(scope, "COMMITTING", permit, commit, commit_digest, staged)


_CONFIRMED_FIELDS = frozenset(
    {
        "schema",
        "version",
        "scope_hmac",
        "permit",
        "commit_json",
        "commit_sha256",
        "staged_write_receipt_json",
        "staged_write_receipt_sha256",
    }
)


def serialize_confirmed_commit_receipt(
    receipt: ConfirmedCommitReceipt, keys: RedisKeyBuilder
) -> str:
    _validate_commit_bundle(
        receipt.scope,
        receipt.permit,
        receipt.commit,
        receipt.commit_sha256,
        receipt.staged_write_receipt,
    )
    staged_json = serialize_staged_write_receipt(receipt.staged_write_receipt)
    return canonical_ascii_json(
        {
            "schema": "shajra.confirmed-commit-receipt",
            "version": 1,
            "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", receipt.scope),
            "permit": _permit_payload(receipt.permit),
            "commit_json": canonical_graph_commit_json(receipt.commit),
            "commit_sha256": receipt.commit_sha256,
            "staged_write_receipt_json": staged_json,
            "staged_write_receipt_sha256": sha256_ascii(staged_json),
        }
    )


def deserialize_confirmed_commit_receipt(
    raw: str, keys: RedisKeyBuilder, scope: str
) -> ConfirmedCommitReceipt:
    value = _strict_object(
        raw, schema="shajra.confirmed-commit-receipt", fields=_CONFIRMED_FIELDS
    )
    if value["scope_hmac"] != keys.scope_hmac("GRAPH_COMMIT", scope):
        _corrupt()
    permit = _parse_permit_payload(value["permit"])
    commit = _parse_graph_commit_json(
        _nonempty_text(value["commit_json"], maximum=100_000)
    )
    commit_digest = _digest(value["commit_sha256"])
    staged_json = _nonempty_text(value["staged_write_receipt_json"], maximum=2_000_000)
    if sha256_ascii(staged_json) != _digest(value["staged_write_receipt_sha256"]):
        _corrupt()
    staged = deserialize_staged_write_receipt(staged_json)
    _validate_commit_bundle(scope, permit, commit, commit_digest, staged)
    return ConfirmedCommitReceipt(scope, permit, commit, commit_digest, staged)


_RECONCILED_BODY_FIELDS = frozenset(
    {
        "schema",
        "version",
        "scope_hmac",
        "revision",
        "semantic_checksum",
        "head_commit_sha256",
        "evidence_sha256",
        "admin_request_nonce_hmac",
    }
)
_RECONCILED_FIELDS = _RECONCILED_BODY_FIELDS | {"proof_sha256"}


def _reconciled_body(
    receipt: ReconciledHeadReceipt, keys: RedisKeyBuilder
) -> dict[str, object]:
    revision = canonical_decimal(receipt.revision, minimum=0)
    head_digest = receipt.head_commit_sha256
    if (receipt.revision == 0 and head_digest is not None) or (
        receipt.revision > 0 and head_digest is None
    ):
        _corrupt()
    if head_digest is not None:
        head_digest = _digest(head_digest)
    return {
        "schema": "shajra.reconciled-head-receipt",
        "version": 1,
        "scope_hmac": keys.scope_hmac("GRAPH_COMMIT", receipt.scope),
        "revision": revision,
        "semantic_checksum": _digest(receipt.semantic_checksum),
        "head_commit_sha256": head_digest,
        "evidence_sha256": _digest(receipt.evidence_sha256),
        "admin_request_nonce_hmac": _digest(receipt.admin_request_nonce_hmac),
    }


def serialize_reconciled_head_receipt(
    receipt: ReconciledHeadReceipt, keys: RedisKeyBuilder
) -> str:
    body = _reconciled_body(receipt, keys)
    return canonical_ascii_json(
        {**body, "proof_sha256": sha256_ascii(canonical_ascii_json(body))}
    )


def deserialize_reconciled_head_receipt(
    raw: str, keys: RedisKeyBuilder, scope: str
) -> ReconciledHeadReceipt:
    value = _strict_object(
        raw, schema="shajra.reconciled-head-receipt", fields=_RECONCILED_FIELDS
    )
    proof_digest = _digest(value.pop("proof_sha256"))
    if sha256_ascii(canonical_ascii_json(value)) != proof_digest:
        _corrupt()
    if value["scope_hmac"] != keys.scope_hmac("GRAPH_COMMIT", scope):
        _corrupt()
    revision = parse_canonical_decimal(value["revision"], minimum=0)
    head_digest = value["head_commit_sha256"]
    if head_digest is not None:
        head_digest = _digest(head_digest)
    if (revision == 0 and head_digest is not None) or (
        revision > 0 and head_digest is None
    ):
        _corrupt()
    return ReconciledHeadReceipt(
        scope,
        revision,
        _digest(value["semantic_checksum"]),
        head_digest,
        _digest(value["evidence_sha256"]),
        _digest(value["admin_request_nonce_hmac"]),
    )


_EVIDENCE_BODY_FIELDS = frozenset(
    {
        "schema",
        "version",
        "scope",
        "committed_head_revision",
        "committed_head_semantic_checksum",
        "committed_head_commit_sha256",
        "max_durable_fencing_token",
        "fencing_floor",
    }
)
_EVIDENCE_FIELDS = _EVIDENCE_BODY_FIELDS | {"evidence_sha256"}


def _evidence_body(
    scope: str,
    committed_head_revision: int,
    semantic_checksum: str,
    head_commit_sha256: str | None,
    max_durable_fencing_token: int,
    fencing_floor: int,
) -> dict[str, object]:
    if (committed_head_revision == 0 and head_commit_sha256 is not None) or (
        committed_head_revision > 0 and head_commit_sha256 is None
    ):
        _corrupt()
    if fencing_floor <= max_durable_fencing_token:
        _corrupt()
    head_digest = (
        _digest(head_commit_sha256) if head_commit_sha256 is not None else None
    )
    return {
        "schema": "shajra.coordination-evidence",
        "version": 1,
        "scope": _nonempty_text(scope),
        "committed_head_revision": canonical_decimal(
            committed_head_revision, minimum=0
        ),
        "committed_head_semantic_checksum": _digest(semantic_checksum),
        "committed_head_commit_sha256": head_digest,
        "max_durable_fencing_token": canonical_decimal(
            max_durable_fencing_token, minimum=0
        ),
        "fencing_floor": canonical_decimal(fencing_floor, minimum=1),
    }


def coordination_evidence_sha256(
    scope: str,
    committed_head_revision: int,
    semantic_checksum: str,
    head_commit_sha256: str | None,
    max_durable_fencing_token: int,
    fencing_floor: int,
) -> str:
    return sha256_ascii(
        canonical_ascii_json(
            _evidence_body(
                scope,
                committed_head_revision,
                semantic_checksum,
                head_commit_sha256,
                max_durable_fencing_token,
                fencing_floor,
            )
        )
    )


def serialize_coordination_evidence(evidence: CoordinationEvidence) -> str:
    body = _evidence_body(
        evidence.scope,
        evidence.committed_head_revision,
        evidence.committed_head_semantic_checksum,
        evidence.committed_head_commit_sha256,
        evidence.max_durable_fencing_token,
        evidence.fencing_floor,
    )
    digest = sha256_ascii(canonical_ascii_json(body))
    if digest != _digest(evidence.evidence_sha256):
        _corrupt()
    return canonical_ascii_json({**body, "evidence_sha256": digest})


def deserialize_coordination_evidence(raw: str) -> CoordinationEvidence:
    value = _strict_object(
        raw, schema="shajra.coordination-evidence", fields=_EVIDENCE_FIELDS
    )
    digest = _digest(value.pop("evidence_sha256"))
    if sha256_ascii(canonical_ascii_json(value)) != digest:
        _corrupt()
    revision = parse_canonical_decimal(value["committed_head_revision"], minimum=0)
    head_digest = value["committed_head_commit_sha256"]
    if head_digest is not None:
        head_digest = _digest(head_digest)
    max_token = parse_canonical_decimal(value["max_durable_fencing_token"], minimum=0)
    floor = parse_canonical_decimal(value["fencing_floor"], minimum=1)
    evidence = CoordinationEvidence(
        _nonempty_text(value["scope"]),
        revision,
        _digest(value["committed_head_semantic_checksum"]),
        head_digest,
        max_token,
        floor,
        digest,
    )
    _evidence_body(
        evidence.scope,
        evidence.committed_head_revision,
        evidence.committed_head_semantic_checksum,
        evidence.committed_head_commit_sha256,
        evidence.max_durable_fencing_token,
        evidence.fencing_floor,
    )
    return evidence


def coordination_state_sha256(raw_values: tuple[str | None, ...]) -> str:
    if not isinstance(raw_values, tuple) or len(raw_values) != 5:
        _corrupt()
    values: list[dict[str, object]] = []
    for value in raw_values:
        if value is None:
            values.append({"present": False})
        elif isinstance(value, str):
            values.append({"present": True, "value": value})
        else:
            _corrupt()
    return sha256_ascii(
        canonical_ascii_json(
            {
                "schema": "shajra.coordination-state-raw",
                "version": 1,
                "values": values,
            }
        )
    )
