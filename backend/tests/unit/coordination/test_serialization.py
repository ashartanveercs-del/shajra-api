from __future__ import annotations

import hashlib
import hmac
import re
import inspect
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings


ROOT_DIR = Path(__file__).resolve().parents[4]

RUNTIME_SETTINGS = {
    "airtable_pat": "test-token",
    "airtable_base_id": "app-test",
    "admin_username": "admin",
    "admin_password_hash": "test-password-hash",
    "jwt_secret": "x" * 32,
    "mutation_preview_secret": "test-mutation-preview-secret",
    "upstash_redis_rest_url": "https://example.upstash.io",
    "upstash_redis_rest_token": "upstash-secret-marker",
    "redis_namespace": "preview-1",
    "redis_key_hmac_secret": "hmac-secret-marker",
    "cors_allowed_origins": "https://synthetic.example",
}


def test_development_does_not_require_upstash_and_uses_exact_jwt_leeway_default():
    settings = Settings(app_env="development", _env_file=None)

    assert settings.upstash_redis_rest_url is None
    assert settings.upstash_redis_rest_token is None
    assert settings.redis_namespace is None
    assert settings.redis_key_hmac_secret is None
    assert settings.jwt_leeway_seconds == 30


@pytest.mark.parametrize("app_env", ["preview", "production"])
@pytest.mark.parametrize(
    "setting_name",
    (
        "upstash_redis_rest_url",
        "upstash_redis_rest_token",
        "redis_namespace",
        "redis_key_hmac_secret",
    ),
)
def test_runtime_environments_require_coordination_settings_without_secret_leaks(
    app_env: str, setting_name: str
):
    values = {**RUNTIME_SETTINGS, setting_name: None}

    with pytest.raises(ValidationError) as raised:
        Settings(app_env=app_env, **values, _env_file=None)

    message = str(raised.value)
    assert setting_name.upper() in message
    assert "upstash-secret-marker" not in message
    assert "hmac-secret-marker" not in message


@pytest.mark.parametrize(
    "namespace",
    (
        "Upper",
        "-leading",
        "trailing-",
        "double--hyphen",
        "has_underscore",
        "a" * 33,
        "",
    ),
)
def test_runtime_environments_reject_noncanonical_redis_namespace(namespace: str):
    with pytest.raises(ValidationError, match="REDIS_NAMESPACE") as raised:
        Settings(
            app_env="preview",
            **{**RUNTIME_SETTINGS, "redis_namespace": namespace},
            _env_file=None,
        )

    assert "upstash-secret-marker" not in str(raised.value)
    assert "hmac-secret-marker" not in str(raised.value)


@pytest.mark.parametrize("namespace", ("a", "abc-123", "a" * 32))
def test_runtime_environments_accept_canonical_redis_namespace(namespace: str):
    settings = Settings(
        app_env="production",
        **{**RUNTIME_SETTINGS, "redis_namespace": namespace},
        _env_file=None,
    )

    assert settings.redis_namespace == namespace


@pytest.mark.parametrize("leeway", (-1, 301))
def test_jwt_leeway_rejects_values_outside_zero_through_three_hundred(leeway: int):
    with pytest.raises(ValidationError, match="jwt_leeway_seconds"):
        Settings(jwt_leeway_seconds=leeway, _env_file=None)


def test_runtime_dependency_pins_are_unique_and_exact():
    expected = {
        "argon2-cffi": "argon2-cffi==25.1.0",
        "cloudinary": "cloudinary==1.45.0",
        "fastapi": "fastapi==0.141.1",
        "pillow": "Pillow==12.3.0",
        "pyairtable": "pyairtable==3.4.2",
        "pyjwt": "PyJWT==2.13.0",
        "upstash-redis": "upstash-redis==1.7.0",
    }
    lines = (
        (ROOT_DIR / "backend" / "requirements.txt")
        .read_text(encoding="ascii")
        .splitlines()
    )
    entries: dict[str, list[str]] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        package = re.split(r"[<>=!~\s\[]", stripped, maxsplit=1)[0]
        normalized = re.sub(r"[-_.]+", "-", package).lower()
        entries.setdefault(normalized, []).append(stripped)

    for package, pin in expected.items():
        assert entries.get(package) == [pin]


