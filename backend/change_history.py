"""Durable, ordered admin undo history for serverless deployments."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Literal, Protocol, Sequence

from coordination import CoordinationError
from coordination.serialization import RedisKeyBuilder


HISTORY_READY_LUA = r"""
-- shajra:history-ready:v3
if redis.call('EXISTS', KEYS[1]) == 1 then return {'BUSY'} end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'GAP'} end
return {'READY'}
"""

HISTORY_ACTIVE_LUA = r"""
-- shajra:history-active:v1
local nonce = redis.call('GET', KEYS[1])
if not nonce then return {'INACTIVE'} end
return {'ACTIVE', nonce}
"""

HISTORY_PUSH_LUA = r"""
-- shajra:history-push:v3
local encoded = ARGV[1]
local maximum = tonumber(ARGV[2])
if type(encoded) ~= 'string' or not maximum or maximum < 1 then
  return {'CORRUPT'}
end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'BUSY'} end
if redis.call('EXISTS', KEYS[3]) == 1 then return {'GAP'} end
redis.call('LREM', KEYS[1], 0, encoded)
redis.call('LPUSH', KEYS[1], encoded)
redis.call('LTRIM', KEYS[1], 0, maximum - 1)
return {'PUSHED'}
"""

HISTORY_WRITE_BEGIN_LUA = r"""
-- shajra:history-write-begin:v2
local encoded = ARGV[1]
local nonce = ARGV[2]
if type(encoded) ~= 'string' or type(nonce) ~= 'string' or nonce == '' then
  return {'CORRUPT'}
end
if redis.call('EXISTS', KEYS[1]) == 1 then return {'BUSY'} end
local current = redis.call('GET', KEYS[2])
if current then
  local ok, guard = pcall(cjson.decode, current)
  if not ok or type(guard) ~= 'table' or guard['nonce'] == nil
      or guard['phase'] == nil then return {'CORRUPT'} end
  if guard['nonce'] == nonce then return {'BEGUN'} end
  if guard['phase'] ~= 'prepared' then return {'GAP'} end
  redis.call('SET', KEYS[2], encoded)
  return {'RECLAIMED'}
end
redis.call('SET', KEYS[2], encoded)
return {'BEGUN'}
"""

HISTORY_WRITE_MARK_STARTED_LUA = r"""
-- shajra:history-write-mark-started:v2
local nonce = ARGV[1]
local started_at = ARGV[2]
if type(nonce) ~= 'string' or nonce == '' or type(started_at) ~= 'string'
    or started_at == '' then return {'CORRUPT'} end
local current = redis.call('GET', KEYS[1])
if not current then return {'CORRUPT'} end
local ok, guard = pcall(cjson.decode, current)
if not ok or type(guard) ~= 'table' or guard['nonce'] == nil
    or guard['phase'] == nil then return {'CORRUPT'} end
if guard['nonce'] ~= nonce then return {'GAP'} end
if guard['phase'] == 'started' or guard['phase'] == 'bound' then
  return {'STARTED', current}
end
if guard['phase'] ~= 'prepared' then return {'CORRUPT'} end
guard['phase'] = 'started'
guard['started_at'] = started_at
local encoded = cjson.encode(guard)
redis.call('SET', KEYS[1], encoded)
return {'STARTED', encoded}
"""

HISTORY_WRITE_BIND_TARGET_LUA = r"""
-- shajra:history-write-bind-target:v1
local nonce = ARGV[1]
local encoded_operation = ARGV[2]
local bound_at = ARGV[3]
if type(nonce) ~= 'string' or nonce == ''
    or type(encoded_operation) ~= 'string'
    or type(bound_at) ~= 'string' or bound_at == '' then return {'CORRUPT'} end
if redis.call('EXISTS', KEYS[1]) == 1 then return {'BUSY'} end
local current = redis.call('GET', KEYS[2])
if not current then return {'CORRUPT'} end
local guard_ok, guard = pcall(cjson.decode, current)
local operation_ok, operation = pcall(cjson.decode, encoded_operation)
if not guard_ok or type(guard) ~= 'table' or guard['nonce'] == nil
    or guard['phase'] == nil or type(guard['operation']) ~= 'table'
    or not operation_ok or type(operation) ~= 'table'
    or type(operation['action']) ~= 'string'
    or type(operation['record_id']) ~= 'string'
    or operation['record_id'] == '' then return {'CORRUPT'} end
if guard['nonce'] ~= nonce then return {'GAP'} end
if guard['operation']['action'] ~= operation['action'] then return {'MISMATCH'} end
if guard['phase'] == 'bound' then
  if guard['operation']['record_id'] ~= operation['record_id'] then
    return {'MISMATCH'}
  end
  return {'BOUND', current}
