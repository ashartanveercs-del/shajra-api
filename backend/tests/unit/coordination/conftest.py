"""Test fixtures for exact production-Lua execution."""

import faulthandler
import gc
from collections.abc import Iterator, Sequence
from typing import Any, Callable

# Lupa locks the first loaded runtime module on Linux. Load LuaJIT before
# fakeredis imports its default Lua 5.1 runtime.
# isort: off
import lupa.luajit21

# Keep one runtime alive so the later fakeredis import cannot replace LuaJIT.
_LUAJIT_BOOTSTRAP_RUNTIME = lupa.luajit21.LuaRuntime()

import fakeredis
import fakeredis.commands_mixins.scripting_mixin as scripting_mixin
# isort: on
import pytest
from fakeredis.commands_mixins.scripting_mixin import ScriptingCommandsMixin


_BIT_COMPAT_LUA = r"""
bit = {}
local function binary_u32(left, right, operation)
  left = left % 4294967296
  right = right % 4294967296
  local result = 0
  local place = 1
  for _ = 1, 32 do
    local left_bit = left % 2
    local right_bit = right % 2
    if operation == 'xor' and left_bit ~= right_bit then result = result + place end
    if operation == 'and' and left_bit == 1 and right_bit == 1 then
      result = result + place
    end
    left = math.floor(left / 2)
    right = math.floor(right / 2)
    place = place * 2
  end
  return result
end
function bit.bxor(...)
  local result = 0
  for index = 1, select('#', ...) do
    result = binary_u32(result, select(index, ...), 'xor')
  end
  return result
end
function bit.band(...)
  local result = 4294967295
  for index = 1, select('#', ...) do
    result = binary_u32(result, select(index, ...), 'and')
  end
  return result
end
function bit.bnot(value)
  return 4294967295 - (value % 4294967296)
end
function bit.rshift(value, width)
  return math.floor((value % 4294967296) / (2 ^ width))
end
function bit.ror(value, width)
  value = value % 4294967296
  local divisor = 2 ^ width
  return math.floor(value / divisor) + ((value % divisor) * (2 ^ (32 - width)))
end
"""

_BIT_SPY_LUA = r"""
local native_bit = bit
__shajra_bit_calls = 0
bit = {
  bxor = function(...)
    __shajra_bit_calls = __shajra_bit_calls + 1
    return native_bit.bxor(...)
  end,
  band = function(...)
    __shajra_bit_calls = __shajra_bit_calls + 1
    return native_bit.band(...)
  end,
  bnot = function(...)
    __shajra_bit_calls = __shajra_bit_calls + 1
    return native_bit.bnot(...)
  end,
  rshift = function(...)
    __shajra_bit_calls = __shajra_bit_calls + 1
    return native_bit.rshift(...)
  end,
  ror = function(...)
    __shajra_bit_calls = __shajra_bit_calls + 1
    return native_bit.ror(...)
  end,
}
"""


class ProductionLuaRedis:
    """Run shipped scripts through fakeredis and record exact script inputs."""

    def __init__(self, *, lua_modules: set[str] | None = None) -> None:
        self.client = fakeredis.FakeRedis(
            version=(7,), decode_responses=True, lua_modules=lua_modules
        )
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

    def install_bit_compatibility(self) -> None:
        self.client.eval("return 1", 0)
        connection = self.client.connection_pool.get_connection()
        try:
            server = connection._server
            server._lua_runtime.execute(_BIT_COMPAT_LUA)
            server._lua_expected_globals.add(b"bit")
        finally:
            self.client.connection_pool.release(connection)

    def install_bit_spy(self) -> None:
        self.client.eval("return 1", 0)
        connection = self.client.connection_pool.get_connection()
        try:
            server = connection._server
            server._lua_runtime.execute(_BIT_SPY_LUA)
            server._lua_expected_globals.add(b"__shajra_bit_calls")
        finally:
            self.client.connection_pool.release(connection)

    def runtime_implementation(self) -> str:
        self.client.eval("return 1", 0)
        connection = self.client.connection_pool.get_connection()
        try:
            raw = connection._server._lua_runtime.lua_implementation
            return raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
        finally:
            self.client.connection_pool.release(connection)


def _close_lua_harness(harness: ProductionLuaRedis) -> None:
    harness.client.close()
    harness.client.connection_pool.disconnect()
    gc.collect()


@pytest.fixture
def production_lua(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ProductionLuaRedis]:
    monkeypatch.setattr(scripting_mixin, "LUA_MODULE", lupa.luajit21)
    harness = ProductionLuaRedis(lua_modules={"bit"})
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
    was_enabled = faulthandler.is_enabled()
    if was_enabled:
        faulthandler.disable()
    try:
        yield harness
    finally:
        _close_lua_harness(harness)
        if was_enabled:
            faulthandler.enable()


@pytest.fixture
def production_lua_compat() -> Iterator[ProductionLuaRedis]:
    harness = ProductionLuaRedis()
    harness.install_bit_compatibility()
    was_enabled = faulthandler.is_enabled()
    if was_enabled:
        faulthandler.disable()
    try:
        yield harness
    finally:
        _close_lua_harness(harness)
        if was_enabled:
            faulthandler.enable()


@pytest.fixture
def production_lua_bit_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ProductionLuaRedis]:
    monkeypatch.setattr(scripting_mixin, "LUA_MODULE", lupa.luajit21)
    harness = ProductionLuaRedis(lua_modules={"bit"})
    harness.install_bit_spy()
    was_enabled = faulthandler.is_enabled()
    if was_enabled:
        faulthandler.disable()
    try:
        yield harness
    finally:
        _close_lua_harness(harness)
        if was_enabled:
            faulthandler.enable()