def test_protocols_expose_distinct_frozen_slot_lease_types_and_exact_policies():
    from coordination.protocols import (
        GraphLease,
        IdentityRateLimitSubject,
        IpRateLimitSubject,
        Lease,
        RateLimitPolicyId,
    )

    generic = Lease("search", "acq-1", 20_000, 15_000, 15_000)
    graph = GraphLease("family", "acq-2", 7, 3, 20_000, 15_000, 15_000)

    assert type(generic) is Lease
    assert type(graph) is GraphLease
    assert not isinstance(generic, GraphLease)
    assert not hasattr(generic, "__dict__")
    with pytest.raises(FrozenInstanceError):
        generic.ttl_ms = 1  # type: ignore[misc]

    assert [(item.name, item.value) for item in RateLimitPolicyId] == [
        ("LOGIN", "login"),
        ("SUBMIT", "submit"),
        ("UPLOAD", "upload"),
        ("COMMENT", "comment"),
        ("STORY", "story"),
        ("SEARCH", "search"),
        ("EMAIL_VERIFICATION", "email-verification"),
    ]
    assert IpRateLimitSubject("IP", "203.0.113.9").kind == "IP"
    assert IdentityRateLimitSubject("IDENTITY", "usr_1").kind == "IDENTITY"


def test_protocol_method_signatures_match_the_binding_contract():
    from coordination.protocols import (
        CommitCoordinator,
        CoordinationAdmin,
        LeaseManager,
        RateLimiter,
        RevocationStore,
    )

    assert str(inspect.signature(LeaseManager.acquire)) == (
        "(self, scope: 'str', acquisition_id: 'str', ttl_ms: 'int' = 15000) -> 'Lease'"
    )
    assert str(inspect.signature(CommitCoordinator.acquire)) == (
        "(self, scope: 'str', committed_revision: 'int', acquisition_id: 'str', "
        "ttl_ms: 'int' = 15000) -> 'GraphLease'"
    )
    assert {"inspect", "initialize", "reconcile"} <= set(CoordinationAdmin.__dict__)
    assert {"revoke", "is_revoked"} <= set(RevocationStore.__dict__)
    assert "consume" in RateLimiter.__dict__


