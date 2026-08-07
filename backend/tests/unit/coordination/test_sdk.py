from __future__ import annotations

from unittest.mock import call, create_autospec, patch

import httpx
import pytest
from upstash_redis import Redis


def test_connect_constructs_upstash_1_7_client_with_sdk_retries_disabled():
    from coordination.sdk import UpstashRedisAdapter

    with patch("coordination.sdk.Redis", autospec=True) as redis_class:
        adapter = UpstashRedisAdapter.connect("https://example.upstash.io", "token")

    redis_class.assert_called_once_with(
        url="https://example.upstash.io", token="token", rest_retries=0
    )
    assert adapter.client is redis_class.return_value


def test_eval_uses_published_three_argument_shape_and_returns_tagged_result():
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    client.eval.return_value = ["OK", "LEASE_ACQUIRED", "payload"]
    adapter = UpstashRedisAdapter(client)
    script = "return {'OK','LEASE_ACQUIRED',ARGV[1]}"
    keys = ["{sj:v1:test:generic:digest}:lock"]
    args = ["nonce-input"]

    result = adapter.eval(script, keys, args, nonce_idempotent=True)

    assert result == ["OK", "LEASE_ACQUIRED", "payload"]
    client.eval.assert_called_once_with(script, keys, args)


def test_nonce_idempotent_eval_retries_one_ambiguous_transport_failure_byte_identically():
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    client.eval.side_effect = [
        httpx.ReadTimeout("response lost"),
        ["OK", "LEASE_REPLAYED", "original"],
    ]
    adapter = UpstashRedisAdapter(client)
    script = "return {'OK','LEASE_REPLAYED',ARGV[1]}"
    keys = ["{tag}:lock", "{tag}:receipt"]
    args = ["same-nonce", "same-input"]

    result = adapter.eval(script, keys, args, nonce_idempotent=True)

    assert result == ["OK", "LEASE_REPLAYED", "original"]
    assert client.eval.call_args_list == [
        call(script, keys, args),
        call(script, keys, args),
    ]


def test_transport_failure_is_not_retried_without_nonce_idempotence():
    from coordination.protocols import CoordinationError
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    client.eval.side_effect = httpx.ConnectError("not sent")
    adapter = UpstashRedisAdapter(client)

    with pytest.raises(CoordinationError) as raised:
        adapter.eval("return {'OK'}", ["{tag}:key"], ["arg"], nonce_idempotent=False)

    assert raised.value.code == "COORDINATION_UNAVAILABLE"
    assert str(raised.value) == "COORDINATION_UNAVAILABLE"
    assert client.eval.call_count == 1


def test_second_transport_failure_fails_closed_after_exactly_one_adapter_retry():
    from coordination.protocols import CoordinationError
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    client.eval.side_effect = [
        httpx.WriteError("ambiguous"),
        httpx.ReadError("ambiguous again"),
    ]
    adapter = UpstashRedisAdapter(client)

    with pytest.raises(CoordinationError, match="COORDINATION_UNAVAILABLE"):
        adapter.eval("return {'OK'}", ["{tag}:key"], ["nonce"], nonce_idempotent=True)

    assert client.eval.call_count == 2


def test_tagged_err_result_is_returned_without_retry_or_translation():
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    tagged = ["ERR", "NONCE_REUSE_CONFLICT"]
    client.eval.return_value = tagged
    adapter = UpstashRedisAdapter(client)

    assert (
        adapter.eval(
            "return {'ERR','NONCE_REUSE_CONFLICT'}",
            ["{tag}:receipt"],
            ["digest"],
            nonce_idempotent=True,
        )
        == tagged
    )
    assert client.eval.call_count == 1


def test_nontransport_sdk_failure_is_never_retried_and_fails_closed():
    from coordination.protocols import CoordinationError
    from coordination.sdk import UpstashRedisAdapter

    client = create_autospec(Redis, instance=True)
    client.eval.side_effect = ValueError("malformed SDK response")
    adapter = UpstashRedisAdapter(client)

    with pytest.raises(CoordinationError) as raised:
        adapter.eval("return {'OK'}", ["{tag}:key"], ["nonce"], nonce_idempotent=True)

    assert raised.value.code == "COORDINATION_UNAVAILABLE"
    assert "malformed" not in str(raised.value)
    assert client.eval.call_count == 1
