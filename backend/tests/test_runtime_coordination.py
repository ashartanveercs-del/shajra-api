import importlib

import pytest
from config import Settings
from change_history import UpstashChangeHistoryStore
from coordination import CoordinationError, UpstashLeaseManager, UpstashRateLimiter


def _runtime_coordination():
    try:
        return importlib.import_module("runtime_coordination")
    except ModuleNotFoundError:
        pytest.fail("runtime_coordination factory is missing")


def _complete_settings():
    return Settings(
        app_env="test",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="synthetic-token",
        redis_namespace="test-1",
        redis_key_hmac_secret="synthetic-hmac-secret",
        _env_file=None,
    )


def test_coordination_configuration_requires_all_runtime_metadata(monkeypatch):
    runtime_coordination = _runtime_coordination()
    partial = Settings(
        app_env="test",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="synthetic-token",
        redis_namespace="test-1",
        _env_file=None,
    )
    monkeypatch.setattr(
        runtime_coordination.UpstashRedisAdapter,
        "connect",
        lambda *_args: pytest.fail("partial metadata constructed an Upstash client"),
    )

    assert runtime_coordination.coordination_configured(partial) is False
    with pytest.raises(CoordinationError) as raised:
        runtime_coordination.build_lease_manager(partial)

    assert raised.value.code == "COORDINATION_UNINITIALIZED"


def test_complete_coordination_factory_builds_generic_lease_manager(monkeypatch):
    runtime_coordination = _runtime_coordination()
    redis = object()
    calls = []

    def connect(url, token):
        calls.append((url, token))
        return redis

    monkeypatch.setattr(runtime_coordination.UpstashRedisAdapter, "connect", connect)

    settings = _complete_settings()
    manager = runtime_coordination.build_lease_manager(settings)

    assert runtime_coordination.coordination_configured(settings) is True
    assert isinstance(manager, UpstashLeaseManager)
    assert manager._redis is redis
    assert calls == [("https://example.upstash.io", "synthetic-token")]


def test_complete_coordination_factory_builds_history_and_rate_limit_stores(
    monkeypatch,
):
    runtime_coordination = _runtime_coordination()
    redis = object()
    monkeypatch.setattr(
        runtime_coordination.UpstashRedisAdapter,
        "connect",
        lambda *_args: redis,
    )

    history = runtime_coordination.build_change_history_store(_complete_settings())
    limiter = runtime_coordination.build_rate_limiter(_complete_settings())

    assert isinstance(history, UpstashChangeHistoryStore)
    assert isinstance(limiter, UpstashRateLimiter)
    assert history._redis is redis
    assert limiter._redis is redis
