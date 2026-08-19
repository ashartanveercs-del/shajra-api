"""Runtime construction for fail-closed legacy relationship coordination."""

from change_history import UpstashChangeHistoryStore
from config import Settings
from coordination import (
    CoordinationError,
    LeaseManager,
    RateLimiter,
    UpstashLeaseManager,
    UpstashRateLimiter,
)
from coordination.sdk import UpstashRedisAdapter
from coordination.serialization import RedisKeyBuilder


def coordination_configured(settings: Settings) -> bool:
    """Return whether all coordination metadata is present, without I/O."""
    token = (
        settings.upstash_redis_rest_token.get_secret_value()
        if settings.upstash_redis_rest_token
        else ""
    )
    key_secret = (
        settings.redis_key_hmac_secret.get_secret_value()
        if settings.redis_key_hmac_secret
        else ""
    )
    return all(
        str(value or "").strip()
        for value in (
            settings.upstash_redis_rest_url,
            token,
            settings.redis_namespace,
            key_secret,
        )
    )


def build_lease_manager(settings: Settings) -> LeaseManager:
    """Build the generic Upstash lease manager or fail closed."""
    if not coordination_configured(settings):
        raise CoordinationError("COORDINATION_UNINITIALIZED")

    url = str(settings.upstash_redis_rest_url).strip()
    namespace = str(settings.redis_namespace).strip()
    token = settings.upstash_redis_rest_token
    key_secret = settings.redis_key_hmac_secret
    if token is None or key_secret is None:
        raise CoordinationError("COORDINATION_UNINITIALIZED")

    try:
        redis = UpstashRedisAdapter.connect(url, token.get_secret_value())
        keys = RedisKeyBuilder(namespace, key_secret.get_secret_value())
        return UpstashLeaseManager(redis, keys)
    except CoordinationError:
        raise
    except Exception:  # noqa: BLE001 - collapse constructor details at the boundary.
        raise CoordinationError("COORDINATION_UNAVAILABLE") from None


def build_change_history_store(settings: Settings) -> UpstashChangeHistoryStore:
    redis, keys = _build_runtime_clients(settings)
    return UpstashChangeHistoryStore(redis, keys)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    redis, keys = _build_runtime_clients(settings)
    return UpstashRateLimiter(redis, keys)


def _build_runtime_clients(settings: Settings):
    if not coordination_configured(settings):
        raise CoordinationError("COORDINATION_UNINITIALIZED")

    token = settings.upstash_redis_rest_token
    key_secret = settings.redis_key_hmac_secret
    if token is None or key_secret is None:
        raise CoordinationError("COORDINATION_UNINITIALIZED")

    try:
        redis = UpstashRedisAdapter.connect(
            str(settings.upstash_redis_rest_url).strip(),
            token.get_secret_value(),
        )
        keys = RedisKeyBuilder(
            str(settings.redis_namespace).strip(),
            key_secret.get_secret_value(),
        )
        return redis, keys
    except CoordinationError:
        raise
    except Exception:  # noqa: BLE001 - collapse constructor details at the boundary.
        raise CoordinationError("COORDINATION_UNAVAILABLE") from None
