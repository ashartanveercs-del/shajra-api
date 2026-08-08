"""Integration tests that execute the exact shipped Lua scripts in-process."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import pytest

from coordination.protocols import (
    CommitReservation,
    CoordinationEvidence,
    CoordinationError,
    GraphLease,
    IpRateLimitSubject,
    RateLimitPolicyId,
    ReconciledHeadReceipt,
)
from coordination.serialization import (
    RedisKeyBuilder,
    coordination_evidence_sha256,
    lease_acquire_request,
    rate_request,
    revocation_request,
    serialize_commit_reservation,
    serialize_generic_lock,
    serialize_graph_lock,
    serialize_reconciled_head_receipt,
    serialize_revocation_entry,
)
from coordination.upstash import (
    AUTHORIZE_COMMIT_LUA,
    CONFIRM_COMMIT_LUA,
    COORDINATION_ADMIN_LUA,
    COORDINATION_INSPECT_LUA,
    COORDINATION_STATUS_LUA,
    GENERIC_ACQUIRE_LUA,
    GRAPH_ACQUIRE_LUA,
    LEASE_ASSERT_LUA,
    LEASE_RELEASE_LUA,
    LEASE_RENEW_LUA,
    RATE_CONSUME_LUA,
    RATE_TIME_LUA,
    REVOCATION_CHECK_LUA,
    REVOCATION_REVOKE_LUA,
    UpstashCommitCoordinator,
    UpstashCoordinationAdmin,
    UpstashLeaseManager,
    UpstashRateLimiter,
    UpstashRevocationStore,
)
from domain.ids import OperationId
from repositories import (
    CommitPermit,
    GraphCommit,
    GraphWriteSet,
    StagedWriteReceipt,
    canonical_graph_write_set_json,
    canonical_graph_commit_json,
    graph_commit_sha256,
)
from repositories.protocols import graph_write_set_sha256


class ProductionLuaHarness(Protocol):
    executed_scripts: list[str]
    client: Any
    command_overrides: dict[str, list[Any]]
    before_eval: Callable[[str, Sequence[str], Sequence[str]], None] | None

    def eval(
        self,
        script: str,
        keys: list[str],
        args: list[str],
        *,
        nonce_idempotent: bool,
    ) -> list[Any]: ...


def test_harness_executes_the_exact_production_lua_string(
    production_lua: ProductionLuaHarness,
) -> None:
    result = production_lua.eval(RATE_TIME_LUA, [], [], nonce_idempotent=False)

    assert result[:2] == ["OK", "TIME"]
    assert production_lua.executed_scripts == [RATE_TIME_LUA]


def _seed_ready_graph(
    production_lua: ProductionLuaHarness,
    keys: RedisKeyBuilder,
    *,
    scope: str = "family",
    revision: int = 0,
    fence: int = 1,
) -> None:
    proof = ReconciledHeadReceipt(
        scope,
        revision,
        "a" * 64,
        None if revision == 0 else "b" * 64,
        "c" * 64,
        "d" * 64,
    )
    production_lua.client.mset(
        {
            keys.graph_confirmed_revision(scope): str(revision),
            keys.graph_fence(scope): str(fence),
            keys.graph_last_confirmation(scope): serialize_reconciled_head_receipt(
                proof, keys
            ),
        }
    )


def _commit_values(lease: GraphLease, revision: int = 1):
    write_set = GraphWriteSet()
    staged = StagedWriteReceipt(
        OperationId("op_actual_lua"),
        revision,
        lease.fencing_token,
        canonical_graph_write_set_json(write_set),
        graph_write_set_sha256(write_set),
    )
    commit = GraphCommit(
        staged.operation_id,
        revision,
        lease.fencing_token,
        "cpr_actual_lua",
        "e" * 64,
        datetime(2026, 8, 8, 10, 0, revision, tzinfo=UTC),
    )
    return commit, staged


def _graph_snapshot(
    production_lua: ProductionLuaHarness, keys: RedisKeyBuilder, scope: str
) -> tuple[tuple[str | None, int], ...]:
    graph_keys = (
        keys.graph_lock(scope),
        keys.graph_fence(scope),
        keys.graph_confirmed_revision(scope),
        keys.graph_reservation(scope),
        keys.graph_last_confirmation(scope),
    )
    return tuple(
        (production_lua.client.get(key), production_lua.client.pexpiretime(key))
        for key in graph_keys
    )


def _database_snapshot(
    production_lua: ProductionLuaHarness,
) -> tuple[tuple[str, str | None, int], ...]:
    return tuple(
        (
            key,
            production_lua.client.get(key),
            production_lua.client.pexpiretime(key),
        )
        for key in sorted(production_lua.client.keys("*"))
    )


def _core_keys(keys: RedisKeyBuilder, scope: str = "family") -> list[str]:
    return [
        keys.graph_lock(scope),
        keys.graph_fence(scope),
        keys.graph_confirmed_revision(scope),
        keys.graph_reservation(scope),
        keys.graph_last_confirmation(scope),
    ]


def _expected_core_args(
    production_lua: ProductionLuaHarness, graph_keys: Sequence[str]
) -> list[str]:
    return [
        value
        if (value := production_lua.client.get(key)) is not None
        else "__SHAJRA_MISSING_V1__"
        for key in graph_keys
    ]


def _reservation_values(
    keys: RedisKeyBuilder,
    lease: GraphLease,
    *,
    revision: int,
    nonce: str,
) -> tuple[CommitPermit, GraphCommit, str]:
    commit, staged = _commit_values(lease, revision)
    digest = graph_commit_sha256(commit)
    permit = CommitPermit(
        lease.scope,
        commit.operation_id,
        commit.revision,
        commit.fencing_token,
        commit.permit_id,
        digest,
    )
    reservation = CommitReservation(
        lease.scope, "COMMITTING", permit, commit, digest, staged
    )
    return permit, commit, serialize_commit_reservation(reservation, keys, nonce)


def _redis_time_ms(production_lua: ProductionLuaHarness) -> int:
    result = production_lua.eval(RATE_TIME_LUA, [], [], nonce_idempotent=False)
    return int(result[2])


@pytest.mark.parametrize(
    "corruption",
    (
        "permit-operation",
        "permit-revision",
        "permit-fence",
        "permit-id",
        "permit-commit-digest",
        "commit-operation",
        "commit-revision",
        "commit-fence",
        "commit-permit-id",
        "commit-semantic-digest",
        "commit-envelope-digest",
        "staged-operation",
        "staged-revision",
        "staged-fence",
        "staged-write-set-digest",
        "staged-envelope-digest",
        "scope-hmac",
        "authorization-nonce-hmac",
    ),
)
def test_confirmation_rejects_corrupt_nested_recovery_data_without_mutation(
    production_lua: ProductionLuaHarness,
    corruption: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "acq-confirm-corrupt")
    commit, staged = _commit_values(lease)
    permit = coordinator.authorize_commit(
        lease, commit, staged, "authorize-confirm-corrupt"
    )
    reservation_key = keys.graph_reservation("family")
    reservation = json.loads(production_lua.client.get(reservation_key))
    if corruption.startswith("permit-"):
        if corruption == "permit-operation":
            reservation["permit"]["operation_id"] = "op_changed"
        elif corruption == "permit-revision":
            reservation["permit"]["revision"] = "2"
        elif corruption == "permit-fence":
            reservation["permit"]["fencing_token"] = str(lease.fencing_token + 1)
        elif corruption == "permit-id":
            reservation["permit"]["permit_id"] = "cpr_changed"
        else:
            reservation["permit"]["commit_sha256"] = "f" * 64
    elif corruption.startswith("commit-") and corruption != "commit-envelope-digest":
        commit_payload = json.loads(reservation["commit_json"])
        if corruption == "commit-operation":
            commit_payload["operation_id"] = "op_changed"
        elif corruption == "commit-revision":
            commit_payload["revision"] = 2
        elif corruption == "commit-fence":
            commit_payload["fencing_token"] = lease.fencing_token + 1
        elif corruption == "commit-permit-id":
            commit_payload["permit_id"] = "cpr_changed"
        else:
            commit_payload["semantic_checksum"] = "f" * 64
        reservation["commit_json"] = json.dumps(
            commit_payload, sort_keys=True, separators=(",", ":")
        )
    elif corruption == "commit-envelope-digest":
        reservation["commit_sha256"] = "f" * 64
    elif corruption == "scope-hmac":
        reservation["scope_hmac"] = "f" * 64
    elif corruption == "authorization-nonce-hmac":
        reservation["authorization_request_nonce_hmac"] = "not-an-hmac"
    else:
        staged_envelope = json.loads(reservation["staged_write_receipt_json"])
        if corruption == "staged-operation":
            staged_envelope["operation_id"] = "op_changed"
        elif corruption == "staged-revision":
            staged_envelope["revision"] = "2"
        elif corruption == "staged-fence":
            staged_envelope["fencing_token"] = str(lease.fencing_token + 1)
        elif corruption == "staged-write-set-digest":
            staged_envelope["write_set_sha256"] = "f" * 64
        staged_raw = json.dumps(staged_envelope, sort_keys=True, separators=(",", ":"))
        reservation["staged_write_receipt_json"] = staged_raw
        reservation["staged_write_receipt_sha256"] = (
            "f" * 64
            if corruption == "staged-envelope-digest"
            else hashlib.sha256(staged_raw.encode("ascii")).hexdigest()
        )
    malformed_raw = json.dumps(reservation, sort_keys=True, separators=(",", ":"))
    production_lua.client.set(reservation_key, malformed_raw)
    before = _graph_snapshot(production_lua, keys, "family")

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.confirm_commit(permit, commit, "confirm-corrupt")

    assert _graph_snapshot(production_lua, keys, "family") == before


def test_confirmation_rejects_core_cas_race_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "acq-confirm-race")
    commit, staged = _commit_values(lease)
    permit = coordinator.authorize_commit(lease, commit, staged, "authorize-race")
    raced_state: list[tuple[tuple[str | None, int], ...]] = []

    def race(script: str, _keys: Sequence[str], _args: Sequence[str]) -> None:
        if script != CONFIRM_COMMIT_LUA:
            return
        production_lua.before_eval = None
        production_lua.client.set(
            keys.graph_fence("family"), str(lease.fencing_token + 1)
        )
        raced_state.append(_graph_snapshot(production_lua, keys, "family"))

    production_lua.before_eval = race

    with pytest.raises(CoordinationError, match="CONFIRMATION_CONFLICT"):
        coordinator.confirm_commit(permit, commit, "confirm-race")

    assert _graph_snapshot(production_lua, keys, "family") == raced_state[0]


def test_graph_acquire_rejects_missing_proof_without_any_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    production_lua.client.delete(keys.graph_last_confirmation("family"))
    before = _database_snapshot(production_lua)
    coordinator = UpstashCommitCoordinator(production_lua, keys)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.acquire("family", 0, "acq-missing-proof")

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("missing", ("fence", "proof"))
def test_authorization_rejects_torn_core_without_any_mutation(
    production_lua: ProductionLuaHarness,
    missing: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-missing-{missing}")
    commit, staged = _commit_values(lease)
    production_lua.client.delete(
        keys.graph_fence("family")
        if missing == "fence"
        else keys.graph_last_confirmation("family")
    )
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.authorize_commit(lease, commit, staged, f"authorize-{missing}")

    assert _database_snapshot(production_lua) == before


def test_renew_then_release_nonce_reuse_is_a_conflict_in_production_lua(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-renew-release")
    renewed = manager.renew(lease, "shared-operation-nonce")
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        manager.release(renewed, "shared-operation-nonce")

    assert _database_snapshot(production_lua) == before


def test_release_then_renew_nonce_reuse_is_a_conflict_in_production_lua(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-release-renew")
    manager.release(lease, "shared-operation-nonce")
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="NONCE_REUSE_CONFLICT"):
        manager.renew(lease, "shared-operation-nonce")

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("invalid", ("n-plus-two", "nested-staged-identity"))
def test_authorization_lua_rejects_invalid_commit_sequence_without_mutation(
    production_lua: ProductionLuaHarness,
    invalid: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-authorize-{invalid}")
    revision = 2 if invalid == "n-plus-two" else 1
    _permit, _commit, proposed_raw = _reservation_values(
        keys, lease, revision=revision, nonce=f"authorize-{invalid}"
    )
    if invalid == "nested-staged-identity":
        proposed = json.loads(proposed_raw)
        staged = json.loads(proposed["staged_write_receipt_json"])
        staged["operation_id"] = "op_changed"
        staged_raw = json.dumps(staged, sort_keys=True, separators=(",", ":"))
        proposed["staged_write_receipt_json"] = staged_raw
        proposed["staged_write_receipt_sha256"] = hashlib.sha256(
            staged_raw.encode("ascii")
        ).hexdigest()
        proposed_raw = json.dumps(proposed, sort_keys=True, separators=(",", ":"))
    graph_keys = _core_keys(keys)
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        AUTHORIZE_COMMIT_LUA,
        graph_keys,
        [
            "family",
            proposed_raw,
            serialize_graph_lock(lease, keys),
            *_expected_core_args(production_lua, graph_keys),
        ],
        nonce_idempotent=True,
    )

    expected = (
        ["ERR", "RESERVATION_CONFLICT"]
        if invalid == "n-plus-two"
        else ["ERR", "COORDINATION_STATE_CORRUPT"]
    )
    assert result == expected
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("invalid", ("n-plus-two", "nested-staged-identity"))
def test_confirmation_lua_rejects_invalid_commit_sequence_without_mutation(
    production_lua: ProductionLuaHarness,
    invalid: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-confirm-{invalid}")
    revision = 2 if invalid == "n-plus-two" else 1
    permit, commit, reservation_raw = _reservation_values(
        keys, lease, revision=revision, nonce=f"authorize-confirm-{invalid}"
    )
    if invalid == "nested-staged-identity":
        reservation = json.loads(reservation_raw)
        staged = json.loads(reservation["staged_write_receipt_json"])
        staged["operation_id"] = "op_changed"
        staged_raw = json.dumps(staged, sort_keys=True, separators=(",", ":"))
        reservation["staged_write_receipt_json"] = staged_raw
        reservation["staged_write_receipt_sha256"] = hashlib.sha256(
            staged_raw.encode("ascii")
        ).hexdigest()
        reservation_raw = json.dumps(reservation, sort_keys=True, separators=(",", ":"))
    production_lua.client.set(keys.graph_reservation("family"), reservation_raw)
    graph_keys = _core_keys(keys)
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        CONFIRM_COMMIT_LUA,
        graph_keys,
        [
            permit.scope,
            str(permit.operation_id),
            str(permit.revision),
            str(permit.fencing_token),
            permit.permit_id,
            permit.commit_sha256,
            canonical_graph_commit_json(commit),
            keys.hmac_hex("graph-confirmation-nonce", f"confirm-{invalid}"),
            "0",
            *_expected_core_args(production_lua, graph_keys),
        ],
        nonce_idempotent=True,
    )

    expected = (
        ["ERR", "CONFIRMATION_CONFLICT"]
        if invalid == "n-plus-two"
        else ["ERR", "COORDINATION_STATE_CORRUPT"]
    )
    assert result == expected
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("domain", ("generic", "graph"))
@pytest.mark.parametrize("applied_pttl", (0, -1, -2, 15_001))
def test_acquire_rolls_back_every_invalid_applied_pttl(
    production_lua: ProductionLuaHarness,
    domain: str,
    applied_pttl: int,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    if domain == "graph":
        _seed_ready_graph(production_lua, keys)
        manager: UpstashCommitCoordinator | UpstashLeaseManager = (
            UpstashCommitCoordinator(production_lua, keys)
        )
    else:
        manager = UpstashLeaseManager(production_lua, keys)
    before = _database_snapshot(production_lua)
    production_lua.command_overrides["PTTL"] = [applied_pttl]

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        if domain == "graph":
            manager.acquire("family", 0, f"acq-pttl-{applied_pttl}")  # type: ignore[call-arg]
        else:
            manager.acquire("enrichment", f"acq-pttl-{applied_pttl}")  # type: ignore[call-arg]

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("applied_pttl", (0, -1, -2, 15_001))
def test_renew_rolls_back_every_invalid_applied_pttl(
    production_lua: ProductionLuaHarness,
    applied_pttl: int,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", f"acq-renew-pttl-{applied_pttl}")
    before = _database_snapshot(production_lua)
    production_lua.command_overrides["PTTL"] = [None, applied_pttl]

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        manager.renew(lease, f"renew-pttl-{applied_pttl}")

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("operation", ("generic-acquire", "graph-acquire", "renew"))
def test_lease_mutation_rolls_back_if_keepttl_recreates_an_expired_key(
    production_lua: ProductionLuaHarness,
    operation: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    if operation == "graph-acquire":
        _seed_ready_graph(production_lua, keys)
        manager: UpstashCommitCoordinator | UpstashLeaseManager = (
            UpstashCommitCoordinator(production_lua, keys)
        )
        before = _database_snapshot(production_lua)
        production_lua.command_overrides["PTTL"] = [15_000, -1]
        action = lambda: manager.acquire("family", 0, "acq-keepttl-race")  # type: ignore[call-arg]
    elif operation == "generic-acquire":
        manager = UpstashLeaseManager(production_lua, keys)
        before = _database_snapshot(production_lua)
        production_lua.command_overrides["PTTL"] = [15_000, -1]
        action = lambda: manager.acquire("enrichment", "acq-keepttl-race")  # type: ignore[call-arg]
    else:
        manager = UpstashLeaseManager(production_lua, keys)
        lease = manager.acquire("enrichment", "acq-renew-keepttl-race")
        before = _database_snapshot(production_lua)
        production_lua.command_overrides["PTTL"] = [None, 15_000, -1]
        action = lambda: manager.renew(lease, "renew-keepttl-race")  # type: ignore[assignment]

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        action()

    assert _database_snapshot(production_lua) == before


def test_renew_rejects_a_live_lock_after_its_absolute_deadline(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-late-renewal")
    now_ms = _redis_time_ms(production_lua)
    late_lease = replace(
        lease,
        expires_at_ms=now_ms + 4_000,
        ttl_ms=4_000,
        renew_deadline_ms=now_ms - 1_000,
    )
    lock_key = keys.generic_lock("enrichment")
    production_lua.client.set(lock_key, serialize_generic_lock(late_lease, keys))
    production_lua.client.pexpireat(lock_key, late_lease.expires_at_ms)
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        manager.renew(late_lease, "late-renewal")

    assert _database_snapshot(production_lua) == before


def test_renew_rejects_at_the_exact_absolute_deadline_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-deadline-boundary")
    deadline_ms = _redis_time_ms(production_lua)
    boundary_lease = replace(
        lease,
        expires_at_ms=deadline_ms + 5_000,
        ttl_ms=5_000,
        renew_deadline_ms=deadline_ms,
    )
    lock_key = keys.generic_lock("enrichment")
    production_lua.client.set(lock_key, serialize_generic_lock(boundary_lease, keys))
    production_lua.client.pexpireat(lock_key, boundary_lease.expires_at_ms)
    production_lua.command_overrides["TIME"] = [
        [
            str(deadline_ms // 1_000).encode("ascii"),
            str((deadline_ms % 1_000) * 1_000).encode("ascii"),
        ]
    ]
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        manager.renew(boundary_lease, "renew-deadline-boundary")

    assert _database_snapshot(production_lua) == before


def test_renew_rejects_an_external_ttl_extension_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-extended-renewal")
    lock_key = keys.generic_lock("enrichment")
    production_lua.client.pexpire(lock_key, lease.ttl_ms + 1_000)
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        manager.renew(lease, "extended-renewal")

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize("current_pttl", (0, -1, -2))
def test_renew_rejects_every_nonpositive_current_pttl_without_mutation(
    production_lua: ProductionLuaHarness,
    current_pttl: int,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", f"acq-current-pttl-{current_pttl}")
    before = _database_snapshot(production_lua)
    production_lua.command_overrides["PTTL"] = [current_pttl]

    with pytest.raises(CoordinationError, match="LEASE_LOST"):
        manager.renew(lease, f"renew-current-pttl-{current_pttl}")

    assert _database_snapshot(production_lua) == before


def test_renew_rejects_an_impossible_lock_timing_envelope_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)
    lease = manager.acquire("enrichment", "acq-impossible-timing")
    lock_key = keys.generic_lock("enrichment")
    malformed = json.loads(production_lua.client.get(lock_key))
    malformed["renew_deadline_ms"] = str(int(malformed["renew_deadline_ms"]) - 1)
    malformed_raw = json.dumps(malformed, sort_keys=True, separators=(",", ":"))
    expiry = production_lua.client.pexpiretime(lock_key)
    production_lua.client.set(lock_key, malformed_raw)
    production_lua.client.pexpireat(lock_key, expiry)
    before = _database_snapshot(production_lua)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        manager.renew(lease, "renew-impossible-timing")

    assert _database_snapshot(production_lua) == before


def test_revocation_preserves_exact_expiry_above_the_lua_integer_boundary(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    store = UpstashRevocationStore(production_lua, keys, leeway_seconds=0)
    token_expires_at_s = 9_007_199_254_740_993
    expected_expires_at_ms = 9_007_199_254_740_993_000

    result = store.revoke("large-expiry", token_expires_at_s, "large-expiry-nonce")

    entry = json.loads(production_lua.client.get(keys.revocation_entry("large-expiry")))
    receipt = json.loads(
        production_lua.client.get(keys.revocation_nonce("large-expiry-nonce"))
    )
    checked = store.is_revoked("large-expiry", token_expires_at_s)
    assert result.expires_at_ms == expected_expires_at_ms
    assert checked.expires_at_ms == expected_expires_at_ms
    assert checked.revoked is True
    assert entry["expires_at_ms"] == str(expected_expires_at_ms)
    assert receipt["expires_at_ms"] == str(expected_expires_at_ms)


def test_revocation_accepts_the_largest_whole_second_signed_64_expiry(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    store = UpstashRevocationStore(production_lua, keys, leeway_seconds=0)
    token_expires_at_s = 9_223_372_036_854_775
    expected_expires_at_ms = 9_223_372_036_854_775_000

    result = store.revoke("max-expiry", token_expires_at_s, "max-expiry-nonce")

    assert result.expires_at_ms == expected_expires_at_ms
    assert store.is_revoked("max-expiry", token_expires_at_s).expires_at_ms == (
        expected_expires_at_ms
    )


def test_revocation_rejects_derived_signed_64_overflow_without_partial_writes(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    token_expires_at_s = 9_223_372_036_854_775
    leeway_s = 1
    request = revocation_request(keys, "overflow-expiry", token_expires_at_s, leeway_s)
    entry_raw = serialize_revocation_entry(
        keys.revocation_jti_hmac("overflow-expiry"), 2**63 - 1
    )
    script_keys = [
        keys.revocation_entry("overflow-expiry"),
        keys.revocation_nonce("overflow-expiry-nonce"),
    ]
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        REVOCATION_REVOKE_LUA,
        script_keys,
        [
            request.text,
            request.sha256,
            "overflow-expiry",
            str(token_expires_at_s),
            str(leeway_s),
            entry_raw,
        ],
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


def test_graph_acquire_rejects_signed_64_fence_overflow_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys, fence=2**63 - 1)
    before = _database_snapshot(production_lua)
    coordinator = UpstashCommitCoordinator(production_lua, keys)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.acquire("family", 0, "acq-max-fence")

    assert _database_snapshot(production_lua) == before


def test_rate_limit_compares_counts_exactly_above_the_lua_integer_boundary(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    now_ms = _redis_time_ms(production_lua)
    window_ms = 60_000
    window_start_ms = (now_ms // window_ms) * window_ms
    limit = 9_007_199_254_740_992
    policy = RateLimitPolicyId.SEARCH
    request = rate_request(
        keys, policy, "IP", "192.0.2.1", window_start_ms, window_ms, limit
    )
    counter_key = keys.rate_counter(policy, "IP", "192.0.2.1", window_start_ms)
    production_lua.client.set(counter_key, str(limit))

    result = production_lua.eval(
        RATE_CONSUME_LUA,
        [counter_key, keys.rate_nonce("rate-large-count")],
        [
            request.text,
            request.sha256,
            policy.value,
            "IP",
            str(window_start_ms),
            str(window_ms),
            str(limit),
        ],
        nonce_idempotent=True,
    )

    assert result[0:2] == ["OK", "RATE_LIMIT_DENIED"]
    receipt = json.loads(result[2])
    assert receipt["observed_count"] == "9007199254740993"
    assert receipt["remaining"] == "0"


def test_rate_limit_rejects_signed_64_counter_overflow_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    now_ms = _redis_time_ms(production_lua)
    window_ms = 60_000
    window_start_ms = (now_ms // window_ms) * window_ms
    policy = RateLimitPolicyId.SEARCH
    request = rate_request(
        keys, policy, "IP", "192.0.2.2", window_start_ms, window_ms, 1
    )
    counter_key = keys.rate_counter(policy, "IP", "192.0.2.2", window_start_ms)
    production_lua.client.set(counter_key, str(2**63 - 1))
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        RATE_CONSUME_LUA,
        [counter_key, keys.rate_nonce("rate-max-count")],
        [
            request.text,
            request.sha256,
            policy.value,
            "IP",
            str(window_start_ms),
            str(window_ms),
            "1",
        ],
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    ("reservation_fence", "expected_mode"), ((9, "COMMITTING"), (11, "CORRUPT"))
)
def test_lock_free_reservation_fence_is_bounded_by_the_stored_floor(
    production_lua: ProductionLuaHarness,
    reservation_fence: int,
    expected_mode: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys, fence=10)
    lease = GraphLease(
        "family", "historical-lease", reservation_fence, 0, 20_000, 15_000, 15_000
    )
    _permit, _commit, reservation_raw = _reservation_values(
        keys, lease, revision=1, nonce=f"reservation-fence-{reservation_fence}"
    )
    production_lua.client.set(keys.graph_reservation("family"), reservation_raw)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    admin = UpstashCoordinationAdmin(production_lua, keys)

    inspection = admin.inspect("family")

    assert inspection.mode == expected_mode
    if reservation_fence == 9:
        status = coordinator.get_status("family")
        assert status.mode == "COMMITTING"
        assert status.active_reservation is not None
        assert status.active_reservation.permit.fencing_token == 9
    else:
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            coordinator.get_status("family")


def test_confirmation_rejects_swapped_confirmed_and_fence_keys_without_mutation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys, revision=1, fence=1)
    lease = GraphLease("family", "historic", 1, 1, 20_000, 15_000, 15_000)
    permit, commit, reservation_raw = _reservation_values(
        keys, lease, revision=2, nonce="authorize-key-swap"
    )
    production_lua.client.set(keys.graph_reservation("family"), reservation_raw)
    swapped_keys = [
        keys.graph_lock("family"),
        keys.graph_confirmed_revision("family"),
        keys.graph_fence("family"),
        keys.graph_reservation("family"),
        keys.graph_last_confirmation("family"),
    ]
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        CONFIRM_COMMIT_LUA,
        swapped_keys,
        [
            permit.scope,
            str(permit.operation_id),
            str(permit.revision),
            str(permit.fencing_token),
            permit.permit_id,
            permit.commit_sha256,
            canonical_graph_commit_json(commit),
            keys.hmac_hex("graph-confirmation-nonce", "confirm-key-swap"),
            "1",
            *_expected_core_args(production_lua, swapped_keys),
        ],
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    "invalid",
    ("wrong-lock-suffix", "wrong-domain", "wrong-hash-tag", "digest", "hmac"),
)
def test_generic_acquire_rejects_invalid_topology_or_grammar_without_mutation(
    production_lua: ProductionLuaHarness,
    invalid: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    scope = "enrichment"
    acquisition_id = f"acq-invalid-{invalid}"
    request = lease_acquire_request(keys, "GENERIC", scope, acquisition_id, 15_000)
    script_keys = [
        keys.generic_lock(scope),
        keys.generic_acquisition_result(scope, acquisition_id),
    ]
    args = [request.text, request.sha256, scope, acquisition_id, "15000"]
    if invalid == "wrong-lock-suffix":
        script_keys[0] += ":wrong"
    elif invalid == "wrong-domain":
        script_keys[0] = script_keys[0].replace(":generic:", ":graph:")
    elif invalid == "wrong-hash-tag":
        script_keys[1] = keys.generic_acquisition_result("other", acquisition_id)
    elif invalid == "digest":
        args[1] = "not-a-digest"
    else:
        payload = json.loads(request.text)
        payload["scope_hmac"] = "not-an-hmac"
        args[0] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        args[1] = "a" * 64
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        GENERIC_ACQUIRE_LUA,
        script_keys,
        args,
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


ALL_PRODUCTION_LUA_SCRIPTS = (
    GENERIC_ACQUIRE_LUA,
    GRAPH_ACQUIRE_LUA,
    LEASE_RENEW_LUA,
    LEASE_RELEASE_LUA,
    LEASE_ASSERT_LUA,
    AUTHORIZE_COMMIT_LUA,
    COORDINATION_STATUS_LUA,
    CONFIRM_COMMIT_LUA,
    COORDINATION_INSPECT_LUA,
    COORDINATION_ADMIN_LUA,
    REVOCATION_REVOKE_LUA,
    REVOCATION_CHECK_LUA,
    RATE_TIME_LUA,
    RATE_CONSUME_LUA,
)


@pytest.mark.parametrize("script", ALL_PRODUCTION_LUA_SCRIPTS)
@pytest.mark.parametrize("shape", ("missing", "extra"))
def test_every_production_script_rejects_invalid_arity_before_mutation(
    production_lua: ProductionLuaHarness,
    script: str,
    shape: str,
) -> None:
    before = _database_snapshot(production_lua)
    keys = [] if shape == "missing" else [f"key-{index}" for index in range(20)]
    args = [] if shape == "missing" else [f"arg-{index}" for index in range(30)]
    if script == RATE_TIME_LUA and shape == "missing":
        args = ["unexpected"]

    result = production_lua.eval(script, keys, args, nonce_idempotent=False)

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    "torn",
    (
        "missing-fence",
        "corrupt-fence",
        "missing-confirmed",
        "corrupt-confirmed",
        "missing-proof",
        "corrupt-proof",
        "corrupt-lock",
        "corrupt-reservation",
    ),
)
def test_graph_acquire_rejects_every_torn_core_without_fence_consumption(
    production_lua: ProductionLuaHarness,
    torn: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    target = {
        "fence": keys.graph_fence("family"),
        "confirmed": keys.graph_confirmed_revision("family"),
        "proof": keys.graph_last_confirmation("family"),
        "lock": keys.graph_lock("family"),
        "reservation": keys.graph_reservation("family"),
    }[torn.split("-", 1)[1]]
    if torn.startswith("missing-"):
        production_lua.client.delete(target)
    else:
        production_lua.client.set(target, "not-canonical-state")
        if torn == "corrupt-lock":
            production_lua.client.pexpire(target, 15_000)
    before = _database_snapshot(production_lua)
    coordinator = UpstashCommitCoordinator(production_lua, keys)

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.acquire("family", 0, f"acq-torn-{torn}")

    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    "torn",
    (
        "missing-lock",
        "corrupt-lock",
        "missing-fence",
        "corrupt-fence",
        "missing-confirmed",
        "corrupt-confirmed",
        "missing-proof",
        "corrupt-proof",
        "corrupt-reservation",
    ),
)
def test_authorization_rejects_every_torn_core_without_reservation(
    production_lua: ProductionLuaHarness,
    torn: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-authorize-torn-{torn}")
    commit, staged = _commit_values(lease)
    target = {
        "lock": keys.graph_lock("family"),
        "fence": keys.graph_fence("family"),
        "confirmed": keys.graph_confirmed_revision("family"),
        "proof": keys.graph_last_confirmation("family"),
        "reservation": keys.graph_reservation("family"),
    }[torn.split("-", 1)[1]]
    if torn.startswith("missing-"):
        production_lua.client.delete(target)
    else:
        expiry = production_lua.client.pexpiretime(target)
        production_lua.client.set(target, "not-canonical-state")
        if expiry > 0:
            production_lua.client.pexpireat(target, expiry)
    before = _database_snapshot(production_lua)

    expected = "LEASE_LOST" if torn == "missing-lock" else "COORDINATION_STATE_CORRUPT"
    with pytest.raises(CoordinationError, match=expected):
        coordinator.authorize_commit(lease, commit, staged, f"authorize-torn-{torn}")

    assert _database_snapshot(production_lua) == before


def test_graph_acquire_core_cas_race_preserves_the_raced_state(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    raced: list[tuple[tuple[str, str | None, int], ...]] = []

    def race(script: str, _keys: Sequence[str], args: Sequence[str]) -> None:
        if script != GRAPH_ACQUIRE_LUA or len(args) != 10:
            return
        production_lua.before_eval = None
        production_lua.client.set(keys.graph_fence("family"), "2")
        raced.append(_database_snapshot(production_lua))

    production_lua.before_eval = race

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.acquire("family", 0, "acq-core-race")

    assert _database_snapshot(production_lua) == raced[0]


def test_authorization_core_cas_race_preserves_the_raced_state(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "acq-authorization-race")
    commit, staged = _commit_values(lease)
    raced: list[tuple[tuple[str, str | None, int], ...]] = []

    def race(script: str, _keys: Sequence[str], args: Sequence[str]) -> None:
        if script != AUTHORIZE_COMMIT_LUA or len(args) != 8:
            return
        production_lua.before_eval = None
        production_lua.client.delete(keys.graph_last_confirmation("family"))
        raced.append(_database_snapshot(production_lua))

    production_lua.before_eval = race

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        coordinator.authorize_commit(lease, commit, staged, "authorization-core-race")

    assert _database_snapshot(production_lua) == raced[0]


def test_graph_acquisition_receipt_replays_before_torn_core_validation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "acq-replay-torn-core")
    production_lua.client.delete(
        keys.graph_fence("family"), keys.graph_last_confirmation("family")
    )
    before = _database_snapshot(production_lua)

    replayed = coordinator.acquire("family", 0, "acq-replay-torn-core")

    assert replayed == lease
    assert _database_snapshot(production_lua) == before


def test_authorization_reservation_replays_before_torn_core_validation(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "acq-auth-replay-torn-core")
    commit, staged = _commit_values(lease)
    permit = coordinator.authorize_commit(
        lease, commit, staged, "auth-replay-torn-core"
    )
    production_lua.client.delete(
        keys.graph_fence("family"), keys.graph_last_confirmation("family")
    )
    before = _database_snapshot(production_lua)

    replayed = coordinator.authorize_commit(
        lease, commit, staged, "auth-replay-torn-core"
    )

    assert replayed == permit
    assert _database_snapshot(production_lua) == before


def test_generic_lease_operations_succeed_and_replay_without_extending_state(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    manager = UpstashLeaseManager(production_lua, keys)

    lease = manager.acquire("enrichment", "generic-replay")
    acquired_state = _database_snapshot(production_lua)
    assert manager.acquire("enrichment", "generic-replay") == lease
    assert _database_snapshot(production_lua) == acquired_state

    renewed = manager.renew(lease, "generic-renew-replay")
    renewed_state = _database_snapshot(production_lua)
    assert manager.renew(lease, "generic-renew-replay") == renewed
    assert _database_snapshot(production_lua) == renewed_state

    released = manager.release(renewed, "generic-release-replay")
    released_state = _database_snapshot(production_lua)
    replayed_release = manager.release(renewed, "generic-release-replay")
    assert released.code == "LEASE_RELEASED"
    assert replayed_release.code == "LEASE_RELEASE_REPLAYED"
    assert _database_snapshot(production_lua) == released_state


def test_graph_commit_operations_succeed_and_replay_in_production_lua(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, "graph-replay")
    assert coordinator.acquire("family", 0, "graph-replay") == lease
    commit, staged = _commit_values(lease)
    permit = coordinator.authorize_commit(lease, commit, staged, "authorize-replay")
    assert (
        coordinator.authorize_commit(lease, commit, staged, "authorize-replay")
        == permit
    )
    confirmed = coordinator.confirm_commit(permit, commit, "confirm-replay")
    before_replay = _database_snapshot(production_lua)
    graph_keys = _core_keys(keys)

    replay = production_lua.eval(
        CONFIRM_COMMIT_LUA,
        graph_keys,
        [
            permit.scope,
            str(permit.operation_id),
            str(permit.revision),
            str(permit.fencing_token),
            permit.permit_id,
            permit.commit_sha256,
            canonical_graph_commit_json(commit),
            keys.hmac_hex("graph-confirmation-nonce", "confirm-replay"),
            "0",
            *_expected_core_args(production_lua, graph_keys),
        ],
        nonce_idempotent=True,
    )

    assert confirmed.code == "CONFIRMED"
    assert replay[0:2] == ["OK", "CONFIRMATION_REPLAYED"]
    assert _database_snapshot(production_lua) == before_replay


def test_admin_initialize_succeeds_and_exactly_replays_in_production_lua(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    admin = UpstashCoordinationAdmin(production_lua, keys)
    absent = admin.inspect("family")
    semantic = "a" * 64
    evidence = CoordinationEvidence(
        "family",
        0,
        semantic,
        None,
        0,
        1,
        coordination_evidence_sha256("family", 0, semantic, None, 0, 1),
    )

    initialized = admin.initialize(
        evidence, absent.state_sha256, "admin-initialize-replay"
    )
    initialized_state = _database_snapshot(production_lua)
    replayed = admin.initialize(
        evidence, absent.state_sha256, "admin-initialize-replay"
    )

    assert initialized.code == "ADMIN_INITIALIZED"
    assert replayed == initialized
    assert _database_snapshot(production_lua) == initialized_state

    current = admin.inspect("family")
    reconciled_evidence = CoordinationEvidence(
        "family",
        0,
        semantic,
        None,
        1,
        2,
        coordination_evidence_sha256("family", 0, semantic, None, 1, 2),
    )
    reconciled = admin.reconcile(
        reconciled_evidence, current.state_sha256, "admin-reconcile-replay"
    )
    reconciled_state = _database_snapshot(production_lua)
    replayed_reconcile = admin.reconcile(
        reconciled_evidence, current.state_sha256, "admin-reconcile-replay"
    )
    assert reconciled.code == "ADMIN_RECONCILED"
    assert replayed_reconcile == reconciled
    assert _database_snapshot(production_lua) == reconciled_state


def test_revocation_and_rate_limit_succeed_and_exactly_replay(
    production_lua: ProductionLuaHarness,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    now_s = _redis_time_ms(production_lua) // 1_000
    revocations = UpstashRevocationStore(production_lua, keys, leeway_seconds=30)
    revoked = revocations.revoke("replay-jti", now_s + 300, "revocation-replay")
    revoked_state = _database_snapshot(production_lua)
    assert revocations.revoke("replay-jti", now_s + 300, "revocation-replay") == revoked
    assert revocations.is_revoked("replay-jti", now_s + 300).revoked is True
    assert _database_snapshot(production_lua) == revoked_state

    limiter = UpstashRateLimiter(production_lua, keys)
    subject = IpRateLimitSubject("IP", "203.0.113.8")
    allowed = limiter.consume(RateLimitPolicyId.SEARCH, subject, "rate-replay")
    rate_state = _database_snapshot(production_lua)
    replayed = limiter.consume(RateLimitPolicyId.SEARCH, subject, "rate-replay")
    assert allowed.allowed is True
    assert replayed == allowed
    assert _database_snapshot(production_lua) == rate_state