end
if guard['phase'] ~= 'started' then return {'CORRUPT'} end
if (operation['action'] == 'update' or operation['action'] == 'delete')
    and guard['operation']['record_id'] ~= operation['record_id'] then
  return {'MISMATCH'}
end
guard['phase'] = 'bound'
guard['operation'] = operation
guard['bound_at'] = bound_at
local encoded = cjson.encode(guard)
redis.call('SET', KEYS[2], encoded)
return {'BOUND', encoded}
"""

HISTORY_WRITE_COMMIT_LUA = r"""
-- shajra:history-write-commit:v3
local nonce = ARGV[1]
local encoded = ARGV[2]
local maximum = tonumber(ARGV[3])
local repair_guard = ARGV[4]
if type(nonce) ~= 'string' or nonce == '' or type(encoded) ~= 'string'
    or not maximum or maximum < 1 or type(repair_guard) ~= 'string' then
  return {'CORRUPT'}
end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'BUSY'} end
local current = redis.call('GET', KEYS[3])
if not current then
  if redis.call('LINDEX', KEYS[1], 0) == encoded then return {'COMMITTED'} end
  redis.call('SET', KEYS[3], repair_guard)
  return {'GAP'}
end
local ok, guard = pcall(cjson.decode, current)
if not ok or type(guard) ~= 'table' or guard['nonce'] == nil
    or guard['phase'] == nil or type(guard['operation']) ~= 'table' then
  return {'CORRUPT'}
end
if guard['nonce'] ~= nonce then return {'GAP'} end
if guard['phase'] ~= 'bound' then return {'GAP'} end
local entry_ok, entry = pcall(cjson.decode, encoded)
if not entry_ok or type(entry) ~= 'table'
    or type(entry['action']) ~= 'string'
    or type(entry['record_id']) ~= 'string' then return {'CORRUPT'} end
if guard['operation']['action'] ~= entry['action']
    or guard['operation']['record_id'] ~= entry['record_id'] then
  return {'MISMATCH'}
end
redis.call('LREM', KEYS[1], 0, encoded)
redis.call('LPUSH', KEYS[1], encoded)
redis.call('LTRIM', KEYS[1], 0, maximum - 1)
redis.call('DEL', KEYS[3])
return {'COMMITTED'}
"""

HISTORY_WRITE_ABORT_LUA = r"""
-- shajra:history-write-abort:v2
local nonce = ARGV[1]
if type(nonce) ~= 'string' or nonce == '' then return {'CORRUPT'} end
local current = redis.call('GET', KEYS[1])
if not current then return {'ABORTED'} end
local ok, guard = pcall(cjson.decode, current)
if not ok or type(guard) ~= 'table' or guard['nonce'] == nil then
  return {'CORRUPT'}
end
if guard['nonce'] ~= nonce then return {'GAP'} end
redis.call('DEL', KEYS[1])
return {'ABORTED'}
"""

HISTORY_WRITE_STATUS_LUA = r"""
-- shajra:history-write-status:v1
local encoded = redis.call('GET', KEYS[1])
if not encoded then return {'INACTIVE'} end
return {'ACTIVE', encoded}
"""

HISTORY_WRITE_RESOLVE_LUA = r"""
-- shajra:history-write-resolve:v3
local nonce = ARGV[1]
local resolution = ARGV[2]
local encoded = ARGV[3]
local maximum = tonumber(ARGV[4])
if type(nonce) ~= 'string' or nonce == '' then return {'CORRUPT'} end
if redis.call('EXISTS', KEYS[2]) == 1 then return {'BUSY'} end
local current = redis.call('GET', KEYS[3])
if not current then return {'INACTIVE'} end
local ok, guard = pcall(cjson.decode, current)
if not ok or type(guard) ~= 'table' or guard['nonce'] == nil then
  return {'CORRUPT'}
end
if guard['nonce'] ~= nonce then return {'GAP'} end
if resolution == 'abort' then
  redis.call('DEL', KEYS[1])
  redis.call('DEL', KEYS[3])
  return {'RESOLVED'}
end
if resolution ~= 'commit' or type(encoded) ~= 'string'
    or not maximum or maximum < 1 then return {'CORRUPT'} end
local operation = guard['operation']
local entry_ok, entry = pcall(cjson.decode, encoded)
if guard['phase'] ~= 'bound' or type(operation) ~= 'table'
    or not entry_ok or type(entry) ~= 'table'
    or type(operation['action']) ~= 'string'
    or type(operation['record_id']) ~= 'string'
    or type(entry['action']) ~= 'string'
    or type(entry['record_id']) ~= 'string' then return {'CORRUPT'} end
if operation['action'] ~= entry['action']
    or operation['record_id'] ~= entry['record_id'] then return {'MISMATCH'} end
