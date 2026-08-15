"""Thin synchronous adapter for the published upstash-redis 1.7.0 API."""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from typing import Any, Self

import httpx
from upstash_redis import Redis

from coordination.protocols import CoordinationError
from upstash_url import require_canonical_upstash_url


class UpstashRedisAdapter:
    """Invoke only nonce-aware EVAL operations with bounded retry behavior."""

    def __init__(self, client: Redis) -> None:
        self.client = client

    @classmethod
    def connect(cls, url: str, token: str) -> Self:
        try:
            require_canonical_upstash_url(url)
        except ValueError:
            raise CoordinationError("COORDINATION_STATE_CORRUPT") from None
        return cls(Redis(url=url, token=token, rest_retries=0))

    def eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[str],
        *,
        nonce_idempotent: bool,
    ) -> list[Any]:
        if (
            not isinstance(script, str)
            or not script
            or not all(isinstance(value, str) for value in keys)
            or not all(isinstance(value, str) for value in args)
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

        eval_keys = keys if isinstance(keys, list) else list(keys)
        eval_args = args if isinstance(args, list) else list(args)
        attempts = 2 if nonce_idempotent else 1
        for attempt in range(attempts):
            try:
                result = self.client.eval(script, eval_keys, eval_args)
                if inspect.isawaitable(result) or not isinstance(result, list):
                    raise CoordinationError("COORDINATION_UNAVAILABLE")
                return result
            except (
                httpx.TransportError,
                httpx.DecodingError,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ):
                if attempt + 1 < attempts:
                    continue
                raise CoordinationError("COORDINATION_UNAVAILABLE") from None
            except CoordinationError:
                raise
            except Exception:  # noqa: BLE001 - the SDK's HTTP layer rethrows broadly.
                raise CoordinationError("COORDINATION_UNAVAILABLE") from None
        raise CoordinationError("COORDINATION_UNAVAILABLE")


UpstashEvalAdapter = UpstashRedisAdapter