def _hmac(secret: str, label: str, value: str) -> str:
    return hmac.new(
        secret.encode("ascii"),
        f"{label}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_canonical_lease_acquire_inputs_use_exact_ascii_fields_and_digest():
    from coordination.serialization import RedisKeyBuilder, lease_acquire_request

    secret = "serialization-secret"
    keys = RedisKeyBuilder("preview-1", secret)
    generic = lease_acquire_request(keys, "GENERIC", "résumé-search", "acq-1", 15_000)
    graph = lease_acquire_request(
        keys, "GRAPH_COMMIT", "family-main", "acq-2", 15_000, 41
    )

    generic_scope = _hmac(secret, "generic-scope", "résumé-search")
    generic_acquisition = _hmac(secret, "generic-acquisition-id", "acq-1")
    expected_generic = (
        '{"acquisition_id_hmac":"'
        + generic_acquisition
        + '","domain":"GENERIC","requested_ttl_ms":"15000",'
        '"schema":"shajra.lease-acquire-request","scope_hmac":"'
        + generic_scope
        + '","version":1}'
    )
    graph_scope = _hmac(secret, "graph-scope", "family-main")
    graph_acquisition = _hmac(secret, "graph-acquisition-id", "acq-2")
    expected_graph = (
        '{"acquisition_id_hmac":"'
        + graph_acquisition
        + '","committed_revision":"41","domain":"GRAPH_COMMIT",'
        '"requested_ttl_ms":"15000","schema":"shajra.lease-acquire-request",'
        '"scope_hmac":"' + graph_scope + '","version":1}'
    )

    assert generic.text == expected_generic
    assert (
        generic.sha256 == hashlib.sha256(expected_generic.encode("ascii")).hexdigest()
    )
    assert graph.text == expected_graph
    assert graph.sha256 == hashlib.sha256(expected_graph.encode("ascii")).hexdigest()
    generic.text.encode("ascii")
    assert "résumé-search" not in generic.text


def test_hmac_key_topology_is_domain_separated_colocated_and_contains_no_raw_secrets():
    from coordination.protocols import RateLimitPolicyId
    from coordination.serialization import RedisKeyBuilder

    secret = "do-not-leak-secret"
    keys = RedisKeyBuilder("prod-1", secret)
    raw_values = (
        "family/private",
        "actor@example.com",
        "203.0.113.8",
        "token-jti-value",
        secret,
    )
    graph_keys = {
        keys.graph_lock(raw_values[0]),
        keys.graph_fence(raw_values[0]),
        keys.graph_confirmed_revision(raw_values[0]),
        keys.graph_reservation(raw_values[0]),
        keys.graph_last_confirmation(raw_values[0]),
        keys.graph_acquisition_result(raw_values[0], "actor@example.com"),
        keys.graph_operation_result(raw_values[0], "actor@example.com"),
        keys.graph_admin_result(raw_values[0], "actor@example.com"),
    }
    generic_keys = {
        keys.generic_lock(raw_values[0]),
        keys.generic_acquisition_result(raw_values[0], "actor@example.com"),
        keys.generic_operation_result(raw_values[0], "actor@example.com"),
    }
    revocation_keys = {
        keys.revocation_entry("token-jti-value"),
        keys.revocation_entry("different-jti"),
        keys.revocation_nonce("actor@example.com"),
    }
    rate_keys = {
        keys.rate_counter(RateLimitPolicyId.LOGIN, "IP", "203.0.113.8", 3_600_000),
        keys.rate_counter(
            RateLimitPolicyId.STORY, "IDENTITY", "actor@example.com", 7_200_000
        ),
        keys.rate_nonce("actor@example.com"),
    }
    history_keys = {
        keys.history_entries(),
        keys.history_active(),
        keys.history_write_guard(),
        keys.history_claim("actor@example.com"),
        keys.history_result("actor@example.com"),
        keys.history_context("actor@example.com"),
    }

    def tag(value: str) -> str:
        return re.search(r"\{[^}]+\}", value).group(0)  # type: ignore[union-attr]

    assert len({tag(value) for value in graph_keys}) == 1
    assert len({tag(value) for value in generic_keys}) == 1
    assert {tag(value) for value in revocation_keys} == {"{sj:v1:prod-1:revocation}"}
    assert {tag(value) for value in rate_keys} == {"{sj:v1:prod-1:rate}"}
    assert {tag(value) for value in history_keys} == {"{sj:v1:prod-1:history}"}
    assert {tag(value) for value in revocation_keys}.isdisjoint(
        {tag(value) for value in rate_keys}
    )
    assert graph_keys.isdisjoint(generic_keys)
    assert keys.graph_acquisition_result("family/private", "same") != (
        keys.graph_operation_result("family/private", "same")
    )
    assert keys.graph_acquisition_result("family/private", "same") != (
        keys.generic_acquisition_result("family/private", "same")
    )
    for key in graph_keys | generic_keys | revocation_keys | rate_keys | history_keys:
        assert all(raw not in key for raw in raw_values)


def test_key_builder_rejects_invalid_deployment_and_signed_64_window_values():
    from coordination.protocols import CoordinationError, RateLimitPolicyId
    from coordination.serialization import RedisKeyBuilder

    for deployment in ("UPPER", "-bad", "bad--name", "a" * 33):
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            RedisKeyBuilder(deployment, "secret")

    keys = RedisKeyBuilder("test", "secret")
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        keys.rate_counter(RateLimitPolicyId.LOGIN, "IP", "203.0.113.1", 2**63)
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        keys.graph_lock("\ud800")


def _empty_staged_receipt():
    from domain.ids import OperationId
    from repositories import GraphWriteSet, StagedWriteReceipt
    from repositories.protocols import graph_write_set_sha256
    from repositories import canonical_graph_write_set_json

    write_set = GraphWriteSet()
    return StagedWriteReceipt(
        operation_id=OperationId("op_123"),
        revision=4,
        fencing_token=9,
        write_set_json=canonical_graph_write_set_json(write_set),
        write_set_sha256=graph_write_set_sha256(write_set),
    )


def test_staged_write_receipt_round_trips_exact_canonical_envelope():
    from coordination.serialization import (
        deserialize_staged_write_receipt,
        serialize_staged_write_receipt,
    )

    receipt = _empty_staged_receipt()
    encoded = serialize_staged_write_receipt(receipt)
    expected = json.dumps(
        {
            "fencing_token": "9",
            "operation_id": "op_123",
            "revision": "4",
            "schema": "shajra.staged-write-receipt",
            "version": 1,
            "write_set_json": receipt.write_set_json,
            "write_set_sha256": receipt.write_set_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    assert encoded == expected
    assert deserialize_staged_write_receipt(encoded) == receipt


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.replace('"version":1', '"version":1,"version":1'),
        lambda value: value.replace(
            ',"write_set_sha256"', ',"extra":true,"write_set_sha256"'
        ),
        lambda value: value.replace(',"revision":"4"', ""),
        lambda value: value.replace('"revision":"4"', '"revision":"04"'),
        lambda value: value.replace('"fencing_token":"9"', '"fencing_token":9'),
        lambda value: value.replace('"op_123"', '"bad-id"'),
        lambda value: value.replace('"write_set_sha256":"', '"write_set_sha256":"0'),
        lambda value: value.replace(":", ": ", 1),
    ),
)
def test_staged_write_receipt_decoder_maps_all_malformed_state_to_stable_error(
    mutation,
):
    from coordination.protocols import CoordinationError
    from coordination.serialization import (
        deserialize_staged_write_receipt,
        serialize_staged_write_receipt,
    )

    malformed = mutation(serialize_staged_write_receipt(_empty_staged_receipt()))

    with pytest.raises(CoordinationError) as raised:
        deserialize_staged_write_receipt(malformed)

    assert raised.value.code == "COORDINATION_STATE_CORRUPT"
    assert str(raised.value) == "COORDINATION_STATE_CORRUPT"


def test_generic_and_graph_lock_envelopes_are_distinct_and_strictly_decoded():
    from coordination.protocols import CoordinationError, GraphLease, Lease
    from coordination.serialization import (
        RedisKeyBuilder,
        deserialize_generic_lock,
        deserialize_graph_lock,
        serialize_generic_lock,
        serialize_graph_lock,
    )

    keys = RedisKeyBuilder("test", "secret")
    generic = Lease("scope", "acq-1", 20_000, 15_000, 15_000)
    graph = GraphLease("scope", "acq-1", 7, 4, 20_000, 15_000, 15_000)
    generic_json = serialize_generic_lock(generic, keys)
    graph_json = serialize_graph_lock(graph, keys)

    assert '"schema":"shajra.generic-lock"' in generic_json
    assert '"schema":"shajra.graph-lock"' in graph_json
    assert deserialize_generic_lock(generic_json, keys, "scope", "acq-1") == generic
    assert deserialize_graph_lock(graph_json, keys, "scope", "acq-1") == graph
    for decoder, raw in (
        (deserialize_generic_lock, graph_json),
        (deserialize_graph_lock, generic_json),
    ):
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            decoder(raw, keys, "scope", "acq-1")
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        deserialize_generic_lock(generic_json, keys, "scope", "changed-acquisition")
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        deserialize_graph_lock(
            graph_json.replace('"fencing_token":"7"', '"fencing_token":"07"'),
            keys,
            "scope",
            "acq-1",
        )


def test_operation_admin_revocation_and_rate_requests_have_exact_digest_inputs():
    from coordination.protocols import GraphLease, Lease, RateLimitPolicyId
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_admin_request,
        lease_operation_request,
        rate_request,
        revocation_request,
    )

    keys = RedisKeyBuilder("test", "secret")
    generic = Lease("scope", "acq-1", 20_000, 15_000, 15_000)
    graph = GraphLease("scope", "acq-2", 8, 4, 20_000, 15_000, 15_000)
    values = (
        lease_operation_request(keys, "renew", generic, "nonce-1", 15_000),
        lease_operation_request(keys, "release", graph, "nonce-2"),
        coordination_admin_request(keys, "initialize", "scope", "a" * 64, "b" * 64),
        revocation_request(keys, "jti-1", 1_700_000_000, 30),
        rate_request(
            keys,
            RateLimitPolicyId.LOGIN,
            "IP",
            "203.0.113.8",
            900_000,
            900_000,
            5,
        ),
    )

    for item in values:
        assert item.sha256 == hashlib.sha256(item.text.encode("ascii")).hexdigest()
        assert (
            json.dumps(
                json.loads(item.text),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            == item.text
        )
    assert set(json.loads(values[0].text)) == {
        "acquisition_id_hmac",
        "domain",
        "lock_sha256",
        "method",
        "request_nonce_hmac",
        "requested_ttl_ms",
        "schema",
        "scope_hmac",
        "version",
    }
    assert set(json.loads(values[1].text)) == {
        "acquisition_id_hmac",
        "domain",
        "lock_sha256",
        "method",
        "request_nonce_hmac",
        "schema",
        "scope_hmac",
        "version",
    }
    assert set(json.loads(values[2].text)) == {
        "evidence_sha256",
        "expected_state_sha256",
        "method",
        "schema",
        "scope_hmac",
        "version",
    }
    assert set(json.loads(values[3].text)) == {
        "jti_hmac",
        "leeway_s",
        "schema",
        "token_expires_at_s",
        "version",
    }
    assert set(json.loads(values[4].text)) == {
        "limit",
        "policy_id",
        "schema",
        "subject_hmac",
        "subject_kind",
        "version",
        "window_ms",
        "window_start_ms",
    }


def test_all_request_builders_reject_wrong_method_domain_or_integer_grammar():
    from coordination.protocols import CoordinationError, Lease, RateLimitPolicyId
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_admin_request,
        lease_operation_request,
        rate_request,
        revocation_request,
    )

    keys = RedisKeyBuilder("test", "secret")
    lease = Lease("scope", "acq", 20_000, 15_000, 15_000)
    calls = (
        lambda: lease_operation_request(keys, "wrong", lease, "nonce"),
        lambda: coordination_admin_request(keys, "wrong", "scope", "a" * 64, "b" * 64),
        lambda: revocation_request(keys, "jti", 2**63, 30),
        lambda: rate_request(
            keys,
            RateLimitPolicyId.LOGIN,
            "IP",
            "203.0.113.8",
            0,
            0,
            5,
        ),
    )
    for call in calls:
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            call()


