"""Fail-closed Upstash implementations for coordination and abuse controls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from ipaddress import ip_address
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from coordination.protocols import (
    CommitCoordinatorStatus,
    CommitReservation,
    ConfirmationResult,
    ConfirmedCommitReceipt,
    CoordinationAdminResult,
    CoordinationError,
    CoordinationEvidence,
    CoordinationInspection,
    GraphLease,
    IdentityRateLimitSubject,
    IpRateLimitSubject,
    Lease,
    LeaseReleaseResult,
    RateLimitPolicyId,
    RateLimitResult,
    ReconciledHeadReceipt,
    RevocationResult,
)
from coordination.serialization import (
    RedisKeyBuilder,
    coordination_state_sha256,
    coordination_admin_request,
    deserialize_commit_reservation,
    deserialize_confirmed_commit_receipt,
    deserialize_admin_result_receipt,
    deserialize_lease_acquisition_receipt,
    deserialize_lease_operation_receipt,
    deserialize_rate_receipt,
    deserialize_revocation_receipt,
    deserialize_reconciled_head_receipt,
    inspect_graph_lock,
    lease_acquire_request,
    lease_operation_request,
    parse_canonical_decimal,
    rate_request,
    revocation_request,
    serialize_coordination_evidence,
    serialize_commit_reservation,
    serialize_generic_lock,
    serialize_graph_lock,
    serialize_reconciled_head_receipt,
    serialize_revocation_entry,
)
from repositories import (
    CommitPermit,
    GraphCommit,
    StagedWriteReceipt,
    canonical_graph_commit_json,
    graph_commit_sha256,
)


class EvalAdapter(Protocol):
    def eval(
        self,
        script: str,
        keys: Sequence[str],
        args: Sequence[str],
        *,
        nonce_idempotent: bool,
    ) -> list[Any]: ...


_LUA_DECIMAL_VALIDATION = r"""
local I64_MAX = '9223372036854775807'
local function canonical_nonnegative(value)
  if type(value) ~= 'string' or value == '' then return false end
  if value == '0' then return true end
  if string.sub(value, 1, 1) == '0' or string.find(value, '[^0-9]') then return false end
  if #value > #I64_MAX or (#value == #I64_MAX and value > I64_MAX) then return false end
  return true
end
local function canonical_positive(value)
  return canonical_nonnegative(value) and value ~= '0'
end
local function decode_object(raw)
  local ok, value = pcall(cjson.decode, raw)
  if not ok or type(value) ~= 'table' then return nil end
  return value
end
local function json_string_ascii(value)
  local parts = {'"'}
  local index = 1
  while index <= #value do
    local first = string.byte(value, index)
    local codepoint = first
    local width = 1
    if first >= 0xC2 and first <= 0xDF then
      local second = string.byte(value, index + 1)
      if not second or second < 0x80 or second > 0xBF then error('invalid UTF-8') end
      codepoint = ((first - 0xC0) * 0x40) + (second - 0x80)
      width = 2
    elseif first >= 0xE0 and first <= 0xEF then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      if not second or not third or second < 0x80 or second > 0xBF
          or third < 0x80 or third > 0xBF then error('invalid UTF-8') end
      codepoint = ((first - 0xE0) * 0x1000)
        + ((second - 0x80) * 0x40) + (third - 0x80)
      if codepoint < 0x800 or (codepoint >= 0xD800 and codepoint <= 0xDFFF) then
        error('invalid UTF-8')
      end
      width = 3
    elseif first >= 0xF0 and first <= 0xF4 then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      local fourth = string.byte(value, index + 3)
      if not second or not third or not fourth or second < 0x80 or second > 0xBF
          or third < 0x80 or third > 0xBF or fourth < 0x80 or fourth > 0xBF then
        error('invalid UTF-8')
      end
      codepoint = ((first - 0xF0) * 0x40000)
        + ((second - 0x80) * 0x1000)
        + ((third - 0x80) * 0x40) + (fourth - 0x80)
      if codepoint < 0x10000 or codepoint > 0x10FFFF then error('invalid UTF-8') end
      width = 4
    elseif first >= 0x80 then
      error('invalid UTF-8')
    end
    if codepoint == 0x22 then table.insert(parts, '\\"')
    elseif codepoint == 0x5C then table.insert(parts, '\\\\')
    elseif codepoint == 0x08 then table.insert(parts, '\\b')
    elseif codepoint == 0x0C then table.insert(parts, '\\f')
    elseif codepoint == 0x0A then table.insert(parts, '\\n')
    elseif codepoint == 0x0D then table.insert(parts, '\\r')
    elseif codepoint == 0x09 then table.insert(parts, '\\t')
    elseif codepoint < 0x20 or codepoint >= 0x80 then
      if codepoint <= 0xFFFF then
        table.insert(parts, string.format('\\u%04x', codepoint))
      else
        local adjusted = codepoint - 0x10000
        local high = 0xD800 + math.floor(adjusted / 0x400)
        local low = 0xDC00 + (adjusted % 0x400)
        table.insert(parts, string.format('\\u%04x\\u%04x', high, low))
      end
    else
      table.insert(parts, string.char(codepoint))
    end
    index = index + width
  end
  table.insert(parts, '"')
  return table.concat(parts)
end
local function canonical_json(value)
  if value == cjson.null then return 'null' end
  local kind = type(value)
  if kind == 'string' then return json_string_ascii(value) end
  if kind == 'number' then
    if value ~= math.floor(value) then error('non-integral JSON number') end
    return string.format('%.0f', value)
  end
  if kind == 'boolean' then return value and 'true' or 'false' end
  if kind == 'nil' then return 'null' end
  if kind ~= 'table' then error('unsupported JSON value') end
  local keys = {}
  for key, _ in pairs(value) do
    if type(key) ~= 'string' then error('JSON object key must be text') end
    table.insert(keys, key)
  end
  table.sort(keys)
  local parts = {}
  for _, key in ipairs(keys) do
    table.insert(parts, json_string_ascii(key) .. ':' .. canonical_json(value[key]))
  end
  return '{' .. table.concat(parts, ',') .. '}'
end
local function strict_object(raw, schema, fields)
  local value = decode_object(raw)
  if not value or value.schema ~= schema or value.version ~= 1 then return nil end
  for key, _ in pairs(value) do
    if not fields[key] then return nil end
  end
  for key, _ in pairs(fields) do
    if value[key] == nil then return nil end
  end
  local ok, canonical = pcall(canonical_json, value)
  if not ok or canonical ~= raw then return nil end
  return value
end
"""


GENERIC_ACQUIRE_LUA = (
    "-- shajra:generic-acquire:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 5 or not canonical_positive(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-acquisition-result', {
    schema=true,version=true,input_sha256=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,requested_ttl_ms=true,lease=true,
    receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or receipt.domain ~= 'GENERIC'
      or not canonical_positive(receipt.requested_ttl_ms)
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 == ARGV[2] then
    return {'OK', 'LEASE_REPLAYED', retained}
  end
  return {'ERR', 'NONCE_REUSE_CONFLICT'}
end
local requested_ttl = tonumber(ARGV[5])
if not requested_ttl or requested_ttl > 300000 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-acquire-request', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,requested_ttl_ms=true})
if not request or request.domain ~= 'GENERIC'
    or request.requested_ttl_ms ~= ARGV[5] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local current = redis.call('GET', KEYS[1])
if current then
  local lock = strict_object(current, 'shajra.generic-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,expires_at_ms=true,ttl_ms=true,
    renew_deadline_ms=true})
  local pttl = redis.call('PTTL', KEYS[1])
  if not lock or lock.domain ~= 'GENERIC' or lock.scope_hmac ~= request.scope_hmac
      or type(lock.acquisition_id_hmac) ~= 'string'
      or not canonical_positive(lock.expires_at_ms)
      or not canonical_positive(lock.ttl_ms)
      or not canonical_nonnegative(lock.renew_deadline_ms)
      or pttl <= 0 or pttl > tonumber(lock.ttl_ms)
      or tonumber(lock.expires_at_ms) - tonumber(lock.renew_deadline_ms) ~= 5000 then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'ERR', 'LOCK_UNAVAILABLE'}
end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local provisional_expires = server_ms + requested_ttl
local provisional_deadline = provisional_expires - 5000
local provisional_lock = canonical_json({schema='shajra.generic-lock',version=1,domain='GENERIC',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  expires_at_ms=tostring(provisional_expires),ttl_ms=ARGV[5],
  renew_deadline_ms=tostring(provisional_deadline)})
redis.call('SET', KEYS[1], provisional_lock, 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[1])
local pttl_text = tostring(pttl)
local expires = server_ms + pttl
local deadline = expires - 5000
local lock = canonical_json({schema='shajra.generic-lock',version=1,domain='GENERIC',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  expires_at_ms=tostring(expires),ttl_ms=pttl_text,renew_deadline_ms=tostring(deadline)})
redis.call('SET', KEYS[1], lock, 'KEEPTTL')
local lease = {schema='shajra.generic-lease',version=1,scope=ARGV[3],
  acquisition_id=ARGV[4],expires_at_ms=tostring(expires),ttl_ms=pttl_text,
  renew_deadline_ms=tostring(deadline)}
local receipt_expires = server_ms + 60000
local result = canonical_json({schema='shajra.lease-acquisition-result',version=1,
  input_sha256=ARGV[2],domain='GENERIC',scope_hmac=request.scope_hmac,
  acquisition_id_hmac=request.acquisition_id_hmac,requested_ttl_ms=ARGV[5],
  lease=lease,receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], tostring(receipt_expires))
return {'OK', 'LEASE_ACQUIRED', result}
"""
)


