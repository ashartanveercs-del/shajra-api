"""Integration tests that execute the exact shipped Lua scripts in-process."""

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
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
    lease_operation_request,
    rate_request,
    revocation_request,
    serialize_commit_reservation,
    serialize_generic_lock,
    serialize_graph_lock,
    serialize_reconciled_head_receipt,
    serialize_revocation_entry,
    serialize_staged_write_receipt,
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
    _LUA_DECIMAL_VALIDATION,
)
from domain.ids import OperationId, PersonId
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


SHA256_PROBE_LUA = (
    "-- shajra-test:sha256-probe:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 0 or #ARGV ~= 1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
return {'OK', sha256_hex(ARGV[1])}
"""
)

SHA256_VECTORS = (
    ("", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    ("a" * 55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"),
    ("a" * 56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"),
    ("a" * 63, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"),
    ("a" * 64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"),
    ("a" * 65, "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0"),
    ("a" * 1_000, "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3"),
    (
        "".join(chr(value) for value in range(0x80)),
        "471fb943aa23c511f6f72f8d1652d9c880cfa392ad80503120547703e56a2be5",
    ),
)


@pytest.mark.parametrize(("value", "expected"), SHA256_VECTORS)
def test_sha256_vectors_use_the_exact_production_lua(
    production_lua: ProductionLuaHarness,
    value: str,
    expected: str,
) -> None:
    result = production_lua.eval(SHA256_PROBE_LUA, [], [value], nonce_idempotent=False)

    assert result == ["OK", expected]


@pytest.mark.parametrize(("value", "expected"), SHA256_VECTORS)
def test_sha256_vectors_pass_with_the_arithmetic_compatibility_shim(
    production_lua_compat: ProductionLuaHarness,
    value: str,
    expected: str,
) -> None:
    result = production_lua_compat.eval(
        SHA256_PROBE_LUA, [], [value], nonce_idempotent=False
    )

    assert result == ["OK", expected]


def test_sha256_production_path_invokes_native_bitop(
    production_lua_bit_spy: ProductionLuaHarness,
) -> None:
    result = production_lua_bit_spy.eval(
        SHA256_PROBE_LUA, [], ["abc"], nonce_idempotent=False
    )
    calls = production_lua_bit_spy.client.eval("return __shajra_bit_calls", 0)

    assert result == [
        "OK",
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ]
    assert calls > 0


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
        datetime(
            2026,
            8,
            8,
            10,
            0,
            revision if revision <= 59 else 1,
            tzinfo=UTC,
        ),
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


def _large_reservation_values(
    keys: RedisKeyBuilder,
    lease: GraphLease,
    *,
    staged_receipt_size: int,
) -> tuple[str, int, int]:
    def make_staged(person_ids: tuple[PersonId, ...]) -> tuple[StagedWriteReceipt, str]:
        write_set_json = canonical_graph_write_set_json(
            GraphWriteSet(person_tombstones=person_ids)
        )
        staged = StagedWriteReceipt(
            OperationId("op_large_lua"),
            1,
            lease.fencing_token,
            write_set_json,
            hashlib.sha256(write_set_json.encode("ascii")).hexdigest(),
        )
        return staged, serialize_staged_write_receipt(staged)

    _empty_staged, empty_raw = make_staged(())
    standard_id_size = len("per_" + ("0" * 32))
    escaped_list_item_size = standard_id_size + 5
    item_count = max(
        1, (staged_receipt_size - len(empty_raw) + 1) // escaped_list_item_size
    )
    person_ids = tuple(PersonId(f"per_{index:032x}") for index in range(item_count))
    staged, staged_raw = make_staged(person_ids)
    filler_size = staged_receipt_size - len(staged_raw)
    assert 0 <= filler_size < escaped_list_item_size
    if filler_size:
        person_ids = (*person_ids[:-1], PersonId(person_ids[-1] + ("a" * filler_size)))
        staged, staged_raw = make_staged(person_ids)
    assert len(staged_raw) == staged_receipt_size

    commit = GraphCommit(
        staged.operation_id,
        1,
        lease.fencing_token,
        "cpr_large_lua",
        "e" * 64,
        datetime(2026, 8, 10, tzinfo=UTC),
    )
    commit_digest = graph_commit_sha256(commit)
    permit = CommitPermit(
        lease.scope,
        commit.operation_id,
        commit.revision,
        commit.fencing_token,
        commit.permit_id,
        commit_digest,
    )
    proposed_raw = serialize_commit_reservation(
        CommitReservation(
            lease.scope,
            "COMMITTING",
            permit,
            commit,
            commit_digest,
            staged,
        ),
        keys,
        f"authorize-large-{staged_receipt_size}",
    )
    return proposed_raw, len(staged.write_set_json), len(staged_raw)


def _redis_time_ms(production_lua: ProductionLuaHarness) -> int:
    result = production_lua.eval(RATE_TIME_LUA, [], [], nonce_idempotent=False)
    return int(result[2])


def _seed_live_graph_lock(
    production_lua: ProductionLuaHarness,
    keys: RedisKeyBuilder,
    *,
    revision: int,
    fence: int,
    acquisition_id: str,
    ttl_ms: int = 15_000,
) -> GraphLease:
    now_ms = _redis_time_ms(production_lua)
    lease = GraphLease(
        "family",
        acquisition_id,
        fence,
        revision,
        now_ms + ttl_ms,
        ttl_ms,
        now_ms + ttl_ms - 5_000,
    )
    production_lua.client.set(
        keys.graph_lock("family"),
        serialize_graph_lock(lease, keys),
        pxat=lease.expires_at_ms,
    )
    return lease


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


@pytest.mark.parametrize(
    "mismatch",
    ("commit-payload", "staged-receipt-payload", "write-set-payload"),
)
def test_authorization_lua_recomputes_every_recovery_payload_digest_before_mutation(
    production_lua: ProductionLuaHarness,
    mismatch: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-digest-{mismatch}")
    _permit, _commit, proposed_raw = _reservation_values(
        keys, lease, revision=1, nonce=f"authorize-digest-{mismatch}"
    )
    proposed = json.loads(proposed_raw)
    staged = json.loads(proposed["staged_write_receipt_json"])

    if mismatch == "commit-payload":
        commit = json.loads(proposed["commit_json"])
        commit["semantic_checksum"] = "f" * 64
        proposed["commit_json"] = json.dumps(
            commit, sort_keys=True, separators=(",", ":")
        )
    elif mismatch == "staged-receipt-payload":
        write_set = json.loads(staged["write_set_json"])
        write_set["person_tombstones"] = ["per_digest_mismatch"]
        staged["write_set_json"] = json.dumps(write_set, separators=(",", ":"))
        staged["write_set_sha256"] = hashlib.sha256(
            staged["write_set_json"].encode("ascii")
        ).hexdigest()
        proposed["staged_write_receipt_json"] = json.dumps(
            staged, sort_keys=True, separators=(",", ":")
        )
    else:
        staged["write_set_sha256"] = "f" * 64
        proposed["staged_write_receipt_json"] = json.dumps(
            staged, sort_keys=True, separators=(",", ":")
        )
        proposed["staged_write_receipt_sha256"] = hashlib.sha256(
            proposed["staged_write_receipt_json"].encode("ascii")
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

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    ("digest_field", "malformed"),
    (
        ("scope-hmac", "f" * 63),
        ("authorization-nonce-hmac", "G" * 64),
        ("permit-commit", "not-a-digest"),
        ("commit-semantic", "F" * 64),
        ("commit-envelope", "0" * 65),
        ("staged-envelope", "g" * 64),
        ("write-set", "0" * 63),
    ),
)
def test_authorization_lua_rejects_malformed_digests_before_any_mutation(
    production_lua: ProductionLuaHarness,
    digest_field: str,
    malformed: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-malformed-{digest_field}")
    _permit, _commit, proposed_raw = _reservation_values(
        keys, lease, revision=1, nonce=f"authorize-malformed-{digest_field}"
    )
    proposed = json.loads(proposed_raw)

    if digest_field == "scope-hmac":
        proposed["scope_hmac"] = malformed
    elif digest_field == "authorization-nonce-hmac":
        proposed["authorization_request_nonce_hmac"] = malformed
    elif digest_field == "permit-commit":
        proposed["permit"]["commit_sha256"] = malformed
    elif digest_field == "commit-semantic":
        commit = json.loads(proposed["commit_json"])
        commit["semantic_checksum"] = malformed
        commit_raw = json.dumps(commit, sort_keys=True, separators=(",", ":"))
        commit_digest = hashlib.sha256(commit_raw.encode("ascii")).hexdigest()
        proposed["commit_json"] = commit_raw
        proposed["commit_sha256"] = commit_digest
        proposed["permit"]["commit_sha256"] = commit_digest
    elif digest_field == "commit-envelope":
        proposed["commit_sha256"] = malformed
    elif digest_field == "staged-envelope":
        proposed["staged_write_receipt_sha256"] = malformed
    else:
        staged = json.loads(proposed["staged_write_receipt_json"])
        staged["write_set_sha256"] = malformed
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

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    ("staged_receipt_size", "maximum_seconds"),
    ((10_000, 5.0), (100_000, 5.0), (1_950_000, 10.0)),
)
def test_authorization_lua_hashes_realistically_large_nested_payloads(
    production_lua: ProductionLuaHarness,
    staged_receipt_size: int,
    maximum_seconds: float,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    coordinator = UpstashCommitCoordinator(production_lua, keys)
    lease = coordinator.acquire("family", 0, f"acq-large-{staged_receipt_size}")
    proposed_raw, write_set_size, actual_staged_size = _large_reservation_values(
        keys, lease, staged_receipt_size=staged_receipt_size
    )
    staged_raw = json.loads(proposed_raw)["staged_write_receipt_json"]
    assert len(staged_raw) == actual_staged_size == staged_receipt_size
    assert write_set_size <= 2_000_000
    assert actual_staged_size <= 2_000_000
    if staged_receipt_size == 1_950_000:
        assert write_set_size > 1_800_000
    graph_keys = _core_keys(keys)

    started = perf_counter()
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
    elapsed = perf_counter() - started

    assert result == ["OK", "RESERVATION_CREATED", proposed_raw]
    assert elapsed < maximum_seconds


@pytest.mark.parametrize(
    ("revision", "fence"),
    (
        (9_007_199_254_740_993, 9_007_199_254_740_993),
        (9_223_372_036_854_775_807, 9_223_372_036_854_775_807),
    ),
)
def test_authorization_lua_preserves_exact_high_commit_revision_and_fence(
    production_lua: ProductionLuaHarness,
    revision: int,
    fence: int,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys, revision=revision - 1, fence=fence)
    lease = _seed_live_graph_lock(
        production_lua,
        keys,
        revision=revision - 1,
        fence=fence,
        acquisition_id=f"acq-high-{revision}",
    )
    _permit, _commit, proposed_raw = _reservation_values(
        keys, lease, revision=revision, nonce=f"authorize-high-{revision}"
    )
    graph_keys = _core_keys(keys)

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

    assert result == ["OK", "RESERVATION_CREATED", proposed_raw]
    persisted = production_lua.client.get(keys.graph_reservation("family"))
    assert persisted == proposed_raw
    decoded = json.loads(persisted)
    assert decoded["permit"]["revision"] == str(revision)
    assert decoded["permit"]["fencing_token"] == str(fence)
    assert f'"revision":{revision}' in decoded["commit_json"]
    assert f'"fencing_token":{fence}' in decoded["commit_json"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("revision", "1.0"),
        ("revision", "1e0"),
        ("revision", "01"),
        ("revision", "9223372036854775808"),
        ("fencing_token", "-1"),
        ("fencing_token", "01"),
        ("fencing_token", "9223372036854775808"),
    ),
)
def test_authorization_lua_rejects_noncanonical_or_overflow_commit_numbers(
    production_lua: ProductionLuaHarness,
    field: str,
    replacement: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    _seed_ready_graph(production_lua, keys)
    lease = _seed_live_graph_lock(
        production_lua,
        keys,
        revision=0,
        fence=1,
        acquisition_id=f"acq-invalid-{field}-{replacement}",
    )
    _permit, _commit, proposed_raw = _reservation_values(
        keys, lease, revision=1, nonce=f"authorize-invalid-{field}-{replacement}"
    )
    proposed = json.loads(proposed_raw)
    commit_raw = proposed["commit_json"].replace(
        f'"{field}":1', f'"{field}":{replacement}'
    )
    assert commit_raw != proposed["commit_json"]
    commit_digest = hashlib.sha256(commit_raw.encode("ascii")).hexdigest()
    proposed["commit_json"] = commit_raw
    proposed["commit_sha256"] = commit_digest
    proposed["permit"]["commit_sha256"] = commit_digest
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

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


@pytest.mark.parametrize(
    ("domain", "corruption"),
    (
        ("generic", "domain"),
        ("generic", "scope-hmac"),
        ("generic", "acquisition-hmac"),
        ("generic", "expires-at"),
        ("generic", "ttl"),
        ("generic", "renew-deadline"),
        ("generic", "applied-expiry"),
        ("generic", "no-expiry"),
        ("graph", "fencing-token"),
        ("graph", "base-revision"),
    ),
)
def test_release_lua_rejects_invalid_current_lock_envelope_without_mutation(
    production_lua: ProductionLuaHarness,
    domain: str,
    corruption: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    scope = "family"
    acquisition_id = f"acq-release-{domain}-{corruption}"
    if domain == "generic":
        manager = UpstashLeaseManager(production_lua, keys)
        lease = manager.acquire(scope, acquisition_id)
        lock_key = keys.generic_lock(scope)
        receipt_key = keys.generic_operation_result(scope, "release-corrupt")
        expected_raw = serialize_generic_lock(lease, keys)
    else:
        _seed_ready_graph(production_lua, keys)
        coordinator = UpstashCommitCoordinator(production_lua, keys)
        lease = coordinator.acquire(scope, 0, acquisition_id)
        lock_key = keys.graph_lock(scope)
        receipt_key = keys.graph_operation_result(scope, "release-corrupt")
        expected_raw = serialize_graph_lock(lease, keys)

    lock = json.loads(expected_raw)
    if corruption == "domain":
        lock["domain"] = "GRAPH_COMMIT"
    elif corruption == "scope-hmac":
        lock["scope_hmac"] = "f" * 64
    elif corruption == "acquisition-hmac":
        lock["acquisition_id_hmac"] = "f" * 64
    elif corruption == "expires-at":
        lock["expires_at_ms"] = str(lease.expires_at_ms + 1)
    elif corruption == "ttl":
        lock["ttl_ms"] = "300001"
    elif corruption == "renew-deadline":
        lock["renew_deadline_ms"] = str(lease.renew_deadline_ms + 1)
    elif corruption == "fencing-token":
        lock["fencing_token"] = "0"
    elif corruption == "base-revision":
        lock["base_revision"] = "-1"
    current_raw = json.dumps(lock, sort_keys=True, separators=(",", ":"))

    if corruption == "no-expiry":
        production_lua.client.set(lock_key, current_raw)
    elif corruption == "applied-expiry":
        production_lua.client.set(lock_key, current_raw, pxat=lease.expires_at_ms + 1)
    else:
        production_lua.client.set(lock_key, current_raw, pxat=lease.expires_at_ms)

    request = lease_operation_request(keys, "release", lease, "release-corrupt")
    request_payload = json.loads(request.text)
    request_payload["lock_sha256"] = hashlib.sha256(
        current_raw.encode("ascii")
    ).hexdigest()
    request_raw = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
    request_sha256 = hashlib.sha256(request_raw.encode("ascii")).hexdigest()
    before = _database_snapshot(production_lua)

    result = production_lua.eval(
        LEASE_RELEASE_LUA,
        [lock_key, receipt_key],
        [request_raw, request_sha256, scope, acquisition_id, current_raw],
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
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
    assert result.expires_at_ms == expected_expires_at_ms
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
    entry = json.loads(production_lua.client.get(keys.revocation_entry("max-expiry")))
    assert entry["expires_at_ms"] == str(expected_expires_at_ms)


@pytest.mark.parametrize("target", ("receipt", "entry"))
@pytest.mark.parametrize(
    "expiry_state", ("exact", "overlong", "underlong", "no-expiry")
)
def test_revocation_fails_closed_for_unverifiable_retained_high_expiry_without_mutation(
    production_lua: ProductionLuaHarness,
    target: str,
    expiry_state: str,
) -> None:
    keys = RedisKeyBuilder("test", "secret")
    store = UpstashRevocationStore(production_lua, keys, leeway_seconds=0)
    jti = f"retained-high-{target}-{expiry_state}"
    nonce = f"retained-high-nonce-{target}-{expiry_state}"
    token_expires_at_s = 9_007_199_254_741
    expected_expiry = token_expires_at_s * 1_000
    store.revoke(jti, token_expires_at_s, nonce)
    entry_key = keys.revocation_entry(jti)
    receipt_key = keys.revocation_nonce(nonce)
    entry_raw = production_lua.client.get(entry_key)
    assert entry_raw is not None

    state_key = receipt_key if target == "receipt" else entry_key
    if target == "entry":
        production_lua.client.delete(receipt_key)
    if expiry_state == "overlong":
        production_lua.client.pexpireat(state_key, expected_expiry + 1)
    elif expiry_state == "underlong":
        production_lua.client.pexpireat(state_key, expected_expiry - 1)
    elif expiry_state == "no-expiry":
        production_lua.client.persist(state_key)

    request = revocation_request(keys, jti, token_expires_at_s, 0)
    before = _database_snapshot(production_lua)
    result = production_lua.eval(
        REVOCATION_REVOKE_LUA,
        [entry_key, receipt_key],
        [
            request.text,
            request.sha256,
            jti,
            str(token_expires_at_s),
            "0",
            entry_raw,
        ],
        nonce_idempotent=True,
    )

    assert result == ["ERR", "COORDINATION_STATE_CORRUPT"]
    assert _database_snapshot(production_lua) == before


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