redis.call('LREM', KEYS[1], 0, encoded)
redis.call('LPUSH', KEYS[1], encoded)
redis.call('LTRIM', KEYS[1], 0, maximum - 1)
redis.call('DEL', KEYS[3])
return {'RESOLVED'}
"""

HISTORY_LIST_LUA = r"""
-- shajra:history-list:v2
local maximum = tonumber(ARGV[1])
if not maximum or maximum < 1 then return {'CORRUPT'} end
local values = redis.call('LRANGE', KEYS[1], 0, maximum - 1)
table.insert(values, 1, 'HISTORY')
return values
"""

HISTORY_CLAIM_LUA = r"""
-- shajra:history-claim:v4
local nonce = ARGV[1]
local empty_receipt = ARGV[2]
local terminal = redis.call('GET', KEYS[4])
if terminal then return {'TERMINAL', terminal} end

local claimed = redis.call('GET', KEYS[3])
if claimed then
  if redis.call('GET', KEYS[2]) ~= nonce then return {'CORRUPT'} end
  if redis.call('LINDEX', KEYS[1], 0) ~= claimed then return {'CORRUPT'} end
  return {'RESUMED', claimed, redis.call('GET', KEYS[5]) or ''}
end

if redis.call('EXISTS', KEYS[2]) == 1 then return {'BUSY'} end
local write_guard = redis.call('GET', KEYS[6])
if write_guard then
  local ok, guard = pcall(cjson.decode, write_guard)
  if not ok or type(guard) ~= 'table' or guard['phase'] == nil then
    return {'CORRUPT'}
  end
  if guard['phase'] ~= 'prepared' then return {'GAP'} end
  redis.call('DEL', KEYS[6])
end
local encoded = redis.call('LINDEX', KEYS[1], 0)
if not encoded then
  redis.call('SET', KEYS[4], empty_receipt)
  return {'TERMINAL', empty_receipt}
end
redis.call('SET', KEYS[2], nonce)
redis.call('SET', KEYS[3], encoded)
return {'CLAIMED', encoded, ''}
"""

HISTORY_MARK_APPLYING_LUA = r"""
-- shajra:history-mark-applying:v3
local nonce = ARGV[1]
local encoded_context = ARGV[2]
local terminal = redis.call('GET', KEYS[4])
if terminal then return {'TERMINAL', terminal} end
if redis.call('GET', KEYS[2]) ~= nonce then return {'CORRUPT'} end
local claimed = redis.call('GET', KEYS[3])
if not claimed or redis.call('LINDEX', KEYS[1], 0) ~= claimed then
  return {'CORRUPT'}
end
local existing = redis.call('GET', KEYS[5])
if existing and existing ~= encoded_context then return {'CONFLICT'} end
if not existing then redis.call('SET', KEYS[5], encoded_context) end
return {'APPLYING', encoded_context}
"""

HISTORY_COMPLETE_LUA = r"""
-- shajra:history-complete:v3
local nonce = ARGV[1]
local completed_receipt = ARGV[2]
local terminal = redis.call('GET', KEYS[3])
if terminal then return {'TERMINAL', terminal} end
if redis.call('GET', KEYS[1]) ~= nonce then return {'CORRUPT'} end
local claimed = redis.call('GET', KEYS[2])
if not claimed or redis.call('LINDEX', KEYS[4], 0) ~= claimed then
  return {'CORRUPT'}
end
local removed = redis.call('LPOP', KEYS[4])
if removed ~= claimed then return {'CORRUPT'} end
redis.call('SET', KEYS[3], completed_receipt)
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[5])
return {'TERMINAL', completed_receipt}
"""

HISTORY_ABORT_LUA = r"""
-- shajra:history-abort:v3
local nonce = ARGV[1]
local terminal = redis.call('GET', KEYS[4])
if terminal then return {'TERMINAL', terminal} end
if redis.call('GET', KEYS[2]) ~= nonce then return {'CORRUPT'} end
local claimed = redis.call('GET', KEYS[3])
if not claimed or redis.call('LINDEX', KEYS[1], 0) ~= claimed then
  return {'CORRUPT'}