def test_lease_acquisition_and_operation_receipts_round_trip_original_results():
    from coordination.protocols import GraphLease, LeaseReleaseResult
    from coordination.serialization import (
        RedisKeyBuilder,
        deserialize_lease_acquisition_receipt,
        deserialize_lease_operation_receipt,
        lease_acquire_request,
        lease_operation_request,
        serialize_lease_acquisition_receipt,
        serialize_lease_operation_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    lease = GraphLease("scope", "acq-1", 8, 4, 120_000, 15_000, 115_000)
    acquire_input = lease_acquire_request(
        keys, "GRAPH_COMMIT", "scope", "acq-1", 15_000, 4
    )
    acquire_raw = serialize_lease_acquisition_receipt(acquire_input, lease, 165_000)
    acquire = deserialize_lease_acquisition_receipt(
        acquire_raw, acquire_input, keys, "scope", "acq-1"
    )

    release_input = lease_operation_request(keys, "release", lease, "nonce-release")
    release_result = LeaseReleaseResult("LEASE_RELEASED", "acq-1", 106_000)
    release_raw = serialize_lease_operation_receipt(
        release_input, release_result, 166_000
    )
    release = deserialize_lease_operation_receipt(
        release_raw, release_input, keys, "scope", "acq-1", "nonce-release"
    )

    assert acquire.lease == lease
    assert acquire.input_sha256 == acquire_input.sha256
    assert acquire.receipt_expires_at_ms == 165_000
    assert release.result == release_result
    assert release.input_sha256 == release_input.sha256
    assert release.receipt_expires_at_ms == 166_000
    assert json.loads(acquire_raw)["lease"]["expires_at_ms"] == "120000"
    assert json.loads(release_raw)["result"]["released_at_ms"] == "106000"


def test_lease_receipts_reject_changed_input_duplicate_fields_and_noncanonical_timing():
    from coordination.protocols import CoordinationError, Lease
    from coordination.serialization import (
        RedisKeyBuilder,
        deserialize_lease_acquisition_receipt,
        lease_acquire_request,
        serialize_lease_acquisition_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    lease = Lease("scope", "acq-1", 120_000, 15_000, 115_000)
    request = lease_acquire_request(keys, "GENERIC", "scope", "acq-1", 15_000)
    changed = lease_acquire_request(keys, "GENERIC", "scope", "acq-1", 14_999)
    raw = serialize_lease_acquisition_receipt(request, lease, 165_000)
    malformed = (
        raw.replace('"version":1', '"version":1,"version":1'),
        raw.replace(
            '"receipt_expires_at_ms":"165000"', '"receipt_expires_at_ms":"0165000"'
        ),
        raw.replace(
            '"receipt_expires_at_ms":"165000"', '"receipt_expires_at_ms":"165001"'
        ),
        raw.replace(',"scope_hmac"', ',"extra":true,"scope_hmac"'),
    )

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        deserialize_lease_acquisition_receipt(raw, changed, keys, "scope", "acq-1")
    for value in malformed:
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            deserialize_lease_acquisition_receipt(
                value, request, keys, "scope", "acq-1"
            )


def test_admin_revocation_and_rate_receipts_preserve_exact_original_payloads():
    from coordination.protocols import (
        CoordinationAdminResult,
        RateLimitPolicyId,
        RateLimitResult,
        RevocationResult,
    )
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_admin_request,
        deserialize_admin_result_receipt,
        deserialize_rate_receipt,
        deserialize_revocation_receipt,
        rate_request,
        revocation_request,
        serialize_admin_result_receipt,
        serialize_rate_receipt,
        serialize_revocation_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    admin_input = coordination_admin_request(
        keys, "reconcile", "scope", "a" * 64, "b" * 64
    )
    admin_result = CoordinationAdminResult("ADMIN_RECONCILED", "b" * 64, "c" * 64, 4, 9)
    admin_raw = serialize_admin_result_receipt(
        admin_input,
        "reconcile",
        keys,
        "scope",
        "nonce-admin",
        "a" * 64,
        "b" * 64,
        admin_result,
        165_000,
    )
    assert (
        deserialize_admin_result_receipt(
            admin_raw, admin_input, keys, "scope", "nonce-admin"
        ).result
        == admin_result
    )

    revoke_input = revocation_request(keys, "jti-1", 100, 30)
    revoke_result = RevocationResult("REVOKED", True, 90_000, 130_000)
    revoke_raw = serialize_revocation_receipt(
        revoke_input, revoke_result, keys.revocation_jti_hmac("jti-1"), 150_000
    )
    assert (
        deserialize_revocation_receipt(revoke_raw, revoke_input).result == revoke_result
    )

    rate_input = rate_request(
        keys,
        RateLimitPolicyId.COMMENT,
        "IDENTITY",
        "usr_1",
        3_600_000,
        3_600_000,
        20,
    )
    rate_result = RateLimitResult(
        RateLimitPolicyId.COMMENT,
        True,
        20,
        1,
        19,
        3_650_000,
        7_200_000,
        0,
    )
    rate_raw = serialize_rate_receipt(rate_input, rate_result, 7_260_000)
    assert deserialize_rate_receipt(rate_raw, rate_input).result == rate_result
    for raw in (admin_raw, revoke_raw, rate_raw):
        parsed = json.loads(raw)
        assert parsed["version"] == 1
        for key, value in parsed.items():
            if key not in {"version", "allowed", "revoked", "result"} and key.endswith(
                ("_ms", "_s", "limit", "count", "remaining")
            ):
                assert isinstance(value, str)


@pytest.mark.parametrize("receipt_kind", ("admin", "revocation", "rate"))
def test_result_receipt_decoders_reject_digest_mismatch_and_unknown_fields(
    receipt_kind,
):
    from coordination.protocols import (
        CoordinationAdminResult,
        CoordinationError,
        RateLimitPolicyId,
        RateLimitResult,
        RevocationResult,
    )
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_admin_request,
        deserialize_admin_result_receipt,
        deserialize_rate_receipt,
        deserialize_revocation_receipt,
        rate_request,
        revocation_request,
        serialize_admin_result_receipt,
        serialize_rate_receipt,
        serialize_revocation_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    if receipt_kind == "admin":
        request = coordination_admin_request(
            keys, "initialize", "scope", "a" * 64, "b" * 64
        )
        raw = serialize_admin_result_receipt(
            request,
            "initialize",
            keys,
            "scope",
            "nonce",
            "a" * 64,
            "b" * 64,
            CoordinationAdminResult("ADMIN_INITIALIZED", "b" * 64, "c" * 64, 0, 1),
            60_000,
        )
        decode = lambda value, expected: deserialize_admin_result_receipt(
            value, expected, keys, "scope", "nonce"
        )
        changed = coordination_admin_request(
            keys, "initialize", "scope", "d" * 64, "b" * 64
        )
    elif receipt_kind == "revocation":
        request = revocation_request(keys, "jti", 100, 30)
        raw = serialize_revocation_receipt(
            request,
            RevocationResult("REVOKED", True, 90_000, 130_000),
            keys.revocation_jti_hmac("jti"),
            150_000,
        )
        decode = lambda value, expected: deserialize_revocation_receipt(value, expected)
        changed = revocation_request(keys, "jti", 101, 30)
    else:
        request = rate_request(
            keys, RateLimitPolicyId.LOGIN, "IP", "203.0.113.1", 0, 900_000, 5
        )
        raw = serialize_rate_receipt(
            request,
            RateLimitResult(RateLimitPolicyId.LOGIN, True, 5, 1, 4, 10, 900_000, 0),
            960_000,
        )
        decode = lambda value, expected: deserialize_rate_receipt(value, expected)
        changed = rate_request(
            keys, RateLimitPolicyId.LOGIN, "IP", "203.0.113.2", 0, 900_000, 5
        )

    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        decode(raw, changed)
    with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
        decode(raw.replace(',"version":1', ',"unknown":null,"version":1'), request)


def _commit_recovery_values():
    from coordination.protocols import (
        CommitReservation,
        ConfirmedCommitReceipt,
    )
    from domain.ids import OperationId
    from repositories import CommitPermit, GraphCommit, graph_commit_sha256

    staged = _empty_staged_receipt()
    commit = GraphCommit(
        OperationId("op_123"),
        4,
        9,
        "cpr_123",
        "d" * 64,
        datetime(2026, 8, 7, 10, 11, 12, 345678, tzinfo=UTC),
    )
    digest = graph_commit_sha256(commit)
    permit = CommitPermit("family-main", commit.operation_id, 4, 9, "cpr_123", digest)
    reservation = CommitReservation(
        "family-main", "COMMITTING", permit, commit, digest, staged
    )
    confirmed = ConfirmedCommitReceipt("family-main", permit, commit, digest, staged)
    return reservation, confirmed


def test_reservation_and_confirmation_proof_round_trip_complete_recovery_data():
    from coordination.serialization import (
        RedisKeyBuilder,
        deserialize_commit_reservation,
        deserialize_confirmed_commit_receipt,
        serialize_commit_reservation,
        serialize_confirmed_commit_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    reservation, confirmed = _commit_recovery_values()
    reservation_raw = serialize_commit_reservation(
        reservation, keys, "authorization-nonce"
    )
    confirmation_raw = serialize_confirmed_commit_receipt(confirmed, keys)

    assert (
        deserialize_commit_reservation(reservation_raw, keys, "family-main")
        == reservation
    )
    assert (
        deserialize_confirmed_commit_receipt(confirmation_raw, keys, "family-main")
        == confirmed
    )
    reservation_value = json.loads(reservation_raw)
    assert reservation_value["staged_write_receipt_json"] == (
        json.dumps(
            json.loads(reservation_value["staged_write_receipt_json"]),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    )
    assert reservation_value["commit_json"] == (
        '{"committed_at":"2026-08-07T10:11:12.345678Z",'
        '"fencing_token":9,"operation_id":"op_123","permit_id":"cpr_123",'
        '"revision":4,"semantic_checksum":"' + "d" * 64 + '"}'
    )
    assert (
        reservation_value["staged_write_receipt_sha256"]
        == hashlib.sha256(
            reservation_value["staged_write_receipt_json"].encode("ascii")
        ).hexdigest()
    )


def test_evidence_and_reconciled_head_proof_use_exact_digest_and_admin_nonce_hmac():
    from coordination.protocols import CoordinationEvidence, ReconciledHeadReceipt
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_evidence_sha256,
        deserialize_coordination_evidence,
        deserialize_reconciled_head_receipt,
        serialize_coordination_evidence,
        serialize_reconciled_head_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    evidence_digest = coordination_evidence_sha256(
        "family-main", 4, "d" * 64, "e" * 64, 8, 9
    )
    evidence = CoordinationEvidence(
        "family-main", 4, "d" * 64, "e" * 64, 8, 9, evidence_digest
    )
    proof = ReconciledHeadReceipt(
        "family-main",
        4,
        "d" * 64,
        "e" * 64,
        evidence_digest,
        keys.admin_nonce_hmac("admin-nonce"),
    )

    evidence_raw = serialize_coordination_evidence(evidence)
    proof_raw = serialize_reconciled_head_receipt(proof, keys)

    assert deserialize_coordination_evidence(evidence_raw) == evidence
    assert deserialize_reconciled_head_receipt(proof_raw, keys, "family-main") == proof
    assert json.loads(evidence_raw)["evidence_sha256"] == evidence_digest
    assert "admin-nonce" not in proof_raw
    assert json.loads(proof_raw)["admin_request_nonce_hmac"] == (
        keys.admin_nonce_hmac("admin-nonce")
    )


@pytest.mark.parametrize(
    "envelope", ("reservation", "confirmed", "reconciled", "evidence")
)
def test_commit_state_decoders_reject_duplicate_missing_extra_noncanonical_and_digest_drift(
    envelope,
):
    from coordination.protocols import (
        CoordinationError,
        CoordinationEvidence,
        ReconciledHeadReceipt,
    )
    from coordination.serialization import (
        RedisKeyBuilder,
        coordination_evidence_sha256,
        deserialize_commit_reservation,
        deserialize_confirmed_commit_receipt,
        deserialize_coordination_evidence,
        deserialize_reconciled_head_receipt,
        serialize_commit_reservation,
        serialize_confirmed_commit_receipt,
        serialize_coordination_evidence,
        serialize_reconciled_head_receipt,
    )

    keys = RedisKeyBuilder("test", "secret")
    reservation, confirmed = _commit_recovery_values()
    evidence_digest = coordination_evidence_sha256(
        "family-main", 4, "d" * 64, "e" * 64, 8, 9
    )
    evidence = CoordinationEvidence(
        "family-main", 4, "d" * 64, "e" * 64, 8, 9, evidence_digest
    )
    proof = ReconciledHeadReceipt(
        "family-main",
        4,
        "d" * 64,
        "e" * 64,
        evidence_digest,
        keys.admin_nonce_hmac("nonce"),
    )
    choices = {
        "reservation": (
            serialize_commit_reservation(reservation, keys, "nonce"),
            lambda raw: deserialize_commit_reservation(raw, keys, "family-main"),
        ),
        "confirmed": (
            serialize_confirmed_commit_receipt(confirmed, keys),
            lambda raw: deserialize_confirmed_commit_receipt(raw, keys, "family-main"),
        ),
        "reconciled": (
            serialize_reconciled_head_receipt(proof, keys),
            lambda raw: deserialize_reconciled_head_receipt(raw, keys, "family-main"),
        ),
        "evidence": (
            serialize_coordination_evidence(evidence),
            deserialize_coordination_evidence,
        ),
    }
    raw, decoder = choices[envelope]
    mutations = (
        raw.replace('"version":1', '"version":1,"version":1'),
        raw.replace(',"version":1', ',"unexpected":true,"version":1'),
        raw.replace(',"version":1', ""),
        raw.replace(":", ": ", 1),
        raw.replace("d" * 64, "0" * 64, 1),
    )
    for malformed in mutations:
        with pytest.raises(CoordinationError) as raised:
            decoder(malformed)
        assert raised.value.code == "COORDINATION_STATE_CORRUPT"


def test_coordination_state_digest_is_ordered_raw_and_marks_missing_values():
    from coordination.serialization import coordination_state_sha256

    raw = (None, "9", "4", None, '{"proof":1}')
    first = coordination_state_sha256(raw)

    assert (
        first
        == hashlib.sha256(
            (
                '{"schema":"shajra.coordination-state-raw","values":['
                '{"present":false},{"present":true,"value":"9"},'
                '{"present":true,"value":"4"},{"present":false},'
                '{"present":true,"value":"{\\"proof\\":1}"}],"version":1}'
            ).encode("ascii")
        ).hexdigest()
    )
    assert coordination_state_sha256(("9", None, "4", None, '{"proof":1}')) != first
    assert coordination_state_sha256((None, "9", "4", "", '{"proof":1}')) != first


def test_canonical_decimal_parser_covers_exact_signed_64_boundaries():
    from coordination.protocols import CoordinationError
    from coordination.serialization import parse_canonical_decimal

    assert parse_canonical_decimal("-9223372036854775808") == -(2**63)
    assert parse_canonical_decimal("9223372036854775807") == 2**63 - 1
    for value in (
        "-9223372036854775809",
        "9223372036854775808",
        "-0",
        "+1",
        "01",
    ):
        with pytest.raises(CoordinationError, match="COORDINATION_STATE_CORRUPT"):
            parse_canonical_decimal(value)