GRAPH_ACQUIRE_LUA = (
    "-- shajra:graph-acquire:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 5 or #ARGV ~= 5 or not canonical_positive(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[5])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-acquisition-result', {
    schema=true,version=true,input_sha256=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,requested_ttl_ms=true,committed_revision=true,
    lease=true,receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or receipt.domain ~= 'GRAPH_COMMIT'
      or not canonical_positive(receipt.requested_ttl_ms)
      or not canonical_nonnegative(receipt.committed_revision)
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 == ARGV[2] then
    return {'OK', 'LEASE_REPLAYED', retained}
  end
  return {'ERR', 'NONCE_REUSE_CONFLICT'}
end
local requested_ttl = tonumber(ARGV[5])
if not requested_ttl or requested_ttl > 300000 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local confirmed = redis.call('GET', KEYS[1])
local fence = redis.call('GET', KEYS[2])
if not confirmed or not fence then return {'ERR', 'COORDINATION_UNINITIALIZED'} end
if not canonical_nonnegative(confirmed) or not canonical_nonnegative(fence) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-acquire-request', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,requested_ttl_ms=true,committed_revision=true})
if not request or request.domain ~= 'GRAPH_COMMIT'
    or request.requested_ttl_ms ~= ARGV[5]
    or not canonical_nonnegative(request.committed_revision) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if confirmed ~= request.committed_revision then
  return {'ERR', 'COORDINATION_REVISION_MISMATCH'}
end
local reservation = redis.call('GET', KEYS[4])
if reservation then
  local decoded_reservation = strict_object(reservation, 'shajra.commit-reservation', {
    schema=true,version=true,state=true,scope_hmac=true,permit=true,
    commit_json=true,commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true,authorization_request_nonce_hmac=true})
  if not decoded_reservation then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  return {'ERR', 'COMMIT_RECOVERY_REQUIRED'}
end
local current = redis.call('GET', KEYS[3])
if current then
  local lock = strict_object(current, 'shajra.graph-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,fencing_token=true,base_revision=true,
    expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
  local pttl = redis.call('PTTL', KEYS[3])
  if not lock or lock.domain ~= 'GRAPH_COMMIT' or lock.scope_hmac ~= request.scope_hmac
      or type(lock.acquisition_id_hmac) ~= 'string'
      or not canonical_positive(lock.fencing_token)
      or not canonical_nonnegative(lock.base_revision)
      or not canonical_positive(lock.expires_at_ms)
      or not canonical_positive(lock.ttl_ms)
      or not canonical_nonnegative(lock.renew_deadline_ms)
      or lock.fencing_token ~= fence or lock.base_revision ~= confirmed
      or pttl <= 0 or pttl > tonumber(lock.ttl_ms)
      or tonumber(lock.expires_at_ms) - tonumber(lock.renew_deadline_ms) ~= 5000 then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'ERR', 'LOCK_UNAVAILABLE'}
end
if fence == I64_MAX then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
redis.call('INCR', KEYS[2])
local next_fence = redis.call('GET', KEYS[2])
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local provisional_expires = server_ms + requested_ttl
local provisional_deadline = provisional_expires - 5000
local provisional_lock = canonical_json({schema='shajra.graph-lock',version=1,domain='GRAPH_COMMIT',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  fencing_token=next_fence,base_revision=request.committed_revision,
  expires_at_ms=tostring(provisional_expires),ttl_ms=ARGV[5],
  renew_deadline_ms=tostring(provisional_deadline)})
redis.call('SET', KEYS[3], provisional_lock, 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[3])
local pttl_text = tostring(pttl)
local expires = server_ms + pttl
local deadline = expires - 5000
local lock = canonical_json({schema='shajra.graph-lock',version=1,domain='GRAPH_COMMIT',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  fencing_token=next_fence,base_revision=request.committed_revision,
  expires_at_ms=tostring(expires),ttl_ms=pttl_text,renew_deadline_ms=tostring(deadline)})
redis.call('SET', KEYS[3], lock, 'KEEPTTL')
local lease = {schema='shajra.graph-lease',version=1,scope=ARGV[3],
  acquisition_id=ARGV[4],fencing_token=next_fence,
  base_revision=request.committed_revision,expires_at_ms=tostring(expires),
  ttl_ms=pttl_text,renew_deadline_ms=tostring(deadline)}
local receipt_expires = server_ms + 60000
local result = canonical_json({schema='shajra.lease-acquisition-result',version=1,
  input_sha256=ARGV[2],domain='GRAPH_COMMIT',scope_hmac=request.scope_hmac,
  acquisition_id_hmac=request.acquisition_id_hmac,requested_ttl_ms=ARGV[5],
  committed_revision=request.committed_revision,lease=lease,
  receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('SET', KEYS[5], result)
redis.call('PEXPIREAT', KEYS[5], tostring(receipt_expires))
return {'OK', 'LEASE_ACQUIRED', result}
"""
)


LEASE_RENEW_LUA = (
    "-- shajra:lease-renew:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 6 or not canonical_positive(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-operation-result', {
    schema=true,version=true,input_sha256=true,method=true,domain=true,
    scope_hmac=true,acquisition_id_hmac=true,request_nonce_hmac=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or receipt.method ~= 'renew'
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 == ARGV[2] then
    return {'OK', 'LEASE_RENEW_REPLAYED', retained}
  end
  return {'ERR', 'NONCE_REUSE_CONFLICT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-operation-request', {
  schema=true,version=true,method=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,request_nonce_hmac=true,lock_sha256=true,
  requested_ttl_ms=true})
if not request or request.method ~= 'renew'
    or (request.domain ~= 'GENERIC' and request.domain ~= 'GRAPH_COMMIT')
    or request.requested_ttl_ms ~= ARGV[5] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local current = redis.call('GET', KEYS[1])
local current_pttl = redis.call('PTTL', KEYS[1])
if not current or current_pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
local lock_value = nil
if request.domain == 'GENERIC' then
  lock_value = strict_object(current, 'shajra.generic-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,expires_at_ms=true,ttl_ms=true,
    renew_deadline_ms=true})
else
  lock_value = strict_object(current, 'shajra.graph-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,fencing_token=true,base_revision=true,
    expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
end
if not lock_value then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if current ~= ARGV[6] then return {'ERR', 'LEASE_LOST'} end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local requested_ttl = tonumber(ARGV[5])
if not requested_ttl or requested_ttl > 300000 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local provisional_expires = server_ms + requested_ttl
lock_value.expires_at_ms = tostring(provisional_expires)
lock_value.ttl_ms = ARGV[5]
lock_value.renew_deadline_ms = tostring(provisional_expires - 5000)
redis.call('SET', KEYS[1], canonical_json(lock_value), 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[1])
local pttl_text = tostring(pttl)
local expires = server_ms + pttl
local deadline = expires - 5000
lock_value.expires_at_ms = tostring(expires)
lock_value.ttl_ms = pttl_text
lock_value.renew_deadline_ms = tostring(deadline)
local next_lock = canonical_json(lock_value)
redis.call('SET', KEYS[1], next_lock, 'KEEPTTL')
local lease = {schema=(request.domain == 'GENERIC' and 'shajra.generic-lease' or 'shajra.graph-lease'),
  version=1,scope=ARGV[3],acquisition_id=ARGV[4],expires_at_ms=tostring(expires),
  ttl_ms=pttl_text,renew_deadline_ms=tostring(deadline)}
if request.domain == 'GRAPH_COMMIT' then
  lease.fencing_token=lock_value.fencing_token
  lease.base_revision=lock_value.base_revision
end
local receipt_expires = server_ms + 60000
local result = canonical_json({schema='shajra.lease-operation-result',version=1,
  input_sha256=ARGV[2],method='renew',domain=request.domain,
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  request_nonce_hmac=request.request_nonce_hmac,result=lease,
  receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], tostring(receipt_expires))
return {'OK', 'LEASE_RENEWED', result}
"""
)


LEASE_RELEASE_LUA = (
    "-- shajra:lease-release:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 5 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-operation-result', {
    schema=true,version=true,input_sha256=true,method=true,domain=true,
    scope_hmac=true,acquisition_id_hmac=true,request_nonce_hmac=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or receipt.method ~= 'release'
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 == ARGV[2] then
    return {'OK', 'LEASE_RELEASE_REPLAYED', retained}
  end
  return {'ERR', 'NONCE_REUSE_CONFLICT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-operation-request', {
  schema=true,version=true,method=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,request_nonce_hmac=true,lock_sha256=true})
if not request or request.method ~= 'release'
    or (request.domain ~= 'GENERIC' and request.domain ~= 'GRAPH_COMMIT') then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local current = redis.call('GET', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if not current or pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
local lock_value = nil
if request.domain == 'GENERIC' then
  lock_value = strict_object(current, 'shajra.generic-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,expires_at_ms=true,ttl_ms=true,
    renew_deadline_ms=true})
else
  lock_value = strict_object(current, 'shajra.graph-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,fencing_token=true,base_revision=true,
    expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
end
if not lock_value then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if current ~= ARGV[5] then return {'ERR', 'LEASE_LOST'} end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local receipt_expires = server_ms + 60000
local release = {schema='shajra.lease-release-result',version=1,
  code='LEASE_RELEASED',acquisition_id=ARGV[4],released_at_ms=tostring(server_ms)}
local result = canonical_json({schema='shajra.lease-operation-result',version=1,
  input_sha256=ARGV[2],method='release',domain=request.domain,
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  request_nonce_hmac=request.request_nonce_hmac,result=release,
  receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('DEL', KEYS[1])
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], tostring(receipt_expires))
return {'OK', 'LEASE_RELEASED', result}
"""
)


LEASE_ASSERT_LUA = r"""-- shajra:lease-assert:v1
if #KEYS ~= 1 or #ARGV ~= 3 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local current = redis.call('GET', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if not current or current ~= ARGV[3] or pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
return {'OK', 'LEASE_OWNED'}
"""


AUTHORIZE_COMMIT_LUA = (
    "-- shajra:commit-authorize:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 3 or #ARGV ~= 3 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local retained = redis.call('GET', KEYS[3])
if retained then
  local reservation = strict_object(retained, 'shajra.commit-reservation', {
    schema=true,version=true,state=true,scope_hmac=true,permit=true,
    commit_json=true,commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true,authorization_request_nonce_hmac=true})
  if not reservation then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if retained == ARGV[2] then return {'OK', 'RESERVATION_REPLAYED', retained} end
  return {'ERR', 'RESERVATION_CONFLICT'}
end
local confirmed = redis.call('GET', KEYS[1])
if not confirmed then return {'ERR', 'COORDINATION_UNINITIALIZED'} end
if not canonical_nonnegative(confirmed) then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local lock = redis.call('GET', KEYS[2])
local pttl = redis.call('PTTL', KEYS[2])
if not lock or pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
local lock_value = strict_object(lock, 'shajra.graph-lock', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,fencing_token=true,base_revision=true,
  expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
if not lock_value then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if lock ~= ARGV[3] then return {'ERR', 'LEASE_LOST'} end
local proposed = strict_object(ARGV[2], 'shajra.commit-reservation', {
  schema=true,version=true,state=true,scope_hmac=true,permit=true,
  commit_json=true,commit_sha256=true,staged_write_receipt_json=true,
  staged_write_receipt_sha256=true,authorization_request_nonce_hmac=true})
if not proposed or proposed.state ~= 'COMMITTING'
    or lock_value.base_revision ~= confirmed
    or proposed.scope_hmac ~= lock_value.scope_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
redis.call('SET', KEYS[3], ARGV[2])
return {'OK', 'RESERVATION_CREATED', ARGV[2]}
"""
)


COORDINATION_STATUS_LUA = r"""-- shajra:coordination-status:v1
if #KEYS ~= 5 or #ARGV ~= 1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local values = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
local pttl = -2
if values[1] then pttl = redis.call('PTTL', KEYS[1]) end
return {'OK', 'STATUS', values[3] or '', values[2] or '', values[1] or '',
  tostring(pttl), values[4] or '', values[5] or ''}
"""


CONFIRM_COMMIT_LUA = (
    "-- shajra:commit-confirm:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 3 or #ARGV ~= 9 or not canonical_positive(ARGV[3])
    or not canonical_positive(ARGV[4]) or not canonical_nonnegative(ARGV[9]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local confirmed = redis.call('GET', KEYS[1])
if not confirmed or not canonical_nonnegative(confirmed) then
  return {'ERR', 'COORDINATION_UNINITIALIZED'}
end
local proof_raw = redis.call('GET', KEYS[3])
if proof_raw then
  local decoded = decode_object(proof_raw)
  if not decoded or type(decoded.schema) ~= 'string' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local proof = nil
  if decoded.schema == 'shajra.confirmed-commit-receipt' then
    proof = strict_object(proof_raw, 'shajra.confirmed-commit-receipt', {
      schema=true,version=true,scope_hmac=true,permit=true,commit_json=true,
      commit_sha256=true,staged_write_receipt_json=true,
      staged_write_receipt_sha256=true})
  elseif decoded.schema == 'shajra.reconciled-head-receipt' then
    proof = strict_object(proof_raw, 'shajra.reconciled-head-receipt', {
      schema=true,version=true,scope_hmac=true,revision=true,
      semantic_checksum=true,head_commit_sha256=true,evidence_sha256=true,
      admin_request_nonce_hmac=true,proof_sha256=true})
  end
  if not proof then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  if proof.schema == 'shajra.confirmed-commit-receipt' and proof.permit
      and proof.permit.operation_id == ARGV[2]
      and proof.permit.revision == ARGV[3]
      and proof.permit.fencing_token == ARGV[4]
      and proof.permit.permit_id == ARGV[5]
      and proof.commit_sha256 == ARGV[6] then
    return {'OK', 'CONFIRMATION_REPLAYED', proof_raw}
  end
end
local function decimal_lte(left, right)
  if #left ~= #right then return #left < #right end
  return left <= right
end
if decimal_lte(ARGV[3], confirmed) then
  return {'ERR', 'CONFIRMATION_PROOF_EVICTED', confirmed}
end
local reservation_raw = redis.call('GET', KEYS[2])
if not reservation_raw then return {'ERR', 'CONFIRMATION_CONFLICT'} end
local reservation = strict_object(reservation_raw, 'shajra.commit-reservation', {
  schema=true,version=true,state=true,scope_hmac=true,permit=true,
  commit_json=true,commit_sha256=true,staged_write_receipt_json=true,
  staged_write_receipt_sha256=true,authorization_request_nonce_hmac=true})
if not reservation or not reservation.permit
    or reservation.permit.operation_id ~= ARGV[2]
    or reservation.permit.revision ~= ARGV[3]
    or reservation.permit.fencing_token ~= ARGV[4]
    or reservation.permit.permit_id ~= ARGV[5]
    or reservation.commit_sha256 ~= ARGV[6]
    or confirmed ~= ARGV[9] then
  return {'ERR', 'CONFIRMATION_CONFLICT'}
end
local proof = canonical_json({schema='shajra.confirmed-commit-receipt',version=1,
  scope_hmac=reservation.scope_hmac,permit=reservation.permit,
  commit_json=reservation.commit_json,commit_sha256=reservation.commit_sha256,
  staged_write_receipt_json=reservation.staged_write_receipt_json,
  staged_write_receipt_sha256=reservation.staged_write_receipt_sha256})
redis.call('SET', KEYS[1], ARGV[3])
redis.call('SET', KEYS[3], proof)
redis.call('DEL', KEYS[2])
return {'OK', 'CONFIRMED', proof}
"""
)


COORDINATION_INSPECT_LUA = r"""-- shajra:coordination-inspect:v1
if #KEYS ~= 5 or #ARGV ~= 1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local values = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
return {'OK', 'INSPECTION', values[1] or '', values[2] or '', values[3] or '',
  values[4] or '', values[5] or ''}
"""


COORDINATION_ADMIN_LUA = (
    "-- shajra:coordination-admin:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
local MISSING = '__SHAJRA_MISSING_V1__'
if #KEYS ~= 6 or #ARGV ~= 17 or not canonical_nonnegative(ARGV[9])
    or not canonical_positive(ARGV[10]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[6])
if retained then
  local receipt = strict_object(retained, 'shajra.coordination-admin-result', {
    schema=true,version=true,input_sha256=true,method=true,scope_hmac=true,
    request_nonce_hmac=true,evidence_sha256=true,expected_state_sha256=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or not receipt.result
      or type(receipt.input_sha256) ~= 'string'
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 == ARGV[2] then
    return {'OK', receipt.result.code, retained}
  end
  return {'ERR', 'NONCE_REUSE_CONFLICT'}
end
if ARGV[17] ~= ARGV[4] then return {'ERR', 'ADMIN_STATE_CHANGED'} end
local current = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
for index = 1, 5 do
  local expected = ARGV[11 + index]
  if expected == MISSING then
    if current[index] then return {'ERR', 'ADMIN_STATE_CHANGED'} end
  elseif not current[index] or current[index] ~= expected then
    return {'ERR', 'ADMIN_STATE_CHANGED'}
  end
end
if current[1] or current[4] then return {'ERR', 'ADMIN_BUSY'} end
if ARGV[11] == 'initialize' then
  for index = 1, 5 do
    if current[index] then return {'ERR', 'ADMIN_STATE_CHANGED'} end
  end
elseif ARGV[11] == 'reconcile' then
  local present = false
  for index = 1, 5 do
    if current[index] then present = true end
  end
  if not present then return {'ERR', 'ADMIN_STATE_CHANGED'} end
else
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if current[2] and canonical_nonnegative(current[2]) then
  if #ARGV[10] < #current[2] or (#ARGV[10] == #current[2] and ARGV[10] < current[2]) then
    return {'ERR', 'ADMIN_EVIDENCE_INVALID'}
  end
end
if current[3] and canonical_nonnegative(current[3]) then
  if #ARGV[9] < #current[3] or (#ARGV[9] == #current[3] and ARGV[9] < current[3]) then
    return {'ERR', 'ADMIN_EVIDENCE_INVALID'}
  end
end
local request = strict_object(ARGV[1], 'shajra.coordination-admin-request', {
  schema=true,version=true,method=true,scope_hmac=true,evidence_sha256=true,
  expected_state_sha256=true})
local evidence = strict_object(ARGV[3], 'shajra.coordination-evidence', {
  schema=true,version=true,scope=true,committed_head_revision=true,
  committed_head_semantic_checksum=true,committed_head_commit_sha256=true,
  max_durable_fencing_token=true,fencing_floor=true,evidence_sha256=true})
local proof = strict_object(ARGV[7], 'shajra.reconciled-head-receipt', {
  schema=true,version=true,scope_hmac=true,revision=true,semantic_checksum=true,
  head_commit_sha256=true,evidence_sha256=true,admin_request_nonce_hmac=true,
  proof_sha256=true})
if not request or not evidence or not proof
    or request.schema ~= 'shajra.coordination-admin-request'
    or request.version ~= 1 or request.method ~= ARGV[11]
    or request.expected_state_sha256 ~= ARGV[4]
    or evidence.evidence_sha256 ~= request.evidence_sha256
    or proof.evidence_sha256 ~= evidence.evidence_sha256
    or proof.revision ~= ARGV[9] then
  return {'ERR', 'ADMIN_EVIDENCE_INVALID'}
end
local code = (ARGV[11] == 'initialize' and 'ADMIN_INITIALIZED' or 'ADMIN_RECONCILED')
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local receipt_expires = server_ms + 60000
local transition = {schema='shajra.coordination-admin-transition',version=1,
  code=code,previous_state_sha256=ARGV[4],state_sha256=ARGV[8],
  confirmed_revision=ARGV[9],fencing_floor=ARGV[10]}
local result = canonical_json({schema='shajra.coordination-admin-result',version=1,
  input_sha256=ARGV[2],method=ARGV[11],scope_hmac=request.scope_hmac,
  request_nonce_hmac=proof.admin_request_nonce_hmac,
  evidence_sha256=evidence.evidence_sha256,expected_state_sha256=ARGV[4],
  result=transition,receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('SET', KEYS[2], ARGV[10])
redis.call('SET', KEYS[3], ARGV[9])
redis.call('SET', KEYS[5], ARGV[7])
redis.call('SET', KEYS[6], result)
redis.call('PEXPIREAT', KEYS[6], tostring(receipt_expires))
return {'OK', code, result}
"""
)


REVOCATION_REVOKE_LUA = (
    "-- shajra:revocation-revoke:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 6 or not canonical_nonnegative(ARGV[4])
    or not canonical_nonnegative(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.revocation-result', {
    schema=true,version=true,input_sha256=true,jti_hmac=true,
    token_expires_at_s=true,leeway_s=true,code=true,revoked=true,
    server_time_ms=true,expires_at_ms=true,receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or not canonical_nonnegative(receipt.token_expires_at_s)
      or not canonical_nonnegative(receipt.leeway_s)
      or not canonical_nonnegative(receipt.server_time_ms)
      or not canonical_nonnegative(receipt.expires_at_ms)
      or not canonical_nonnegative(receipt.receipt_expires_at_ms)
      or type(receipt.revoked) ~= 'boolean' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 ~= ARGV[2] then return {'ERR', 'NONCE_REUSE_CONFLICT'} end
  local request = strict_object(ARGV[1], 'shajra.revocation-request', {
    schema=true,version=true,jti_hmac=true,token_expires_at_s=true,leeway_s=true})
  local revoked_code = receipt.code == 'REVOKED' or receipt.code == 'ALREADY_REVOKED'
  if not request or receipt.jti_hmac ~= request.jti_hmac
      or receipt.token_expires_at_s ~= request.token_expires_at_s
      or receipt.leeway_s ~= request.leeway_s
      or receipt.revoked ~= revoked_code
      or (not revoked_code and receipt.code ~= 'TOKEN_ALREADY_EXPIRED') then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local expected_expires = (tonumber(receipt.token_expires_at_s) * 1000)
    + (tonumber(receipt.leeway_s) * 1000)
  local expected_receipt_expires = math.max(
    expected_expires, tonumber(receipt.server_time_ms) + 60000)
  if receipt.expires_at_ms ~= tostring(expected_expires)
      or receipt.receipt_expires_at_ms ~= tostring(expected_receipt_expires) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local ttl = redis.call('PTTL', KEYS[2])
  local clock = redis.call('TIME')
  local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
  local remaining = tonumber(receipt.receipt_expires_at_ms) - now
  if ttl == -1 or (ttl >= 0 and ttl < remaining) then
    redis.call('PEXPIREAT', KEYS[2], receipt.receipt_expires_at_ms)
  elseif ttl > remaining then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'OK', receipt.code, retained}
end
local request = strict_object(ARGV[1], 'shajra.revocation-request', {
  schema=true,version=true,jti_hmac=true,token_expires_at_s=true,leeway_s=true})
local entry_proposed = strict_object(ARGV[6], 'shajra.revocation-entry', {
  schema=true,version=true,jti_hmac=true,expires_at_ms=true,entry_sha256=true})
if not request or not entry_proposed or request.token_expires_at_s ~= ARGV[4]
    or request.leeway_s ~= ARGV[5] or entry_proposed.jti_hmac ~= request.jti_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local expires = (tonumber(ARGV[4]) * 1000) + (tonumber(ARGV[5]) * 1000)
if not expires or expires > 9223372036854775807 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local existing = redis.call('GET', KEYS[1])
local code = 'REVOKED'
local revoked = true
if server_ms >= expires then
  code = 'TOKEN_ALREADY_EXPIRED'
  revoked = false
elseif existing then
  if existing ~= ARGV[6] then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local ttl = redis.call('PTTL', KEYS[1])
  local remaining = expires - server_ms
  if ttl == -1 or (ttl >= 0 and ttl < remaining) then
    redis.call('PEXPIREAT', KEYS[1], tostring(expires))
  elseif ttl > remaining then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  code = 'ALREADY_REVOKED'
end
local receipt_expires = math.max(expires, server_ms + 60000)
local result = canonical_json({schema='shajra.revocation-result',version=1,
  input_sha256=ARGV[2],jti_hmac=request.jti_hmac,token_expires_at_s=ARGV[4],
  leeway_s=ARGV[5],code=code,revoked=revoked,server_time_ms=tostring(server_ms),
  expires_at_ms=tostring(expires),receipt_expires_at_ms=tostring(receipt_expires)})
if code == 'REVOKED' then
  redis.call('SET', KEYS[1], ARGV[6])
  redis.call('PEXPIREAT', KEYS[1], tostring(expires))
end
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], tostring(receipt_expires))
return {'OK', code, result}
"""
)


REVOCATION_CHECK_LUA = (
    "-- shajra:revocation-check:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 1 or #ARGV ~= 4 or not canonical_nonnegative(ARGV[2])
    or not canonical_nonnegative(ARGV[3]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local expires = (tonumber(ARGV[2]) * 1000) + (tonumber(ARGV[3]) * 1000)
if server_ms >= expires then
  return {'OK', 'TOKEN_ALREADY_EXPIRED', 'false', tostring(server_ms), tostring(expires)}
end
local raw = redis.call('GET', KEYS[1])
if not raw then return {'OK', 'NOT_REVOKED', 'false', tostring(server_ms), tostring(expires)} end
if raw ~= ARGV[1] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local ttl = redis.call('PTTL', KEYS[1])
local remaining = expires - server_ms
if ttl == -1 or (ttl >= 0 and ttl < remaining) then
  redis.call('PEXPIREAT', KEYS[1], tostring(expires))
elseif ttl > remaining then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
return {'OK', 'REVOKED', 'true', tostring(server_ms), tostring(expires)}
"""
)


RATE_TIME_LUA = r"""-- shajra:rate-time:v1
if #KEYS ~= 0 or #ARGV ~= 0 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
return {'OK', 'TIME', tostring(server_ms)}
"""


RATE_CONSUME_LUA = (
    "-- shajra:rate-consume:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 7 or not canonical_nonnegative(ARGV[5])
    or not canonical_positive(ARGV[6]) or not canonical_positive(ARGV[7]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.rate-result', {
    schema=true,version=true,input_sha256=true,policy_id=true,subject_kind=true,
    subject_hmac=true,window_start_ms=true,window_ms=true,limit=true,allowed=true,
    observed_count=true,remaining=true,server_time_ms=true,reset_at_ms=true,
    retry_after_ms=true,receipt_expires_at_ms=true})
  if not receipt or type(receipt.input_sha256) ~= 'string'
      or type(receipt.allowed) ~= 'boolean'
      or not canonical_nonnegative(receipt.window_start_ms)
      or not canonical_positive(receipt.window_ms)
      or not canonical_positive(receipt.limit)
      or not canonical_positive(receipt.observed_count)
      or not canonical_nonnegative(receipt.remaining)
      or not canonical_nonnegative(receipt.server_time_ms)
      or not canonical_positive(receipt.reset_at_ms)
      or not canonical_nonnegative(receipt.retry_after_ms)
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 ~= ARGV[2] then return {'ERR', 'NONCE_REUSE_CONFLICT'} end
  local request = strict_object(ARGV[1], 'shajra.rate-request', {
    schema=true,version=true,policy_id=true,subject_kind=true,subject_hmac=true,
    window_start_ms=true,window_ms=true,limit=true})
  if not request or receipt.policy_id ~= request.policy_id
      or receipt.subject_kind ~= request.subject_kind
      or receipt.subject_hmac ~= request.subject_hmac
      or receipt.window_start_ms ~= request.window_start_ms
      or receipt.window_ms ~= request.window_ms or receipt.limit ~= request.limit then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local start = tonumber(receipt.window_start_ms)
  local window = tonumber(receipt.window_ms)
  local limit = tonumber(receipt.limit)
  local observed = tonumber(receipt.observed_count)
  local server = tonumber(receipt.server_time_ms)
  local reset = start + window
  local expected_remaining = math.max(limit - observed, 0)
  local expected_retry = receipt.allowed and 0 or reset - server
  if receipt.reset_at_ms ~= tostring(reset)
      or receipt.receipt_expires_at_ms ~= tostring(reset + 60000)
      or receipt.remaining ~= tostring(expected_remaining)
      or receipt.retry_after_ms ~= tostring(expected_retry)
      or server < start or server >= reset or receipt.allowed ~= (observed <= limit) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local clock = redis.call('TIME')
  local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
  local ttl = redis.call('PTTL', KEYS[2])
  local remaining = tonumber(receipt.receipt_expires_at_ms) - now
  if ttl == -1 or (ttl >= 0 and ttl < remaining) then
    redis.call('PEXPIREAT', KEYS[2], receipt.receipt_expires_at_ms)
  elseif ttl > remaining then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  return {'OK', (receipt.allowed and 'RATE_LIMIT_ALLOWED' or 'RATE_LIMIT_DENIED'), retained}
end
local request = strict_object(ARGV[1], 'shajra.rate-request', {
  schema=true,version=true,policy_id=true,subject_kind=true,subject_hmac=true,
  window_start_ms=true,window_ms=true,limit=true})
if not request or request.policy_id ~= ARGV[3] or request.subject_kind ~= ARGV[4]
    or request.window_start_ms ~= ARGV[5] or request.window_ms ~= ARGV[6]
    or request.limit ~= ARGV[7] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local window = tonumber(ARGV[6])
local actual_start = math.floor(server_ms / window) * window
if tostring(actual_start) ~= ARGV[5] then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local reset = actual_start + window
local raw_count = redis.call('GET', KEYS[1])
local count = '0'
if raw_count then
  if not canonical_nonnegative(raw_count) then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  count = raw_count
end
if count == I64_MAX then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local counter_ttl = redis.call('PTTL', KEYS[1])
if raw_count then
  local remaining = reset - server_ms
  if counter_ttl > remaining then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
end
redis.call('INCR', KEYS[1])
count = redis.call('GET', KEYS[1])
redis.call('PEXPIREAT', KEYS[1], tostring(reset))
local numeric_count = tonumber(count)
local limit = tonumber(ARGV[7])
local allowed = numeric_count <= limit
local remaining = math.max(limit - numeric_count, 0)
local retry_after = (allowed and 0 or reset - server_ms)
local receipt_expires = reset + 60000
local result = canonical_json({schema='shajra.rate-result',version=1,
  input_sha256=ARGV[2],policy_id=ARGV[3],subject_kind=ARGV[4],
  subject_hmac=request.subject_hmac,window_start_ms=ARGV[5],window_ms=ARGV[6],
  limit=ARGV[7],allowed=allowed,observed_count=count,remaining=tostring(remaining),
  server_time_ms=tostring(server_ms),reset_at_ms=tostring(reset),
  retry_after_ms=tostring(retry_after),receipt_expires_at_ms=tostring(receipt_expires)})
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], tostring(receipt_expires))
return {'OK', (allowed and 'RATE_LIMIT_ALLOWED' or 'RATE_LIMIT_DENIED'), result}
"""
)


def new_acquisition_id() -> str:
    return str(uuid4())


_STABLE_ERROR_CODES = frozenset(
    {
        "ADMIN_BUSY",
        "ADMIN_EVIDENCE_INVALID",
        "ADMIN_STATE_CHANGED",
        "COMMIT_RECOVERY_REQUIRED",
        "CONFIRMATION_CONFLICT",
        "CONFIRMATION_PROOF_EVICTED",
        "COORDINATION_REVISION_MISMATCH",
        "COORDINATION_STATE_CORRUPT",
        "COORDINATION_UNAVAILABLE",
        "COORDINATION_UNINITIALIZED",
        "LEASE_LOST",
        "LOCK_UNAVAILABLE",
        "NONCE_REUSE_CONFLICT",
        "RESERVATION_CONFLICT",
    }
)


def _tagged(result: list[Any], allowed: set[str]) -> tuple[str, list[Any]]:
    if (
        not isinstance(result, list)
        or len(result) < 2
        or result[0] not in {"OK", "ERR"}
        or not isinstance(result[1], str)
    ):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if result[0] == "ERR":
        if result[1] not in _STABLE_ERROR_CODES:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        raise CoordinationError(result[1])
    if result[1] not in allowed:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return result[1], result[2:]


def _one_text_payload(payload: list[Any]) -> str:
    if len(payload) != 1 or not isinstance(payload[0], str):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return payload[0]


class UpstashLeaseManager:
    def __init__(self, redis: EvalAdapter, keys: RedisKeyBuilder) -> None:
        self._redis = redis
        self._keys = keys

    def acquire(self, scope: str, acquisition_id: str, ttl_ms: int = 15_000) -> Lease:
        request = lease_acquire_request(
            self._keys, "GENERIC", scope, acquisition_id, ttl_ms
        )
        result = self._redis.eval(
            GENERIC_ACQUIRE_LUA,
            [
                self._keys.generic_lock(scope),
                self._keys.generic_acquisition_result(scope, acquisition_id),
            ],
            [request.text, request.sha256, scope, acquisition_id, str(ttl_ms)],
            nonce_idempotent=True,
        )
        _, payload = _tagged(result, {"LEASE_ACQUIRED", "LEASE_REPLAYED"})
        receipt = deserialize_lease_acquisition_receipt(
            _one_text_payload(payload), request, self._keys, scope, acquisition_id
        )
        if type(receipt.lease) is not Lease:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.lease

    def renew(self, lease: Lease, request_nonce: str, ttl_ms: int = 15_000) -> Lease:
        if type(lease) is not Lease:
            raise CoordinationError("LEASE_LOST")
        request = lease_operation_request(
            self._keys, "renew", lease, request_nonce, ttl_ms
        )
        result = self._redis.eval(
            LEASE_RENEW_LUA,
            [
                self._keys.generic_lock(lease.scope),
                self._keys.generic_operation_result(lease.scope, request_nonce),
            ],
            [
                request.text,
                request.sha256,
                lease.scope,
                lease.acquisition_id,
                str(ttl_ms),
                serialize_generic_lock(lease, self._keys),
            ],
            nonce_idempotent=True,
        )
        _, payload = _tagged(result, {"LEASE_RENEWED", "LEASE_RENEW_REPLAYED"})
        receipt = deserialize_lease_operation_receipt(
            _one_text_payload(payload),
            request,
            self._keys,
            lease.scope,
            lease.acquisition_id,
            request_nonce,
        )
        if type(receipt.result) is not Lease:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.result

    def assert_owned(self, lease: Lease) -> None:
        if type(lease) is not Lease:
            raise CoordinationError("LEASE_LOST")
        result = self._redis.eval(
            LEASE_ASSERT_LUA,
            [self._keys.generic_lock(lease.scope)],
            [
                lease.scope,
                lease.acquisition_id,
                serialize_generic_lock(lease, self._keys),
            ],
            nonce_idempotent=False,
        )
        _tagged(result, {"LEASE_OWNED"})

    def release(self, lease: Lease, request_nonce: str) -> LeaseReleaseResult:
        if type(lease) is not Lease:
            raise CoordinationError("LEASE_LOST")
        request = lease_operation_request(self._keys, "release", lease, request_nonce)
        result = self._redis.eval(
            LEASE_RELEASE_LUA,
            [
                self._keys.generic_lock(lease.scope),
                self._keys.generic_operation_result(lease.scope, request_nonce),
            ],
            [
                request.text,
                request.sha256,
                lease.scope,
                lease.acquisition_id,
                serialize_generic_lock(lease, self._keys),
            ],
            nonce_idempotent=True,
        )
        code, payload = _tagged(result, {"LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"})
        receipt = deserialize_lease_operation_receipt(
            _one_text_payload(payload),
            request,
            self._keys,
            lease.scope,
            lease.acquisition_id,
            request_nonce,
        )
        if not isinstance(receipt.result, LeaseReleaseResult):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        if code == "LEASE_RELEASE_REPLAYED":
            return replace(receipt.result, code="LEASE_RELEASE_REPLAYED")
        return receipt.result


class UpstashCommitCoordinator:
    def __init__(self, redis: EvalAdapter, keys: RedisKeyBuilder) -> None:
        self._redis = redis
        self._keys = keys

    def acquire(
        self,
        scope: str,
        committed_revision: int,
        acquisition_id: str,
        ttl_ms: int = 15_000,
    ) -> GraphLease:
        request = lease_acquire_request(
            self._keys,
            "GRAPH_COMMIT",
            scope,
            acquisition_id,
            ttl_ms,
            committed_revision,
        )
        result = self._redis.eval(
            GRAPH_ACQUIRE_LUA,
            [
                self._keys.graph_confirmed_revision(scope),
                self._keys.graph_fence(scope),
                self._keys.graph_lock(scope),
                self._keys.graph_reservation(scope),
                self._keys.graph_acquisition_result(scope, acquisition_id),
            ],
            [request.text, request.sha256, scope, acquisition_id, str(ttl_ms)],
            nonce_idempotent=True,
        )
        _, payload = _tagged(result, {"LEASE_ACQUIRED", "LEASE_REPLAYED"})
        receipt = deserialize_lease_acquisition_receipt(
            _one_text_payload(payload), request, self._keys, scope, acquisition_id
        )
        if type(receipt.lease) is not GraphLease:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.lease

    def renew(
        self, lease: GraphLease, request_nonce: str, ttl_ms: int = 15_000
    ) -> GraphLease:
        if type(lease) is not GraphLease:
            raise CoordinationError("LEASE_LOST")
        request = lease_operation_request(
            self._keys, "renew", lease, request_nonce, ttl_ms
        )
        result = self._redis.eval(
            LEASE_RENEW_LUA,
            [
                self._keys.graph_lock(lease.scope),
                self._keys.graph_operation_result(lease.scope, request_nonce),
            ],
            [
                request.text,
                request.sha256,
                lease.scope,
                lease.acquisition_id,
                str(ttl_ms),
                serialize_graph_lock(lease, self._keys),
            ],
            nonce_idempotent=True,
        )
        _, payload = _tagged(result, {"LEASE_RENEWED", "LEASE_RENEW_REPLAYED"})
        receipt = deserialize_lease_operation_receipt(
            _one_text_payload(payload),
            request,
            self._keys,
            lease.scope,
            lease.acquisition_id,
            request_nonce,
        )
        if type(receipt.result) is not GraphLease:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.result

    def assert_owned(self, lease: GraphLease) -> None:
        if type(lease) is not GraphLease:
            raise CoordinationError("LEASE_LOST")
        result = self._redis.eval(
            LEASE_ASSERT_LUA,
            [self._keys.graph_lock(lease.scope)],
            [
                lease.scope,
                lease.acquisition_id,
                serialize_graph_lock(lease, self._keys),
            ],
            nonce_idempotent=False,
        )
        _tagged(result, {"LEASE_OWNED"})

    def release(self, lease: GraphLease, request_nonce: str) -> LeaseReleaseResult:
        if type(lease) is not GraphLease:
            raise CoordinationError("LEASE_LOST")
        request = lease_operation_request(self._keys, "release", lease, request_nonce)
        result = self._redis.eval(
            LEASE_RELEASE_LUA,
            [
                self._keys.graph_lock(lease.scope),
                self._keys.graph_operation_result(lease.scope, request_nonce),
            ],
            [
                request.text,
                request.sha256,
                lease.scope,
                lease.acquisition_id,
                serialize_graph_lock(lease, self._keys),
            ],
            nonce_idempotent=True,
        )
        code, payload = _tagged(result, {"LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"})
        receipt = deserialize_lease_operation_receipt(
            _one_text_payload(payload),
            request,
            self._keys,
            lease.scope,
            lease.acquisition_id,
            request_nonce,
        )
        if not isinstance(receipt.result, LeaseReleaseResult):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        if code == "LEASE_RELEASE_REPLAYED":
            return replace(receipt.result, code="LEASE_RELEASE_REPLAYED")
        return receipt.result

    def authorize_commit(
        self,
        lease: GraphLease,
        commit: GraphCommit,
        staged_write_receipt: StagedWriteReceipt,
        request_nonce: str,
    ) -> CommitPermit:
        if type(lease) is not GraphLease:
            raise CoordinationError("LEASE_LOST")
        if (
            not isinstance(commit, GraphCommit)
            or not isinstance(staged_write_receipt, StagedWriteReceipt)
            or commit.revision != lease.base_revision + 1
            or commit.fencing_token != lease.fencing_token
            or staged_write_receipt.operation_id != commit.operation_id
            or staged_write_receipt.revision != commit.revision
            or staged_write_receipt.fencing_token != commit.fencing_token
        ):
            raise CoordinationError("LEASE_LOST")
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
            lease.scope,
            "COMMITTING",
            permit,
            commit,
            digest,
            staged_write_receipt,
        )
        reservation_raw = serialize_commit_reservation(
            reservation, self._keys, request_nonce
        )
        result = self._redis.eval(
            AUTHORIZE_COMMIT_LUA,
            [
                self._keys.graph_confirmed_revision(lease.scope),
                self._keys.graph_lock(lease.scope),
                self._keys.graph_reservation(lease.scope),
            ],
            [
                lease.scope,
                reservation_raw,
                serialize_graph_lock(lease, self._keys),
            ],
            nonce_idempotent=True,
        )
        _, payload = _tagged(result, {"RESERVATION_CREATED", "RESERVATION_REPLAYED"})
        persisted = deserialize_commit_reservation(
            _one_text_payload(payload), self._keys, lease.scope
        )
        if persisted != reservation:
            raise CoordinationError("RESERVATION_CONFLICT")
        return persisted.permit

    def get_status(self, scope: str) -> CommitCoordinatorStatus:
        result = self._redis.eval(
            COORDINATION_STATUS_LUA,
            [
                self._keys.graph_lock(scope),
                self._keys.graph_fence(scope),
                self._keys.graph_confirmed_revision(scope),
                self._keys.graph_reservation(scope),
                self._keys.graph_last_confirmation(scope),
            ],
            [scope],
            nonce_idempotent=False,
        )
        _, payload = _tagged(result, {"STATUS"})
        if len(payload) != 6 or not all(isinstance(value, str) for value in payload):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        confirmed_raw, fence_raw, lock_raw, pttl_raw, reservation_raw, proof_raw = (
            payload
        )
        if not confirmed_raw and not fence_raw:
            if lock_raw or reservation_raw or proof_raw or pttl_raw != "-2":
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            raise CoordinationError("COORDINATION_UNINITIALIZED")
        if not confirmed_raw or not fence_raw or not proof_raw:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        confirmed = parse_canonical_decimal(confirmed_raw, minimum=0)
        fence = parse_canonical_decimal(fence_raw, minimum=1)
        if lock_raw:
            pttl = parse_canonical_decimal(pttl_raw, minimum=1)
            lock = inspect_graph_lock(lock_raw, self._keys, scope)
            if (
                pttl > lock.ttl_ms
                or lock.fencing_token != fence
                or lock.base_revision != confirmed
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
        elif pttl_raw != "-2":
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

        proof: ConfirmedCommitReceipt | ReconciledHeadReceipt
        proof_fencing_token: int | None = None
        try:
            proof = deserialize_confirmed_commit_receipt(proof_raw, self._keys, scope)
            proof_revision = proof.commit.revision
            proof_fencing_token = proof.commit.fencing_token
        except CoordinationError:
            proof = deserialize_reconciled_head_receipt(proof_raw, self._keys, scope)
            proof_revision = proof.revision
        if proof_revision != confirmed or (
            proof_fencing_token is not None and proof_fencing_token > fence
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

        reservation = (
            deserialize_commit_reservation(reservation_raw, self._keys, scope)
            if reservation_raw
            else None
        )
        if reservation is not None and (
            reservation.commit.revision != confirmed + 1
            or reservation.commit.fencing_token != fence
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        state_digest = coordination_state_sha256(
            (
                lock_raw or None,
                fence_raw,
                confirmed_raw,
                reservation_raw or None,
                proof_raw,
            )
        )
        return CommitCoordinatorStatus(
            scope,
            "COMMITTING" if reservation is not None else "READY",
            confirmed,
            fence,
            reservation,
            proof,
            state_digest,
        )

    def confirm_commit(
        self, permit: CommitPermit, commit: GraphCommit, request_nonce: str
    ) -> ConfirmationResult:
        if (
            not isinstance(permit, CommitPermit)
            or not isinstance(commit, GraphCommit)
            or permit.operation_id != commit.operation_id
            or permit.revision != commit.revision
            or permit.fencing_token != commit.fencing_token
            or permit.permit_id != commit.permit_id
            or permit.commit_sha256 != graph_commit_sha256(commit)
        ):
            raise CoordinationError("CONFIRMATION_CONFLICT")
        result = self._redis.eval(
            CONFIRM_COMMIT_LUA,
            [
                self._keys.graph_confirmed_revision(permit.scope),
                self._keys.graph_reservation(permit.scope),
                self._keys.graph_last_confirmation(permit.scope),
            ],
            [
                permit.scope,
                str(permit.operation_id),
                str(permit.revision),
                str(permit.fencing_token),
                permit.permit_id,
                permit.commit_sha256,
                canonical_graph_commit_json(commit),
                self._keys.hmac_hex("graph-confirmation-nonce", request_nonce),
                str(commit.revision - 1),
            ],
            nonce_idempotent=True,
        )
        if (
            isinstance(result, list)
            and len(result) == 3
            and result[:2] == ["ERR", "CONFIRMATION_PROOF_EVICTED"]
            and isinstance(result[2], str)
        ):
            return ConfirmationResult(
                "CONFIRMATION_PROOF_EVICTED",
                permit,
                parse_canonical_decimal(result[2], minimum=0),
            )
        code, payload = _tagged(result, {"CONFIRMED", "CONFIRMATION_REPLAYED"})
        proof = deserialize_confirmed_commit_receipt(
            _one_text_payload(payload), self._keys, permit.scope
        )
        if proof.permit != permit or proof.commit != commit:
            raise CoordinationError("CONFIRMATION_CONFLICT")
        return ConfirmationResult(
            cast(Literal["CONFIRMED", "CONFIRMATION_REPLAYED"], code),
            proof.permit,
            proof.commit.revision,
        )


@dataclass(frozen=True, slots=True)
class _AdminSnapshot:
    inspection: CoordinationInspection
    raw: tuple[str | None, ...]


class UpstashCoordinationAdmin:
    """Operator-only exact-state inspection, initialization, and repair."""

    _MISSING = "__SHAJRA_MISSING_V1__"

    def __init__(self, redis: EvalAdapter, keys: RedisKeyBuilder) -> None:
        self._redis = redis
        self._keys = keys

    def inspect(self, scope: str) -> CoordinationInspection:
        return self._inspect_snapshot(scope).inspection

    def _inspect_snapshot(self, scope: str) -> _AdminSnapshot:
        result = self._redis.eval(
            COORDINATION_INSPECT_LUA,
            [
                self._keys.graph_lock(scope),
                self._keys.graph_fence(scope),
                self._keys.graph_confirmed_revision(scope),
                self._keys.graph_reservation(scope),
                self._keys.graph_last_confirmation(scope),
            ],
            [scope],
            nonce_idempotent=False,
        )
        _, payload = _tagged(result, {"INSPECTION"})
        if len(payload) != 5 or not all(isinstance(value, str) for value in payload):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        raw = tuple(value or None for value in payload)
        state_digest = coordination_state_sha256(raw)
        if all(value is None for value in raw):
            return _AdminSnapshot(
                CoordinationInspection(
                    scope,
                    "UNINITIALIZED",
                    None,
                    None,
                    False,
                    None,
                    None,
                    state_digest,
                ),
                raw,
            )

        corrupt = False
        confirmed: int | None = None
        fence: int | None = None
        if raw[2] is not None:
            try:
                confirmed = parse_canonical_decimal(raw[2], minimum=0)
            except CoordinationError:
                corrupt = True
        if raw[1] is not None:
            try:
                fence = parse_canonical_decimal(raw[1], minimum=1)
            except CoordinationError:
                corrupt = True
        if (confirmed is None) != (fence is None):
            corrupt = True

        if raw[0] is not None:
            try:
                lock = inspect_graph_lock(raw[0], self._keys, scope)
                if (fence is not None and lock.fencing_token != fence) or (
                    confirmed is not None and lock.base_revision != confirmed
                ):
                    corrupt = True
            except CoordinationError:
                corrupt = True

        reservation: CommitReservation | None = None
        if raw[3] is not None:
            try:
                reservation = deserialize_commit_reservation(raw[3], self._keys, scope)
            except CoordinationError:
                corrupt = True

        proof: ConfirmedCommitReceipt | ReconciledHeadReceipt | None = None
        proof_revision: int | None = None
        proof_fencing_token: int | None = None
        if raw[4] is not None:
            try:
                proof = deserialize_confirmed_commit_receipt(raw[4], self._keys, scope)
                proof_revision = proof.commit.revision
                proof_fencing_token = proof.commit.fencing_token
            except CoordinationError:
                try:
                    proof = deserialize_reconciled_head_receipt(
                        raw[4], self._keys, scope
                    )
                    proof_revision = proof.revision
                except CoordinationError:
                    corrupt = True
        else:
            corrupt = True

        if (
            confirmed is None
            or fence is None
            or proof_revision != confirmed
            or (proof_fencing_token is not None and proof_fencing_token > fence)
        ):
            corrupt = True
        if reservation is not None and (
            confirmed is None
            or fence is None
            or reservation.commit.revision != confirmed + 1
            or reservation.commit.fencing_token != fence
        ):
            corrupt = True
        mode = (
            "CORRUPT"
            if corrupt
            else ("COMMITTING" if reservation is not None else "READY")
        )
        return _AdminSnapshot(
            CoordinationInspection(
                scope,
                cast(Literal["READY", "COMMITTING", "CORRUPT"], mode),
                confirmed,
                fence,
                raw[0] is not None,
                reservation,
                proof,
                state_digest,
            ),
            raw,
        )

    def initialize(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult:
        return self._transition(
            "initialize", evidence, expected_state_sha256, request_nonce
        )

    def reconcile(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult:
        return self._transition(
            "reconcile", evidence, expected_state_sha256, request_nonce
        )

    def _transition(
        self,
        method: Literal["initialize", "reconcile"],
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult:
        evidence_raw = serialize_coordination_evidence(evidence)
        request = coordination_admin_request(
            self._keys,
            method,
            evidence.scope,
            evidence.evidence_sha256,
            expected_state_sha256,
        )
        snapshot = self._inspect_snapshot(evidence.scope)
        proof = ReconciledHeadReceipt(
            evidence.scope,
            evidence.committed_head_revision,
            evidence.committed_head_semantic_checksum,
            evidence.committed_head_commit_sha256,
            evidence.evidence_sha256,
            self._keys.admin_nonce_hmac(request_nonce),
        )
        proof_raw = serialize_reconciled_head_receipt(proof, self._keys)
        post_state_digest = coordination_state_sha256(
            (
                None,
                str(evidence.fencing_floor),
                str(evidence.committed_head_revision),
                None,
                proof_raw,
            )
        )
        result = self._redis.eval(
            COORDINATION_ADMIN_LUA,
            [
                self._keys.graph_lock(evidence.scope),
                self._keys.graph_fence(evidence.scope),
                self._keys.graph_confirmed_revision(evidence.scope),
                self._keys.graph_reservation(evidence.scope),
                self._keys.graph_last_confirmation(evidence.scope),
                self._keys.graph_admin_result(evidence.scope, request_nonce),
            ],
            [
                request.text,
                request.sha256,
                evidence_raw,
                expected_state_sha256,
                evidence.scope,
                request_nonce,
                proof_raw,
                post_state_digest,
                str(evidence.committed_head_revision),
                str(evidence.fencing_floor),
                method,
                *(
                    value if value is not None else self._MISSING
                    for value in snapshot.raw
                ),
                snapshot.inspection.state_sha256,
            ],
            nonce_idempotent=True,
        )
        expected_code = (
            "ADMIN_INITIALIZED" if method == "initialize" else "ADMIN_RECONCILED"
        )
        _, payload = _tagged(result, {expected_code})
        receipt = deserialize_admin_result_receipt(
            _one_text_payload(payload),
            request,
            self._keys,
            evidence.scope,
            request_nonce,
        )
        if receipt.result.code != expected_code:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.result


class UpstashRevocationStore:
    def __init__(
        self,
        redis: EvalAdapter,
        keys: RedisKeyBuilder,
        *,
        leeway_seconds: int,
    ) -> None:
        if (
            isinstance(leeway_seconds, bool)
            or not isinstance(leeway_seconds, int)
            or not 0 <= leeway_seconds <= 300
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        self._redis = redis
        self._keys = keys
        self._leeway_seconds = leeway_seconds

    def _expires_at_ms(self, token_expires_at_s: int) -> int:
        request = revocation_request(
            self._keys, "validation-jti", token_expires_at_s, self._leeway_seconds
        )
        del request
        expires_at_ms = token_expires_at_s * 1_000 + self._leeway_seconds * 1_000
        if expires_at_ms < 0 or expires_at_ms > 2**63 - 1:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return expires_at_ms

    def revoke(
        self, jti: str, token_expires_at_s: int, request_nonce: str
    ) -> RevocationResult:
        request = revocation_request(
            self._keys, jti, token_expires_at_s, self._leeway_seconds
        )
        expires_at_ms = self._expires_at_ms(token_expires_at_s)
        entry_raw = serialize_revocation_entry(
            self._keys.revocation_jti_hmac(jti), expires_at_ms
        )
        result = self._redis.eval(
            REVOCATION_REVOKE_LUA,
            [
                self._keys.revocation_entry(jti),
                self._keys.revocation_nonce(request_nonce),
            ],
            [
                request.text,
                request.sha256,
                jti,
                str(token_expires_at_s),
                str(self._leeway_seconds),
                entry_raw,
            ],
            nonce_idempotent=True,
        )
        code, payload = _tagged(
            result,
            {
                "REVOKED",
                "ALREADY_REVOKED",
                "TOKEN_ALREADY_EXPIRED",
            },
        )
        receipt = deserialize_revocation_receipt(_one_text_payload(payload), request)
        if receipt.result.code != code:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.result

    def is_revoked(self, jti: str, token_expires_at_s: int) -> RevocationResult:
        revocation_request(self._keys, jti, token_expires_at_s, self._leeway_seconds)
        expected_expiry = self._expires_at_ms(token_expires_at_s)
        jti_hmac = self._keys.revocation_jti_hmac(jti)
        expected_entry = serialize_revocation_entry(jti_hmac, expected_expiry)
        result = self._redis.eval(
            REVOCATION_CHECK_LUA,
            [self._keys.revocation_entry(jti)],
            [
                expected_entry,
                str(token_expires_at_s),
                str(self._leeway_seconds),
                jti_hmac,
            ],
            nonce_idempotent=False,
        )
        code, payload = _tagged(
            result, {"REVOKED", "NOT_REVOKED", "TOKEN_ALREADY_EXPIRED"}
        )
        if (
            len(payload) != 3
            or payload[0] not in {"true", "false"}
            or not isinstance(payload[1], str)
            or not isinstance(payload[2], str)
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        revoked = payload[0] == "true"
        server_time = parse_canonical_decimal(payload[1], minimum=0)
        expires_at = parse_canonical_decimal(payload[2], minimum=0)
        if (
            expires_at != expected_expiry
            or revoked != (code == "REVOKED")
            or (code == "TOKEN_ALREADY_EXPIRED") != (server_time >= expires_at)
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return RevocationResult(
            cast(
                Literal["REVOKED", "NOT_REVOKED", "TOKEN_ALREADY_EXPIRED"],
                code,
            ),
            revoked,
            server_time,
            expires_at,
        )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int
    window_ms: int
    subject_kind: Literal["IP", "IDENTITY"]


RATE_LIMIT_POLICIES = MappingProxyType(
    {
        RateLimitPolicyId.LOGIN: RateLimitPolicy(5, 900_000, "IP"),
        RateLimitPolicyId.SUBMIT: RateLimitPolicy(5, 3_600_000, "IP"),
        RateLimitPolicyId.UPLOAD: RateLimitPolicy(10, 3_600_000, "IDENTITY"),
        RateLimitPolicyId.COMMENT: RateLimitPolicy(20, 3_600_000, "IDENTITY"),
        RateLimitPolicyId.STORY: RateLimitPolicy(20, 3_600_000, "IDENTITY"),
        RateLimitPolicyId.SEARCH: RateLimitPolicy(60, 60_000, "IP"),
        RateLimitPolicyId.EMAIL_VERIFICATION: RateLimitPolicy(10, 3_600_000, "IP"),
    }
)


class UpstashRateLimiter:
    def __init__(self, redis: EvalAdapter, keys: RedisKeyBuilder) -> None:
        self._redis = redis
        self._keys = keys

    def _subject(
        self,
        policy: RateLimitPolicy,
        subject: IpRateLimitSubject | IdentityRateLimitSubject,
    ) -> tuple[Literal["IP", "IDENTITY"], str]:
        if (
            policy.subject_kind == "IP"
            and type(subject) is IpRateLimitSubject
            and subject.kind == "IP"
            and isinstance(subject.normalized_ip, str)
        ):
            try:
                normalized = str(ip_address(subject.normalized_ip))
            except (TypeError, ValueError):
                raise CoordinationError("COORDINATION_STATE_CORRUPT") from None
            if normalized != subject.normalized_ip:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            return "IP", normalized
        if (
            policy.subject_kind == "IDENTITY"
            and type(subject) is IdentityRateLimitSubject
            and subject.kind == "IDENTITY"
            and isinstance(subject.identity_id, str)
            and subject.identity_id
            and "\x00" not in subject.identity_id
        ):
            return "IDENTITY", subject.identity_id
        raise CoordinationError("COORDINATION_STATE_CORRUPT")

    def consume(
        self,
        policy: RateLimitPolicyId,
        subject: IpRateLimitSubject | IdentityRateLimitSubject,
        request_nonce: str,
    ) -> RateLimitResult:
        if not isinstance(policy, RateLimitPolicyId):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        definition = RATE_LIMIT_POLICIES[policy]
        subject_kind, subject_value = self._subject(definition, subject)
        time_result = self._redis.eval(RATE_TIME_LUA, [], [], nonce_idempotent=False)
        _, time_payload = _tagged(time_result, {"TIME"})
        server_time_ms = parse_canonical_decimal(
            _one_text_payload(time_payload), minimum=0
        )
        window_start_ms = (
            server_time_ms // definition.window_ms
        ) * definition.window_ms
        request = rate_request(
            self._keys,
            policy,
            subject_kind,
            subject_value,
            window_start_ms,
            definition.window_ms,
            definition.limit,
        )
        result = self._redis.eval(
            RATE_CONSUME_LUA,
            [
                self._keys.rate_counter(
                    policy, subject_kind, subject_value, window_start_ms
                ),
                self._keys.rate_nonce(request_nonce),
            ],
            [
                request.text,
                request.sha256,
                policy.value,
                subject_kind,
                str(window_start_ms),
                str(definition.window_ms),
                str(definition.limit),
            ],
            nonce_idempotent=True,
        )
        code, payload = _tagged(result, {"RATE_LIMIT_ALLOWED", "RATE_LIMIT_DENIED"})
        receipt = deserialize_rate_receipt(_one_text_payload(payload), request)
        if receipt.result.allowed != (code == "RATE_LIMIT_ALLOWED"):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        return receipt.result