end
redis.call('DEL', KEYS[3])
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[5])
return {'RETRYABLE'}
"""

_MAX_ENTRY_BYTES = 1_000_000
_MAX_RESULT_BYTES = 100_000
_MAX_CONTEXT_BYTES = 1_000_000
_MAX_GUARD_BYTES = 100_000


class EvalAdapter(Protocol):
    def eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[str],
        *,
        nonce_idempotent: bool,
    ) -> list[Any]: ...


HistoryClaimState = Literal["claimed", "resumed", "completed", "empty", "busy"]


@dataclass(frozen=True, slots=True)
class HistoryClaim:
    state: HistoryClaimState
    entry: dict[str, object] | None = None
    result: dict[str, object] | None = None
    context: dict[str, object] | None = None


HistoryWritePhase = Literal["prepared", "started", "bound"]


@dataclass(frozen=True, slots=True)
class HistoryWriteStatus:
    nonce: str
    phase: HistoryWritePhase
    operation: dict[str, object]
    prepared_at: str
    started_at: str | None = None
    bound_at: str | None = None


class ChangeHistoryStore(Protocol):
    def ensure_ready(self) -> None: ...

    def push(self, entry: dict[str, object]) -> None: ...

    def begin_write(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None: ...

    def mark_write_started(self, request_nonce: str) -> None: ...

    def bind_write_target(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None: ...

    def commit_write(
        self, request_nonce: str, entry: dict[str, object]
    ) -> None: ...

    def abort_write(self, request_nonce: str) -> None: ...

    def write_status(self) -> HistoryWriteStatus | None: ...

    def resolve_write(
        self,
        request_nonce: str,
        entry: dict[str, object] | None,
    ) -> None: ...

    def list(self) -> list[dict[str, object]]: ...

    def active_nonce(self) -> str | None: ...

    def claim(self, request_nonce: str) -> HistoryClaim: ...

    def mark_applying(
        self, request_nonce: str, context: dict[str, object]
    ) -> dict[str, object]: ...

    def complete(
        self, request_nonce: str, result: dict[str, object]
    ) -> dict[str, object]: ...

    def abort(self, request_nonce: str) -> None: ...


class InMemoryChangeHistoryStore:
    def __init__(
        self,
        entries: list[dict[str, object]] | None = None,
        *,
        max_entries: int = 50,
    ) -> None:
        self._entries = entries if entries is not None else []
        self._max_entries = _valid_max_entries(max_entries)
        self._active_nonce: str | None = None
        self._write_guard: HistoryWriteStatus | None = None
        self._claims: dict[str, dict[str, object]] = {}
        self._contexts: dict[str, dict[str, object]] = {}
        self._terminal: dict[
            str, tuple[Literal["completed", "empty"], dict[str, object] | None]
        ] = {}
        self._lock = RLock()

    def ensure_ready(self) -> None:
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            if self._write_guard is not None:
                raise CoordinationError("UNDO_HISTORY_GAP")

    def push(self, entry: dict[str, object]) -> None:
        canonical = _decode_entry(_encode_entry(entry))
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            if self._write_guard is not None:
                raise CoordinationError("UNDO_HISTORY_GAP")
            self._entries[:] = [
                current for current in self._entries if current != canonical
            ]
            self._entries.append(canonical)
            if len(self._entries) > self._max_entries:
                del self._entries[: len(self._entries) - self._max_entries]

    def begin_write(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        canonical_operation = _decode_operation(_encode_operation(operation))
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            if self._write_guard is not None:
                if self._write_guard.nonce == nonce:
                    return
                if self._write_guard.phase != "prepared":
                    raise CoordinationError("UNDO_HISTORY_GAP")
            self._write_guard = HistoryWriteStatus(
                nonce=nonce,
                phase="prepared",
                operation=canonical_operation,
                prepared_at=_now_iso(),
            )

    def mark_write_started(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        with self._lock:
            guard = self._write_guard
            if guard is None:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            if guard.nonce != nonce:
                raise CoordinationError("UNDO_HISTORY_GAP")
            if guard.phase in {"started", "bound"}:
                return
            self._write_guard = HistoryWriteStatus(
                nonce=guard.nonce,
                phase="started",
                operation=deepcopy(guard.operation),
                prepared_at=guard.prepared_at,
                started_at=_now_iso(),
            )

    def bind_write_target(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        canonical_operation = _decode_operation(_encode_operation(operation))
        if not canonical_operation["record_id"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            guard = self._write_guard
            if guard is None:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            if guard.nonce != nonce:
                raise CoordinationError("UNDO_HISTORY_GAP")
            if guard.operation.get("action") != canonical_operation.get("action"):
                raise CoordinationError("UNDO_HISTORY_GAP")
            if guard.phase == "bound":
                if guard.operation != canonical_operation:
                    raise CoordinationError("UNDO_HISTORY_GAP")
                return
            if guard.phase != "started":
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            if (
                canonical_operation["action"] in {"update", "delete"}
                and guard.operation.get("record_id")
                != canonical_operation.get("record_id")
            ):
                raise CoordinationError("UNDO_HISTORY_GAP")
            self._write_guard = HistoryWriteStatus(
                nonce=guard.nonce,
                phase="bound",
                operation=canonical_operation,
                prepared_at=guard.prepared_at,
                started_at=guard.started_at,
                bound_at=_now_iso(),
            )

    def commit_write(
        self, request_nonce: str, entry: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        canonical = _decode_entry(_encode_entry(entry))
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            if self._write_guard is None:
                if self._entries and self._entries[-1] == canonical:
                    return
                self._write_guard = _repair_guard(nonce, canonical)
                raise CoordinationError("UNDO_HISTORY_GAP")
            if self._write_guard.nonce != nonce:
                raise CoordinationError("UNDO_HISTORY_GAP")
            if self._write_guard.phase != "bound":
                raise CoordinationError("UNDO_HISTORY_GAP")
            if not _operation_matches_entry(
                self._write_guard.operation, canonical
            ):
                raise CoordinationError("UNDO_HISTORY_GAP")
            self._entries[:] = [
                current for current in self._entries if current != canonical
            ]
            self._entries.append(canonical)
            if len(self._entries) > self._max_entries:
                del self._entries[: len(self._entries) - self._max_entries]
            self._write_guard = None

    def abort_write(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        with self._lock:
            if self._write_guard is None:
                return
            if self._write_guard.nonce != nonce:
                raise CoordinationError("UNDO_HISTORY_GAP")
            self._write_guard = None

    def write_status(self) -> HistoryWriteStatus | None:
        with self._lock:
            return deepcopy(self._write_guard)

    def resolve_write(
        self,
        request_nonce: str,
        entry: dict[str, object] | None,
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        canonical = _decode_entry(_encode_entry(entry)) if entry is not None else None
        with self._lock:
            if self._active_nonce is not None:
                raise CoordinationError("UNDO_IN_PROGRESS")
            guard = self._write_guard
            if guard is None:
                return
            if guard.nonce != nonce:
                raise CoordinationError("UNDO_HISTORY_GAP")
            if canonical is not None:
                if guard.phase != "bound" or not _operation_matches_entry(
                    guard.operation, canonical
                ):
                    raise CoordinationError("UNDO_HISTORY_GAP")
                self._entries[:] = [
                    current for current in self._entries if current != canonical
                ]
                self._entries.append(canonical)
                if len(self._entries) > self._max_entries:
                    del self._entries[: len(self._entries) - self._max_entries]
            else:
                self._entries.clear()
            self._write_guard = None

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            return deepcopy(list(reversed(self._entries)))

    def active_nonce(self) -> str | None:
        with self._lock:
            return self._active_nonce

    def claim(self, request_nonce: str) -> HistoryClaim:
        nonce = _valid_nonce(request_nonce)
        with self._lock:
            terminal = self._terminal.get(nonce)
            if terminal is not None:
                state, result = terminal
                return HistoryClaim(state, result=deepcopy(result))
            retained = self._claims.get(nonce)
            if retained is not None:
                if self._active_nonce != nonce or not self._entries:
                    raise CoordinationError("COORDINATION_STATE_CORRUPT")
                if self._entries[-1] != retained:
                    raise CoordinationError("COORDINATION_STATE_CORRUPT")
                return HistoryClaim(
                    "resumed",
                    entry=deepcopy(retained),
                    context=deepcopy(self._contexts.get(nonce)),
                )
            if self._active_nonce is not None:
                return HistoryClaim("busy")
            if self._write_guard is not None:
                if self._write_guard.phase != "prepared":
                    raise CoordinationError("UNDO_HISTORY_GAP")
                self._write_guard = None
            if not self._entries:
                self._terminal[nonce] = ("empty", None)
                return HistoryClaim("empty")
            canonical = _decode_entry(_encode_entry(self._entries[-1]))
            self._active_nonce = nonce
            self._claims[nonce] = canonical
            return HistoryClaim("claimed", entry=deepcopy(canonical))

    def mark_applying(
        self, request_nonce: str, context: dict[str, object]
    ) -> dict[str, object]:
        nonce = _valid_nonce(request_nonce)
        canonical_context = _decode_context(_encode_context(context))
        with self._lock:
            claimed = self._claims.get(nonce)
            if (
                self._active_nonce != nonce
                or claimed is None
                or not self._entries
                or self._entries[-1] != claimed
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            retained = self._contexts.get(nonce)
            if retained is not None and retained != canonical_context:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            self._contexts[nonce] = canonical_context
            return deepcopy(canonical_context)

    def complete(
        self, request_nonce: str, result: dict[str, object]
    ) -> dict[str, object]:
        nonce = _valid_nonce(request_nonce)
        canonical_result = _decode_result(_encode_result(result))
        with self._lock:
            terminal = self._terminal.get(nonce)
            if terminal is not None:
                state, retained = terminal
                if state != "completed" or retained is None:
                    raise CoordinationError("COORDINATION_STATE_CORRUPT")
                return deepcopy(retained)
            claimed = self._claims.get(nonce)
            if (
                self._active_nonce != nonce
                or claimed is None
                or not self._entries
                or self._entries[-1] != claimed
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            self._entries.pop()
            self._claims.pop(nonce)
            self._contexts.pop(nonce, None)
            self._active_nonce = None
            self._terminal[nonce] = ("completed", canonical_result)
            return deepcopy(canonical_result)

    def abort(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        with self._lock:
            if nonce in self._terminal:
                return
            claimed = self._claims.get(nonce)
            if (
                self._active_nonce != nonce
                or claimed is None
                or not self._entries
                or self._entries[-1] != claimed
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            self._claims.pop(nonce)
            self._contexts.pop(nonce, None)
            self._active_nonce = None


class UpstashChangeHistoryStore:
    def __init__(
        self,
        redis: EvalAdapter,
        keys: RedisKeyBuilder,
        *,
        max_entries: int = 50,
    ) -> None:
        self._redis = redis
        self._keys = keys
        self._max_entries = _valid_max_entries(max_entries)

    def ensure_ready(self) -> None:
        result = self._redis.eval(
            HISTORY_READY_LUA,
            [self._keys.history_active(), self._keys.history_write_guard()],
            [],
            nonce_idempotent=False,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result != ["READY"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def push(self, entry: dict[str, object]) -> None:
        result = self._redis.eval(
            HISTORY_PUSH_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_write_guard(),
            ],
            [_encode_entry(entry), str(self._max_entries)],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result != ["PUSHED"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def begin_write(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        encoded_guard = _encode_guard(
            HistoryWriteStatus(
                nonce=nonce,
                phase="prepared",
                operation=_decode_operation(_encode_operation(operation)),
                prepared_at=_now_iso(),
            )
        )
        result = self._redis.eval(
            HISTORY_WRITE_BEGIN_LUA,
            [self._keys.history_active(), self._keys.history_write_guard()],
            [encoded_guard, nonce],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result not in (["BEGUN"], ["RECLAIMED"]):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def mark_write_started(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        result = self._redis.eval(
            HISTORY_WRITE_MARK_STARTED_LUA,
            [self._keys.history_write_guard()],
            [nonce, _now_iso()],
            nonce_idempotent=True,
        )
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if len(result) != 2 or result[0] != "STARTED":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        _decode_guard(result[1])

    def bind_write_target(
        self, request_nonce: str, operation: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        canonical_operation = _decode_operation(_encode_operation(operation))
        if not canonical_operation["record_id"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        result = self._redis.eval(
            HISTORY_WRITE_BIND_TARGET_LUA,
            [self._keys.history_active(), self._keys.history_write_guard()],
            [nonce, _encode_operation(canonical_operation), _now_iso()],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result in (["GAP"], ["MISMATCH"]):
            raise CoordinationError("UNDO_HISTORY_GAP")
        if len(result) != 2 or result[0] != "BOUND":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        _decode_guard(result[1])

    def commit_write(
        self, request_nonce: str, entry: dict[str, object]
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        encoded_entry = _encode_entry(entry)
        repair_guard = _encode_guard(_repair_guard(nonce, entry))
        result = self._redis.eval(
            HISTORY_WRITE_COMMIT_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_write_guard(),
            ],
            [nonce, encoded_entry, str(self._max_entries), repair_guard],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result in (["GAP"], ["MISMATCH"]):
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result != ["COMMITTED"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def abort_write(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        result = self._redis.eval(
            HISTORY_WRITE_ABORT_LUA,
            [self._keys.history_write_guard()],
            [nonce],
            nonce_idempotent=True,
        )
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result != ["ABORTED"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def write_status(self) -> HistoryWriteStatus | None:
        result = self._redis.eval(
            HISTORY_WRITE_STATUS_LUA,
            [self._keys.history_write_guard()],
            [],
            nonce_idempotent=False,
        )
        if result == ["INACTIVE"]:
            return None
        if len(result) == 2 and result[0] == "ACTIVE":
            return _decode_guard(result[1])
        raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def resolve_write(
        self,
        request_nonce: str,
        entry: dict[str, object] | None,
    ) -> None:
        nonce = _valid_nonce(request_nonce)
        resolution = "commit" if entry is not None else "abort"
        encoded_entry = _encode_entry(entry) if entry is not None else ""
        result = self._redis.eval(
            HISTORY_WRITE_RESOLVE_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_write_guard(),
            ],
            [nonce, resolution, encoded_entry, str(self._max_entries)],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            raise CoordinationError("UNDO_IN_PROGRESS")
        if result in (["GAP"], ["MISMATCH"]):
            raise CoordinationError("UNDO_HISTORY_GAP")
        if result not in (["RESOLVED"], ["INACTIVE"]):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def list(self) -> list[dict[str, object]]:
        result = self._redis.eval(
            HISTORY_LIST_LUA,
            [self._keys.history_entries()],
            [str(self._max_entries)],
            nonce_idempotent=False,
        )
        if not result or result[0] != "HISTORY":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return [_decode_entry(value) for value in result[1:]]

    def active_nonce(self) -> str | None:
        result = self._redis.eval(
            HISTORY_ACTIVE_LUA,
            [self._keys.history_active()],
            [],
            nonce_idempotent=False,
        )
        if result == ["INACTIVE"]:
            return None
        if len(result) == 2 and result[0] == "ACTIVE":
            return _valid_nonce(result[1])
        raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def claim(self, request_nonce: str) -> HistoryClaim:
        nonce = _valid_nonce(request_nonce)
        result = self._redis.eval(
            HISTORY_CLAIM_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_claim(nonce),
                self._keys.history_result(nonce),
                self._keys.history_context(nonce),
                self._keys.history_write_guard(),
            ],
            [nonce, _encode_terminal("empty", None)],
            nonce_idempotent=True,
        )
        if result == ["BUSY"]:
            return HistoryClaim("busy")
        if result == ["GAP"]:
            raise CoordinationError("UNDO_HISTORY_GAP")
        if len(result) == 2 and result[0] == "TERMINAL":
            state, completed = _decode_terminal(result[1])
            return HistoryClaim(state, result=completed)
        if len(result) == 3 and result[0] in {"CLAIMED", "RESUMED"}:
            claim_state: HistoryClaimState = (
                "claimed" if result[0] == "CLAIMED" else "resumed"
            )
            context = _decode_context(result[2]) if result[2] else None
            return HistoryClaim(
                claim_state,
                entry=_decode_entry(result[1]),
                context=context,
            )
        raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def mark_applying(
        self, request_nonce: str, context: dict[str, object]
    ) -> dict[str, object]:
        nonce = _valid_nonce(request_nonce)
        encoded_context = _encode_context(context)
        result = self._redis.eval(
            HISTORY_MARK_APPLYING_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_claim(nonce),
                self._keys.history_result(nonce),
                self._keys.history_context(nonce),
            ],
            [nonce, encoded_context],
            nonce_idempotent=True,
        )
        if result == ["CONFLICT"]:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        if len(result) != 2 or result[0] != "APPLYING":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return _decode_context(result[1])

    def complete(
        self, request_nonce: str, result: dict[str, object]
    ) -> dict[str, object]:
        nonce = _valid_nonce(request_nonce)
        response = self._redis.eval(
            HISTORY_COMPLETE_LUA,
            [
                self._keys.history_active(),
                self._keys.history_claim(nonce),
                self._keys.history_result(nonce),
                self._keys.history_entries(),
                self._keys.history_context(nonce),
            ],
            [
                nonce,
                _encode_terminal("completed", result),
            ],
            nonce_idempotent=True,
        )
        if len(response) != 2 or response[0] != "TERMINAL":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        state, completed = _decode_terminal(response[1])
        if state != "completed" or completed is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return completed

    def abort(self, request_nonce: str) -> None:
        nonce = _valid_nonce(request_nonce)
        result = self._redis.eval(
            HISTORY_ABORT_LUA,
            [
                self._keys.history_entries(),
                self._keys.history_active(),
                self._keys.history_claim(nonce),
                self._keys.history_result(nonce),
                self._keys.history_context(nonce),
            ],
            [nonce],
            nonce_idempotent=True,
        )
        if result == ["RETRYABLE"]:
            return
        if len(result) == 2 and result[0] == "TERMINAL":
            return
        raise CoordinationError("COORDINATION_STATE_CORRUPT")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repair_guard(
    request_nonce: str,
    entry: dict[str, object],
) -> HistoryWriteStatus:
    return HistoryWriteStatus(
        nonce=_valid_nonce(request_nonce),
        phase="bound",
        operation={
            "action": entry.get("action"),
            "record_id": entry.get("record_id"),
        },
        prepared_at=str(entry.get("timestamp") or _now_iso()),
        started_at=_now_iso(),
        bound_at=_now_iso(),
    )


def _operation_matches_entry(
    operation: dict[str, object], entry: dict[str, object]
) -> bool:
    return (
        operation.get("action") == entry.get("action")
        and operation.get("record_id") == entry.get("record_id")
    )


def _encode_guard(status: HistoryWriteStatus) -> str:
    _validate_guard(status)
    return _encode_json(
        {
            "nonce": status.nonce,
            "phase": status.phase,
            "operation": status.operation,
            "prepared_at": status.prepared_at,
            "started_at": status.started_at,
            "bound_at": status.bound_at,
        },
        _MAX_GUARD_BYTES,
    )


def _decode_guard(value: object) -> HistoryWriteStatus:
    payload = _decode_json(value, _MAX_GUARD_BYTES)
    if set(payload) != {
        "nonce",
        "phase",
        "operation",
        "prepared_at",
        "started_at",
        "bound_at",
    }:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    operation = payload.get("operation")
    if not isinstance(operation, dict):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    raw_nonce = payload.get("nonce")
    if not isinstance(raw_nonce, str):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    raw_phase = payload.get("phase")
    if raw_phase not in {"prepared", "started", "bound"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    raw_prepared_at = payload.get("prepared_at")
    raw_started_at = payload.get("started_at")
    raw_bound_at = payload.get("bound_at")
    if not isinstance(raw_prepared_at, str) or not raw_prepared_at:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if raw_started_at is not None and not isinstance(raw_started_at, str):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if raw_bound_at is not None and not isinstance(raw_bound_at, str):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    status = HistoryWriteStatus(
        nonce=_valid_nonce(raw_nonce),
        phase=raw_phase,  # type: ignore[arg-type]
        operation=_decode_operation(_encode_operation(operation)),
        prepared_at=raw_prepared_at,
        started_at=raw_started_at,
        bound_at=raw_bound_at,
    )
    _validate_guard(status)
    return status


def _validate_guard(status: HistoryWriteStatus) -> None:
    _valid_nonce(status.nonce)
    if status.phase not in {"prepared", "started", "bound"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    _validate_operation(status.operation)
    if not status.prepared_at:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if status.phase == "prepared" and (
        status.started_at is not None or status.bound_at is not None
    ):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if status.phase == "started" and (
        not status.started_at or status.bound_at is not None
    ):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if status.phase == "bound" and (
        not status.started_at
        or not status.bound_at
        or not status.operation.get("record_id")
    ):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")


def _encode_operation(operation: dict[str, object]) -> str:
    _validate_operation(operation)
    return _encode_json(operation, _MAX_GUARD_BYTES)


def _decode_operation(value: object) -> dict[str, object]:
    operation = _decode_json(value, _MAX_GUARD_BYTES)
    _validate_operation(operation)
    return operation


def _validate_operation(operation: dict[str, object]) -> None:
    if set(operation) != {"action", "record_id"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    action = operation.get("action")
    record_id = operation.get("record_id")
    if action not in {"approve", "create", "delete", "update"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if not isinstance(record_id, str) or len(record_id) > 256:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")


def _valid_max_entries(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("max_entries must be between 1 and 100")
    return value


def _valid_nonce(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or "\x00" in value:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return value


def _encode_entry(entry: dict[str, object]) -> str:
    _validate_entry(entry)
    return _encode_json(entry, _MAX_ENTRY_BYTES)


def _decode_entry(value: object) -> dict[str, object]:
    decoded = _decode_json(value, _MAX_ENTRY_BYTES)
    _validate_entry(decoded)
    return decoded


def _encode_result(result: dict[str, object]) -> str:
    if not isinstance(result, dict):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return _encode_json(result, _MAX_RESULT_BYTES)


def _decode_result(value: object) -> dict[str, object]:
    return _decode_json(value, _MAX_RESULT_BYTES)


def _encode_context(context: dict[str, object]) -> str:
    if not isinstance(context, dict):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return _encode_json(context, _MAX_CONTEXT_BYTES)


def _decode_context(value: object) -> dict[str, object]:
    return _decode_json(value, _MAX_CONTEXT_BYTES)


def _encode_terminal(
    state: Literal["completed", "empty"], result: dict[str, object] | None
) -> str:
    if state == "completed" and result is None:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if state == "empty" and result is not None:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    payload: dict[str, object] = {"state": state, "result": result}
    return _encode_json(payload, _MAX_RESULT_BYTES)


def _decode_terminal(
    value: object,
) -> tuple[Literal["completed", "empty"], dict[str, object] | None]:
    payload = _decode_json(value, _MAX_RESULT_BYTES)
    if set(payload) != {"state", "result"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    state = payload.get("state")
    result = payload.get("result")
    if state == "empty" and result is None:
        return "empty", None
    if state == "completed" and isinstance(result, dict):
        return "completed", result
    raise CoordinationError("COORDINATION_STATE_CORRUPT")


def _encode_json(value: dict[str, object], maximum_bytes: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, UnicodeError):
        raise CoordinationError("COORDINATION_STATE_CORRUPT") from None
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return encoded


def _decode_json(value: object, maximum_bytes: int) -> dict[str, object]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum_bytes:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, UnicodeError):
        raise CoordinationError("COORDINATION_STATE_CORRUPT") from None
    if not isinstance(decoded, dict):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return decoded


def _validate_entry(entry: dict[str, object]) -> None:
    if not isinstance(entry, dict):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if set(entry) != {"timestamp", "action", "record_id", "before", "after"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if entry.get("action") not in {"approve", "create", "update", "delete"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    for key in ("timestamp", "record_id"):
        value = entry.get(key)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
    for key in ("before", "after"):
        if entry.get(key) is not None and not isinstance(entry.get(key), dict):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
