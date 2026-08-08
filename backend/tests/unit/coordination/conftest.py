"""Test fixtures for exact production-Lua execution."""

from collections.abc import Sequence
from typing import Any, Callable

import fakeredis
import pytest
from fakeredis.commands_mixins.scripting_mixin import ScriptingCommandsMixin


class ProductionLuaRedis:
    """Run shipped scripts through fakeredis and record exact script inputs."""

    def __init__(self) -> None:
        self.client = fakeredis.FakeRedis(version=(7,), decode_responses=True)
        self.executed_scripts: list[str] = []
        self.command_overrides: dict[str, list[Any]] = {}
        self.before_eval: Callable[[str, Sequence[str], Sequence[str]], None] | None = (
            None
        )

    def eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[str],
        *,
        nonce_idempotent: bool,
    ) -> list[Any]:
        del nonce_idempotent
        self.executed_scripts.append(script)
        if self.before_eval is not None:
            self.before_eval(script, keys, args)
        result = self.client.eval(script, len(keys), *keys, *args)
        assert isinstance(result, list)
        return result


@pytest.fixture
def production_lua(monkeypatch: pytest.MonkeyPatch) -> ProductionLuaRedis:
    harness = ProductionLuaRedis()
    original = ScriptingCommandsMixin._lua_redis_call

    def intercept(
        socket: Any,
        lua_runtime: Any,
        expected_globals: set[Any],
        op: bytes,
        *args: Any,
    ) -> Any:
        queue = harness.command_overrides.get(op.decode("ascii"))
        if queue:
            override = queue.pop(0)
            if override is not None:
                return socket._convert_redis_result(lua_runtime, override)
        return original(socket, lua_runtime, expected_globals, op, *args)

    monkeypatch.setattr(ScriptingCommandsMixin, "_lua_redis_call", intercept)
    return harness
