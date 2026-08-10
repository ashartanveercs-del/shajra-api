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
local function canonical_increment(value)
  if not canonical_nonnegative(value) then return nil end
  local digits = {}
  local carry = 1
  for index = #value, 1, -1 do
    local digit = string.byte(value, index) - 48 + carry
    if digit >= 10 then digit = digit - 10 carry = 1 else carry = 0 end
    table.insert(digits, 1, string.char(48 + digit))
  end
  if carry == 1 then table.insert(digits, 1, '1') end
  local result = table.concat(digits)
  if not canonical_nonnegative(result) then return nil end
  return result
end
local function decimal_compare(left, right)
  if #left < #right then return -1 end
  if #left > #right then return 1 end
  if left < right then return -1 end
  if left > right then return 1 end
  return 0
end
local function decimal_lte(left, right)
  return decimal_compare(left, right) <= 0
end
local function decimal_add(left, right)
  if not canonical_nonnegative(left) or not canonical_nonnegative(right) then
    return nil
  end
  local digits = {}
  local carry = 0
  local left_index = #left
  local right_index = #right
  while left_index >= 1 or right_index >= 1 or carry ~= 0 do
    local left_digit = left_index >= 1 and string.byte(left, left_index) - 48 or 0
    local right_digit = right_index >= 1 and string.byte(right, right_index) - 48 or 0
    local digit = left_digit + right_digit + carry
    carry = math.floor(digit / 10)
    table.insert(digits, 1, string.char(48 + (digit % 10)))
    left_index = left_index - 1
    right_index = right_index - 1
  end
  local result = table.concat(digits)
  if not canonical_nonnegative(result) then return nil end
  return result
end
local function decimal_subtract(left, right)
  if not canonical_nonnegative(left) or not canonical_nonnegative(right)
      or decimal_compare(left, right) < 0 then return nil end
  local digits = {}
  local borrow = 0
  local offset = #left - #right
  for index = #left, 1, -1 do
    local right_index = index - offset
    local right_digit = right_index >= 1 and string.byte(right, right_index) - 48 or 0
    local digit = string.byte(left, index) - 48 - borrow - right_digit
    if digit < 0 then digit = digit + 10 borrow = 1 else borrow = 0 end
    table.insert(digits, 1, string.char(48 + digit))
  end
  local result = string.gsub(table.concat(digits), '^0+', '')
  if result == '' then result = '0' end
  return result
end
local function decimal_times_1000(value)
  if not canonical_nonnegative(value) then return nil end
  if value == '0' then return '0' end
  local result = value .. '000'
  if not canonical_nonnegative(result) then return nil end
  return result
end
local function decimal_mod_small(value, divisor)
  if not canonical_nonnegative(value) or type(divisor) ~= 'number'
      or divisor < 1 or divisor ~= math.floor(divisor) then return nil end
  local remainder = 0
  for index = 1, #value do
    remainder = ((remainder * 10) + string.byte(value, index) - 48) % divisor
  end
  return remainder
end
local function redis_time_ms(clock)
  local seconds = tostring(clock[1])
  local microseconds = tostring(clock[2])
  if not canonical_nonnegative(seconds) or not canonical_nonnegative(microseconds)
      or decimal_compare(microseconds, '999999') > 0 then return nil end
  local seconds_ms = decimal_times_1000(seconds)
  local millisecond_part = tostring(math.floor(tonumber(microseconds) / 1000))
  if not seconds_ms then return nil end
  return decimal_add(seconds_ms, millisecond_part)
end
local function valid_digest(value)
  return type(value) == 'string' and #value == 64
    and not string.find(value, '[^0-9a-f]')
end
local SHA256_CONSTANTS = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,
  0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
  0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,
  0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,
  0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
  0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,
  0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,
  0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
  0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2}
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
local function xor_u32(...)
  local result = 0
  for index = 1, select('#', ...) do
    result = binary_u32(result, select(index, ...), 'xor')
  end
  return result
end
local function and_u32(...)
  local result = 4294967295
  for index = 1, select('#', ...) do
    result = binary_u32(result, select(index, ...), 'and')
  end
  return result
end
local function not_u32(value)
  return 4294967295 - (value % 4294967296)
end
local function rshift_u32(value, width)
  return math.floor((value % 4294967296) / (2 ^ width))
end
local function ror_u32(value, width)
  value = value % 4294967296
  local divisor = 2 ^ width
  return math.floor(value / divisor) + ((value % divisor) * (2 ^ (32 - width)))
end
local function add_u32(...)
  local sum = 0
  for index = 1, select('#', ...) do
    local value = select(index, ...)
    sum = (sum + value) % 4294967296
  end
  return sum
end
local function u32_bytes(value)
  value = value % 4294967296
  return string.char(math.floor(value / 16777216) % 256,
    math.floor(value / 65536) % 256, math.floor(value / 256) % 256,
    value % 256)
end
local function sha256_hex(value)
  if type(value) ~= 'string' then return nil end
  local bit_length = #value * 8
  local high = math.floor(bit_length / 4294967296)
  local low = bit_length % 4294967296
  local padding = (56 - ((#value + 1) % 64)) % 64
  local message = value .. string.char(0x80) .. string.rep(string.char(0), padding)
    .. u32_bytes(high) .. u32_bytes(low)
  local hashes = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
    0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}
  for offset = 1, #message, 64 do
    local words = {}
    for index = 0, 15 do
      local position = offset + (index * 4)
      words[index + 1] = (string.byte(message, position) * 16777216)
        + (string.byte(message, position + 1) * 65536)
        + (string.byte(message, position + 2) * 256)
        + string.byte(message, position + 3)
    end
    for index = 17, 64 do
      local left = words[index - 15]
      local right = words[index - 2]
      local sigma0 = xor_u32(ror_u32(left, 7), ror_u32(left, 18),
        rshift_u32(left, 3))
      local sigma1 = xor_u32(ror_u32(right, 17), ror_u32(right, 19),
        rshift_u32(right, 10))
      words[index] = add_u32(words[index - 16], sigma0, words[index - 7], sigma1)
    end
    local a,b,c,d,e,f,g,h = hashes[1],hashes[2],hashes[3],hashes[4],
      hashes[5],hashes[6],hashes[7],hashes[8]
    for index = 1, 64 do
      local choice = xor_u32(and_u32(e, f), and_u32(not_u32(e), g))
      local majority = xor_u32(and_u32(a, b), and_u32(a, c), and_u32(b, c))
      local sigma0 = xor_u32(ror_u32(a, 2), ror_u32(a, 13), ror_u32(a, 22))
      local sigma1 = xor_u32(ror_u32(e, 6), ror_u32(e, 11), ror_u32(e, 25))
      local first = add_u32(h, sigma1, choice, SHA256_CONSTANTS[index], words[index])
      local second = add_u32(sigma0, majority)
      h,g,f,e,d,c,b,a = g,f,e,add_u32(d, first),c,b,a,add_u32(first, second)
    end
    for index, value_hash in ipairs({a,b,c,d,e,f,g,h}) do
      hashes[index] = add_u32(hashes[index], value_hash)
    end
  end
  local result = {}
  for _, hash in ipairs(hashes) do
    table.insert(result, string.format('%08x', hash))
  end
  return table.concat(result)
end
local function valid_text(value, maximum)
  return type(value) == 'string' and value ~= '' and #value <= maximum
    and not string.find(value, '\0', 1, true)
end
local function valid_deployment(value)
  return type(value) == 'string' and #value >= 1 and #value <= 32
    and not string.find(value, '[^a-z0-9-]')
    and string.sub(value, 1, 1) ~= '-'
    and string.sub(value, -1) ~= '-'
    and not string.find(value, '--', 1, true)
end
local function scope_key_parts(key, domain)
  if type(key) ~= 'string' then return nil end
  local tag, deployment, scope_hmac, suffix = string.match(key,
    '^({sj:v1:([a-z0-9-]+):' .. domain .. ':([0-9a-f]+)}):(.+)$')
  if not tag or not valid_deployment(deployment) or not valid_digest(scope_hmac)
      or not valid_text(suffix, 256) then return nil end
  return tag, scope_hmac, suffix
end
local function global_key_parts(key, domain)
  if type(key) ~= 'string' then return nil end
  local tag, deployment, suffix = string.match(key,
    '^({sj:v1:([a-z0-9-]+):' .. domain .. '}):(.+)$')
  if not tag or not valid_deployment(deployment)
      or not valid_text(suffix, 256) then return nil end
  return tag, suffix
end
local function digest_suffix(suffix, prefix)
  if type(suffix) ~= 'string' or string.sub(suffix, 1, #prefix) ~= prefix then
    return nil
  end
  local digest = string.sub(suffix, #prefix + 1)
  if not valid_digest(digest) then return nil end
  return digest
end
local function graph_core_key_parts(keys)
  local tag, scope_hmac, suffix = scope_key_parts(keys[1], 'graph')
  if not tag or suffix ~= 'lock' then return nil end
  local expected_suffixes = {'fence', 'confirmed-revision', 'commit-reservation',
    'last-confirmation'}
  for index = 2, 5 do
    local other_tag, other_scope_hmac, other_suffix = scope_key_parts(
      keys[index], 'graph')
    if other_tag ~= tag or other_scope_hmac ~= scope_hmac
        or other_suffix ~= expected_suffixes[index - 1] then return nil end
  end
  return tag, scope_hmac
end
local function ensure_exact_expiry(key, expected)
  if not canonical_positive(expected) then return false end
  if decimal_compare(expected, '9007199254740991') > 0 then
    return false
  end
  local observed = redis.call('PEXPIRETIME', key)
  local target = tonumber(expected)
  if observed == -1 or (observed >= 0 and observed < target) then
    redis.call('PEXPIREAT', key, expected)
    return true
  end
  return observed == target
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
local function exact_table(value, schema, fields)
  if type(value) ~= 'table' or value.schema ~= schema or value.version ~= 1 then
    return nil
  end
  for key, _ in pairs(value) do if not fields[key] then return nil end end
  for key, _ in pairs(fields) do if value[key] == nil then return nil end end
  return value
end
local function consume_literal(raw, position, literal)
  if string.sub(raw, position, position + #literal - 1) ~= literal then return nil end
  return position + #literal
end
local function consume_json_string(raw, position)
  if string.byte(raw, position) ~= 0x22 then return nil end
  position = position + 1
  while position <= #raw do
    local byte = string.byte(raw, position)
    if byte == 0x22 then return position + 1 end
    if byte == 0x5C then
      position = position + 1
      if position > #raw then return nil end
    elseif byte < 0x20 then
      return nil
    end
    position = position + 1
  end
  return nil
end
local function consume_positive_decimal(raw, position)
  local first = position
  while position <= #raw do
    local byte = string.byte(raw, position)
    if byte < 0x30 or byte > 0x39 then break end
    position = position + 1
  end
  local value = string.sub(raw, first, position - 1)
  if not canonical_positive(value) then return nil end
  return position, value, first
end
local function masked_graph_commit(raw)
  local position = consume_literal(raw, 1, '{"committed_at":')
  if not position then return nil end
  position = consume_json_string(raw, position)
  if not position then return nil end
  position = consume_literal(raw, position, ',"fencing_token":')
  if not position then return nil end
  local fence_end, fence, fence_start = consume_positive_decimal(raw, position)
  if not fence_end then return nil end
  position = consume_literal(raw, fence_end, ',"operation_id":')
  if not position then return nil end
  position = consume_json_string(raw, position)
  if not position then return nil end
  position = consume_literal(raw, position, ',"permit_id":')
  if not position then return nil end
  position = consume_json_string(raw, position)
  if not position then return nil end
  position = consume_literal(raw, position, ',"revision":')
  if not position then return nil end
  local revision_end, revision, revision_start = consume_positive_decimal(raw, position)
  if not revision_end then return nil end
  position = consume_literal(raw, revision_end, ',"semantic_checksum":')
  if not position then return nil end
  position = consume_json_string(raw, position)
  if not position or position ~= #raw or string.sub(raw, position, position) ~= '}' then
    return nil
  end
  local masked = string.sub(raw, 1, fence_start - 1) .. '1'
    .. string.sub(raw, fence_end, revision_start - 1) .. '1'
    .. string.sub(raw, revision_end)
  return masked, revision, fence
end
local function strict_graph_commit(raw)
  if type(raw) ~= 'string' then return nil end
  local masked, revision, fence = masked_graph_commit(raw)
  if not masked then return nil end
  local commit = decode_object(masked)
  if not commit then return nil end
  local fields = {operation_id=true,revision=true,fencing_token=true,
    permit_id=true,semantic_checksum=true,committed_at=true}
  for key, _ in pairs(commit) do if not fields[key] then return nil end end
  for key, _ in pairs(fields) do if commit[key] == nil then return nil end end
  local ok, canonical = pcall(canonical_json, commit)
  if not ok or canonical ~= masked or not valid_text(commit.operation_id, 512)
      or not valid_text(commit.permit_id, 512)
      or not valid_digest(commit.semantic_checksum)
      or not valid_text(commit.committed_at, 512) then return nil end
  return commit, revision, fence
end
local function strict_reservation(raw)
  local reservation = strict_object(raw, 'shajra.commit-reservation', {
    schema=true,version=true,state=true,scope_hmac=true,permit=true,
    commit_json=true,commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true,authorization_request_nonce_hmac=true})
  if not reservation or reservation.state ~= 'COMMITTING'
      or not valid_digest(reservation.scope_hmac)
      or not valid_digest(reservation.commit_sha256)
      or not valid_digest(reservation.staged_write_receipt_sha256)
      or not valid_digest(reservation.authorization_request_nonce_hmac) then
    return nil
  end
  local permit = exact_table(reservation.permit, 'shajra.commit-permit', {
    schema=true,version=true,scope=true,operation_id=true,revision=true,
    fencing_token=true,permit_id=true,commit_sha256=true})
  if not permit or type(permit.scope) ~= 'string' or permit.scope == ''
      or type(permit.operation_id) ~= 'string'
      or string.sub(permit.operation_id, 1, 3) ~= 'op_' or #permit.operation_id <= 3
      or not canonical_positive(permit.revision)
      or not canonical_positive(permit.fencing_token)
      or type(permit.permit_id) ~= 'string'
      or string.sub(permit.permit_id, 1, 4) ~= 'cpr_' or #permit.permit_id <= 4
      or not valid_digest(permit.commit_sha256) then return nil end
  local commit, commit_revision, commit_fence = strict_graph_commit(
    reservation.commit_json)
  if not commit or sha256_hex(reservation.commit_json) ~= reservation.commit_sha256 then
    return nil
  end
  if not canonical_positive(commit_revision) or not canonical_positive(commit_fence)
      or permit.operation_id ~= commit.operation_id
      or permit.revision ~= commit_revision
      or permit.fencing_token ~= commit_fence
      or permit.permit_id ~= commit.permit_id
      or permit.commit_sha256 ~= reservation.commit_sha256 then return nil end
  if type(reservation.staged_write_receipt_json) ~= 'string'
      or sha256_hex(reservation.staged_write_receipt_json)
        ~= reservation.staged_write_receipt_sha256 then return nil end
  local staged = strict_object(reservation.staged_write_receipt_json,
    'shajra.staged-write-receipt', {schema=true,version=true,operation_id=true,
      revision=true,fencing_token=true,write_set_json=true,write_set_sha256=true})
  if not staged or type(staged.operation_id) ~= 'string'
      or not canonical_positive(staged.revision)
      or not canonical_positive(staged.fencing_token)
      or type(staged.write_set_json) ~= 'string'
      or not valid_digest(staged.write_set_sha256)
      or sha256_hex(staged.write_set_json) ~= staged.write_set_sha256
      or staged.operation_id ~= permit.operation_id
      or staged.revision ~= permit.revision
      or staged.fencing_token ~= permit.fencing_token then return nil end
  return reservation, permit, commit, staged
end
"""


GENERIC_ACQUIRE_LUA = (
    "-- shajra:generic-acquire:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 5 or not canonical_positive(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lock_tag, key_scope_hmac, lock_suffix = scope_key_parts(KEYS[1], 'generic')
local receipt_tag, receipt_scope_hmac, receipt_suffix = scope_key_parts(
  KEYS[2], 'generic')
local receipt_acquisition_hmac = receipt_suffix
  and digest_suffix(receipt_suffix, 'lease-result:acquire:')
if not lock_tag or lock_suffix ~= 'lock' or receipt_tag ~= lock_tag
    or receipt_scope_hmac ~= key_scope_hmac or not receipt_acquisition_hmac
    or not valid_digest(ARGV[2]) or not valid_text(ARGV[3], 512)
    or not valid_text(ARGV[4], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-acquire-request', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,requested_ttl_ms=true})
if not request or request.domain ~= 'GENERIC'
    or request.requested_ttl_ms ~= ARGV[5]
    or not valid_digest(request.scope_hmac)
    or not valid_digest(request.acquisition_id_hmac)
    or request.scope_hmac ~= key_scope_hmac
    or request.acquisition_id_hmac ~= receipt_acquisition_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-acquisition-result', {
    schema=true,version=true,input_sha256=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,requested_ttl_ms=true,lease=true,
    receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
      or receipt.domain ~= 'GENERIC'
      or receipt.scope_hmac ~= key_scope_hmac
      or receipt.acquisition_id_hmac ~= receipt_acquisition_hmac
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
local current = redis.call('GET', KEYS[1])
if current then
  local lock = strict_object(current, 'shajra.generic-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,expires_at_ms=true,ttl_ms=true,
    renew_deadline_ms=true})
  local pttl = redis.call('PTTL', KEYS[1])
  if not lock or lock.domain ~= 'GENERIC' or lock.scope_hmac ~= request.scope_hmac
      or not valid_digest(lock.scope_hmac)
      or not valid_digest(lock.acquisition_id_hmac)
      or not canonical_positive(lock.expires_at_ms)
      or not canonical_positive(lock.ttl_ms)
      or not canonical_nonnegative(lock.renew_deadline_ms)
      or pttl <= 0 or pttl > tonumber(lock.ttl_ms)
      or decimal_subtract(lock.expires_at_ms, lock.renew_deadline_ms) ~= '5000' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'ERR', 'LOCK_UNAVAILABLE'}
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
local provisional_expires = server_ms and decimal_add(server_ms, ARGV[5])
local provisional_deadline = provisional_expires
  and decimal_subtract(provisional_expires, '5000')
if not server_ms or not provisional_expires or not provisional_deadline then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local provisional_lock = canonical_json({schema='shajra.generic-lock',version=1,domain='GENERIC',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  expires_at_ms=provisional_expires,ttl_ms=ARGV[5],
  renew_deadline_ms=provisional_deadline})
redis.call('SET', KEYS[1], provisional_lock, 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[1])
if pttl <= 0 or pttl > requested_ttl then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local pttl_text = tostring(pttl)
local applied_expiry = redis.call('PEXPIRETIME', KEYS[1])
local expires = tostring(applied_expiry)
local deadline = decimal_subtract(expires, '5000')
if applied_expiry <= 0 or not deadline then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lock = canonical_json({schema='shajra.generic-lock',version=1,domain='GENERIC',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  expires_at_ms=expires,ttl_ms=pttl_text,renew_deadline_ms=deadline})
redis.call('SET', KEYS[1], lock, 'KEEPTTL')
local final_pttl = redis.call('PTTL', KEYS[1])
local final_expiry = redis.call('PEXPIRETIME', KEYS[1])
if final_pttl <= 0 or final_pttl > pttl or tostring(final_expiry) ~= expires then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lease = {schema='shajra.generic-lease',version=1,scope=ARGV[3],
  acquisition_id=ARGV[4],expires_at_ms=expires,ttl_ms=pttl_text,
  renew_deadline_ms=deadline}
local lease_started = decimal_subtract(expires, pttl_text)
local receipt_expires = lease_started and decimal_add(lease_started, '60000')
if not receipt_expires then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local result = canonical_json({schema='shajra.lease-acquisition-result',version=1,
  input_sha256=ARGV[2],domain='GENERIC',scope_hmac=request.scope_hmac,
  acquisition_id_hmac=request.acquisition_id_hmac,requested_ttl_ms=ARGV[5],
  lease=lease,receipt_expires_at_ms=receipt_expires})
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], receipt_expires)
return {'OK', 'LEASE_ACQUIRED', result}
"""
)


GRAPH_ACQUIRE_LUA = (
    "-- shajra:graph-acquire:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
local MISSING = '__SHAJRA_MISSING_V1__'
if #KEYS ~= 6 or (#ARGV ~= 5 and #ARGV ~= 10)
    or not canonical_positive(ARGV[5]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local core_tag, key_scope_hmac = graph_core_key_parts(KEYS)
local receipt_tag, receipt_scope_hmac, receipt_suffix = scope_key_parts(
  KEYS[6], 'graph')
local receipt_acquisition_hmac = receipt_suffix
  and digest_suffix(receipt_suffix, 'lease-result:acquire:')
if not core_tag or receipt_tag ~= core_tag
    or receipt_scope_hmac ~= key_scope_hmac or not receipt_acquisition_hmac
    or not valid_digest(ARGV[2]) or not valid_text(ARGV[3], 512)
    or not valid_text(ARGV[4], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local request = strict_object(ARGV[1], 'shajra.lease-acquire-request', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,requested_ttl_ms=true,committed_revision=true})
if not request or request.domain ~= 'GRAPH_COMMIT'
    or request.requested_ttl_ms ~= ARGV[5]
    or not canonical_nonnegative(request.committed_revision)
    or not valid_digest(request.scope_hmac)
    or not valid_digest(request.acquisition_id_hmac)
    or request.scope_hmac ~= key_scope_hmac
    or request.acquisition_id_hmac ~= receipt_acquisition_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[6])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-acquisition-result', {
    schema=true,version=true,input_sha256=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,requested_ttl_ms=true,committed_revision=true,
    lease=true,receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
      or receipt.domain ~= 'GRAPH_COMMIT'
      or receipt.scope_hmac ~= key_scope_hmac
      or receipt.acquisition_id_hmac ~= receipt_acquisition_hmac
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
local current = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
local current_pttl = -2
if current[1] then current_pttl = redis.call('PTTL', KEYS[1]) end
if #ARGV == 5 then
  return {'OK', 'GRAPH_PREFLIGHT', current[3] or '', current[2] or '',
    current[1] or '', tostring(current_pttl), current[4] or '', current[5] or ''}
end
for index = 1, 5 do
  local expected = ARGV[5 + index]
  if expected == MISSING then
    if current[index] then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  elseif not current[index] or current[index] ~= expected then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
end
local confirmed = current[3]
local fence = current[2]
if not confirmed or not fence then return {'ERR', 'COORDINATION_UNINITIALIZED'} end
if not canonical_nonnegative(confirmed) or not canonical_positive(fence) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local proof_raw = current[5]
local proof = decode_object(proof_raw or '')
if not proof or type(proof.schema) ~= 'string' then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if proof.schema == 'shajra.confirmed-commit-receipt' then
  proof = strict_object(proof_raw, 'shajra.confirmed-commit-receipt', {
    schema=true,version=true,scope_hmac=true,permit=true,commit_json=true,
    commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true})
  if not proof or not proof.permit or proof.permit.revision ~= confirmed
      or proof.scope_hmac ~= key_scope_hmac then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
elseif proof.schema == 'shajra.reconciled-head-receipt' then
  proof = strict_object(proof_raw, 'shajra.reconciled-head-receipt', {
    schema=true,version=true,scope_hmac=true,revision=true,
    semantic_checksum=true,head_commit_sha256=true,evidence_sha256=true,
    admin_request_nonce_hmac=true,proof_sha256=true})
  if not proof or proof.revision ~= confirmed or proof.scope_hmac ~= key_scope_hmac then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
else
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if confirmed ~= request.committed_revision then
  return {'ERR', 'COORDINATION_REVISION_MISMATCH'}
end
local reservation = current[4]
if reservation then
  local decoded_reservation, reservation_permit = strict_reservation(reservation)
  if not decoded_reservation or decoded_reservation.scope_hmac ~= key_scope_hmac
      or reservation_permit.revision ~= canonical_increment(confirmed)
      or decimal_compare(reservation_permit.fencing_token, fence) > 0 then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'ERR', 'COMMIT_RECOVERY_REQUIRED'}
end
local current_lock = current[1]
if current_lock then
  local lock = strict_object(current_lock, 'shajra.graph-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,fencing_token=true,base_revision=true,
    expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
  local pttl = current_pttl
  if not lock or lock.domain ~= 'GRAPH_COMMIT' or lock.scope_hmac ~= request.scope_hmac
      or not valid_digest(lock.scope_hmac)
      or not valid_digest(lock.acquisition_id_hmac)
      or not canonical_positive(lock.fencing_token)
      or not canonical_nonnegative(lock.base_revision)
      or not canonical_positive(lock.expires_at_ms)
      or not canonical_positive(lock.ttl_ms)
      or not canonical_nonnegative(lock.renew_deadline_ms)
      or lock.fencing_token ~= fence or lock.base_revision ~= confirmed
      or pttl <= 0 or pttl > tonumber(lock.ttl_ms)
      or decimal_subtract(lock.expires_at_ms, lock.renew_deadline_ms) ~= '5000' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'ERR', 'LOCK_UNAVAILABLE'}
end
local next_fence = canonical_increment(fence)
if not next_fence then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
local provisional_expires = server_ms and decimal_add(server_ms, ARGV[5])
local provisional_deadline = provisional_expires
  and decimal_subtract(provisional_expires, '5000')
if not server_ms or not provisional_expires or not provisional_deadline then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local provisional_lock = canonical_json({schema='shajra.graph-lock',version=1,domain='GRAPH_COMMIT',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  fencing_token=next_fence,base_revision=request.committed_revision,
  expires_at_ms=provisional_expires,ttl_ms=ARGV[5],
  renew_deadline_ms=provisional_deadline})
redis.call('SET', KEYS[1], provisional_lock, 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[1])
if pttl <= 0 or pttl > requested_ttl then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local pttl_text = tostring(pttl)
local applied_expiry = redis.call('PEXPIRETIME', KEYS[1])
local expires = tostring(applied_expiry)
local deadline = decimal_subtract(expires, '5000')
if applied_expiry <= 0 or not deadline then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lock = canonical_json({schema='shajra.graph-lock',version=1,domain='GRAPH_COMMIT',
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  fencing_token=next_fence,base_revision=request.committed_revision,
  expires_at_ms=expires,ttl_ms=pttl_text,renew_deadline_ms=deadline})
redis.call('SET', KEYS[1], lock, 'KEEPTTL')
local final_pttl = redis.call('PTTL', KEYS[1])
local final_expiry = redis.call('PEXPIRETIME', KEYS[1])
if final_pttl <= 0 or final_pttl > pttl or tostring(final_expiry) ~= expires then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lease = {schema='shajra.graph-lease',version=1,scope=ARGV[3],
  acquisition_id=ARGV[4],fencing_token=next_fence,
  base_revision=request.committed_revision,expires_at_ms=expires,
  ttl_ms=pttl_text,renew_deadline_ms=deadline}
local lease_started = decimal_subtract(expires, pttl_text)
local receipt_expires = lease_started and decimal_add(lease_started, '60000')
if not receipt_expires then
  redis.call('DEL', KEYS[1])
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local result = canonical_json({schema='shajra.lease-acquisition-result',version=1,
  input_sha256=ARGV[2],domain='GRAPH_COMMIT',scope_hmac=request.scope_hmac,
  acquisition_id_hmac=request.acquisition_id_hmac,requested_ttl_ms=ARGV[5],
  committed_revision=request.committed_revision,lease=lease,
  receipt_expires_at_ms=receipt_expires})
redis.call('SET', KEYS[2], next_fence)
redis.call('SET', KEYS[6], result)
redis.call('PEXPIREAT', KEYS[6], receipt_expires)
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
local request = strict_object(ARGV[1], 'shajra.lease-operation-request', {
  schema=true,version=true,method=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,request_nonce_hmac=true,lock_sha256=true,
  requested_ttl_ms=true})
if not request or request.method ~= 'renew'
    or (request.domain ~= 'GENERIC' and request.domain ~= 'GRAPH_COMMIT')
    or request.requested_ttl_ms ~= ARGV[5]
    or not decimal_lte(ARGV[5], '300000')
    or not valid_digest(ARGV[2]) or not valid_digest(request.scope_hmac)
    or not valid_digest(request.acquisition_id_hmac)
    or not valid_digest(request.request_nonce_hmac)
    or not valid_digest(request.lock_sha256)
    or not valid_text(ARGV[3], 512) or not valid_text(ARGV[4], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local key_domain = request.domain == 'GENERIC' and 'generic' or 'graph'
local lock_tag, key_scope_hmac, lock_suffix = scope_key_parts(KEYS[1], key_domain)
local receipt_tag, receipt_scope_hmac, receipt_suffix = scope_key_parts(
  KEYS[2], key_domain)
local receipt_nonce_hmac = receipt_suffix
  and digest_suffix(receipt_suffix, 'lease-result:operation:')
if not lock_tag or lock_suffix ~= 'lock' or receipt_tag ~= lock_tag
    or receipt_scope_hmac ~= key_scope_hmac or not receipt_nonce_hmac
    or request.scope_hmac ~= key_scope_hmac
    or request.request_nonce_hmac ~= receipt_nonce_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-operation-result', {
    schema=true,version=true,input_sha256=true,method=true,domain=true,
    scope_hmac=true,acquisition_id_hmac=true,request_nonce_hmac=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
      or receipt.domain ~= request.domain
      or receipt.scope_hmac ~= key_scope_hmac
      or receipt.acquisition_id_hmac ~= request.acquisition_id_hmac
      or receipt.request_nonce_hmac ~= receipt_nonce_hmac
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 ~= ARGV[2] then return {'ERR', 'NONCE_REUSE_CONFLICT'} end
  if receipt.method ~= 'renew' then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  return {'OK', 'LEASE_RENEW_REPLAYED', retained}
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
if not lock_value or lock_value.domain ~= request.domain
    or lock_value.scope_hmac ~= request.scope_hmac
    or lock_value.acquisition_id_hmac ~= request.acquisition_id_hmac
    or not valid_digest(lock_value.scope_hmac)
    or not valid_digest(lock_value.acquisition_id_hmac)
    or not canonical_positive(lock_value.expires_at_ms)
    or not canonical_positive(lock_value.ttl_ms)
    or not decimal_lte(lock_value.ttl_ms, '300000')
    or not canonical_nonnegative(lock_value.renew_deadline_ms)
    or decimal_subtract(lock_value.expires_at_ms,
      lock_value.renew_deadline_ms) ~= '5000'
    or current_pttl > tonumber(lock_value.ttl_ms) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if request.domain == 'GRAPH_COMMIT'
    and (not canonical_positive(lock_value.fencing_token)
      or not canonical_nonnegative(lock_value.base_revision)) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if current ~= ARGV[6] then return {'ERR', 'LEASE_LOST'} end
local original_expires_at_ms = redis.call('PEXPIRETIME', KEYS[1])
if original_expires_at_ms <= 0
    or tostring(original_expires_at_ms) ~= lock_value.expires_at_ms then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local function restore_original_lock()
  redis.call('SET', KEYS[1], current)
  redis.call('PEXPIREAT', KEYS[1], tostring(original_expires_at_ms))
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
if not server_ms then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if decimal_lte(lock_value.renew_deadline_ms, server_ms) then
  return {'ERR', 'LEASE_LOST'}
end
local requested_ttl = tonumber(ARGV[5])
if not requested_ttl or requested_ttl > 300000 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local provisional_expires = decimal_add(server_ms, ARGV[5])
local provisional_deadline = provisional_expires
  and decimal_subtract(provisional_expires, '5000')
if not provisional_expires or not provisional_deadline then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
lock_value.expires_at_ms = provisional_expires
lock_value.ttl_ms = ARGV[5]
lock_value.renew_deadline_ms = provisional_deadline
redis.call('SET', KEYS[1], canonical_json(lock_value), 'PX', ARGV[5])
local pttl = redis.call('PTTL', KEYS[1])
if pttl <= 0 or pttl > requested_ttl then
  restore_original_lock()
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local pttl_text = tostring(pttl)
local applied_expiry = redis.call('PEXPIRETIME', KEYS[1])
local expires = tostring(applied_expiry)
local deadline = decimal_subtract(expires, '5000')
if applied_expiry <= 0 or not deadline then
  restore_original_lock()
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
lock_value.expires_at_ms = expires
lock_value.ttl_ms = pttl_text
lock_value.renew_deadline_ms = deadline
local next_lock = canonical_json(lock_value)
redis.call('SET', KEYS[1], next_lock, 'KEEPTTL')
local final_pttl = redis.call('PTTL', KEYS[1])
local final_expiry = redis.call('PEXPIRETIME', KEYS[1])
if final_pttl <= 0 or final_pttl > pttl or tostring(final_expiry) ~= expires then
  restore_original_lock()
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lease = {schema=(request.domain == 'GENERIC' and 'shajra.generic-lease' or 'shajra.graph-lease'),
  version=1,scope=ARGV[3],acquisition_id=ARGV[4],expires_at_ms=expires,
  ttl_ms=pttl_text,renew_deadline_ms=deadline}
if request.domain == 'GRAPH_COMMIT' then
  lease.fencing_token=lock_value.fencing_token
  lease.base_revision=lock_value.base_revision
end
local lease_started = decimal_subtract(expires, pttl_text)
local receipt_expires = lease_started and decimal_add(lease_started, '60000')
if not receipt_expires then
  restore_original_lock()
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local result = canonical_json({schema='shajra.lease-operation-result',version=1,
  input_sha256=ARGV[2],method='renew',domain=request.domain,
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  request_nonce_hmac=request.request_nonce_hmac,result=lease,
  receipt_expires_at_ms=receipt_expires})
redis.call('SET', KEYS[2], result)
redis.call('PEXPIREAT', KEYS[2], receipt_expires)
return {'OK', 'LEASE_RENEWED', result}
"""
)


LEASE_RELEASE_LUA = (
    "-- shajra:lease-release:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 5 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local request = strict_object(ARGV[1], 'shajra.lease-operation-request', {
  schema=true,version=true,method=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,request_nonce_hmac=true,lock_sha256=true})
if not request or request.method ~= 'release'
    or (request.domain ~= 'GENERIC' and request.domain ~= 'GRAPH_COMMIT')
    or not valid_digest(ARGV[2]) or not valid_digest(request.scope_hmac)
    or not valid_digest(request.acquisition_id_hmac)
    or not valid_digest(request.request_nonce_hmac)
    or not valid_digest(request.lock_sha256)
    or sha256_hex(ARGV[1]) ~= ARGV[2]
    or not valid_text(ARGV[3], 512) or not valid_text(ARGV[4], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local key_domain = request.domain == 'GENERIC' and 'generic' or 'graph'
local lock_tag, key_scope_hmac, lock_suffix = scope_key_parts(KEYS[1], key_domain)
local receipt_tag, receipt_scope_hmac, receipt_suffix = scope_key_parts(
  KEYS[2], key_domain)
local receipt_nonce_hmac = receipt_suffix
  and digest_suffix(receipt_suffix, 'lease-result:operation:')
if not lock_tag or lock_suffix ~= 'lock' or receipt_tag ~= lock_tag
    or receipt_scope_hmac ~= key_scope_hmac or not receipt_nonce_hmac
    or request.scope_hmac ~= key_scope_hmac
    or request.request_nonce_hmac ~= receipt_nonce_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.lease-operation-result', {
    schema=true,version=true,input_sha256=true,method=true,domain=true,
    scope_hmac=true,acquisition_id_hmac=true,request_nonce_hmac=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
      or receipt.domain ~= request.domain
      or receipt.scope_hmac ~= key_scope_hmac
      or receipt.acquisition_id_hmac ~= request.acquisition_id_hmac
      or receipt.request_nonce_hmac ~= receipt_nonce_hmac
      or not canonical_positive(receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 ~= ARGV[2] then return {'ERR', 'NONCE_REUSE_CONFLICT'} end
  if receipt.method ~= 'release' then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  return {'OK', 'LEASE_RELEASE_REPLAYED', retained}
end
local current = redis.call('GET', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if not current then return {'ERR', 'LEASE_LOST'} end
if pttl == -1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
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
if not lock_value or lock_value.domain ~= request.domain
    or lock_value.scope_hmac ~= key_scope_hmac
    or lock_value.scope_hmac ~= request.scope_hmac
    or lock_value.acquisition_id_hmac ~= request.acquisition_id_hmac
    or not valid_digest(lock_value.scope_hmac)
    or not valid_digest(lock_value.acquisition_id_hmac)
    or not canonical_positive(lock_value.expires_at_ms)
    or not canonical_positive(lock_value.ttl_ms)
    or not decimal_lte(lock_value.ttl_ms, '300000')
    or not canonical_nonnegative(lock_value.renew_deadline_ms)
    or decimal_subtract(lock_value.expires_at_ms,
      lock_value.renew_deadline_ms) ~= '5000'
    or pttl > tonumber(lock_value.ttl_ms) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if request.domain == 'GRAPH_COMMIT'
    and (not canonical_positive(lock_value.fencing_token)
      or not canonical_nonnegative(lock_value.base_revision)) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if current ~= ARGV[5] then return {'ERR', 'LEASE_LOST'} end
if sha256_hex(ARGV[5]) ~= request.lock_sha256 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local applied_expiry = redis.call('PEXPIRETIME', KEYS[1])
if applied_expiry <= 0 or tostring(applied_expiry) ~= lock_value.expires_at_ms then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
local receipt_expires = server_ms and decimal_add(server_ms, '60000')
if not server_ms or not receipt_expires then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local release = {schema='shajra.lease-release-result',version=1,
  code='LEASE_RELEASED',acquisition_id=ARGV[4],released_at_ms=server_ms}
local result = canonical_json({schema='shajra.lease-operation-result',version=1,
  input_sha256=ARGV[2],method='release',domain=request.domain,
  scope_hmac=request.scope_hmac,acquisition_id_hmac=request.acquisition_id_hmac,
  request_nonce_hmac=request.request_nonce_hmac,result=release,
  receipt_expires_at_ms=receipt_expires})
redis.call('DEL', KEYS[1])
redis.call('SET', KEYS[2], result, 'PXAT', receipt_expires)
return {'OK', 'LEASE_RELEASED', result}
"""
)


LEASE_ASSERT_LUA = (
    "-- shajra:lease-assert:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 1 or #ARGV ~= 3 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local tag, key_scope_hmac, suffix = scope_key_parts(KEYS[1], 'generic')
local domain = 'GENERIC'
if not tag then
  tag, key_scope_hmac, suffix = scope_key_parts(KEYS[1], 'graph')
  domain = 'GRAPH_COMMIT'
end
if not tag or suffix ~= 'lock' or not valid_text(ARGV[1], 512)
    or not valid_text(ARGV[2], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local fields = {schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true}
local schema = 'shajra.generic-lock'
if domain == 'GRAPH_COMMIT' then
  fields.fencing_token = true
  fields.base_revision = true
  schema = 'shajra.graph-lock'
end
local expected = strict_object(ARGV[3], schema, fields)
if not expected or expected.domain ~= domain or expected.scope_hmac ~= key_scope_hmac
    or not valid_digest(expected.scope_hmac)
    or not valid_digest(expected.acquisition_id_hmac)
    or not canonical_positive(expected.expires_at_ms)
    or not canonical_positive(expected.ttl_ms)
    or not canonical_nonnegative(expected.renew_deadline_ms)
    or decimal_subtract(expected.expires_at_ms,
      expected.renew_deadline_ms) ~= '5000'
    or (domain == 'GRAPH_COMMIT' and (not canonical_positive(expected.fencing_token)
      or not canonical_nonnegative(expected.base_revision))) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local current = redis.call('GET', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if not current or current ~= ARGV[3] or pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
return {'OK', 'LEASE_OWNED'}
"""
)


AUTHORIZE_COMMIT_LUA = (
    "-- shajra:commit-authorize:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
local MISSING = '__SHAJRA_MISSING_V1__'
if #KEYS ~= 5 or (#ARGV ~= 3 and #ARGV ~= 8) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local core_tag, key_scope_hmac = graph_core_key_parts(KEYS)
local proposed, proposed_permit = strict_reservation(ARGV[2])
local expected_lock = strict_object(ARGV[3], 'shajra.graph-lock', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,fencing_token=true,base_revision=true,
  expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
if not core_tag or not valid_text(ARGV[1], 512) or not proposed
    or proposed.scope_hmac ~= key_scope_hmac
    or proposed_permit.scope ~= ARGV[1]
    or not expected_lock or expected_lock.domain ~= 'GRAPH_COMMIT'
    or expected_lock.scope_hmac ~= key_scope_hmac
    or not valid_digest(expected_lock.scope_hmac)
    or not valid_digest(expected_lock.acquisition_id_hmac)
    or not canonical_positive(expected_lock.fencing_token)
    or not canonical_nonnegative(expected_lock.base_revision)
    or not canonical_positive(expected_lock.expires_at_ms)
    or not canonical_positive(expected_lock.ttl_ms)
    or not canonical_nonnegative(expected_lock.renew_deadline_ms)
    or decimal_subtract(expected_lock.expires_at_ms,
      expected_lock.renew_deadline_ms) ~= '5000' then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[4])
if retained then
  local reservation = strict_reservation(retained)
  if not reservation then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if retained == ARGV[2] then return {'OK', 'RESERVATION_REPLAYED', retained} end
  return {'ERR', 'RESERVATION_CONFLICT'}
end
local current = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
local current_pttl = -2
if current[1] then current_pttl = redis.call('PTTL', KEYS[1]) end
if #ARGV == 3 then
  return {'OK', 'AUTHORIZATION_PREFLIGHT', current[3] or '', current[2] or '',
    current[1] or '', tostring(current_pttl), current[4] or '', current[5] or ''}
end
for index = 1, 5 do
  local expected = ARGV[3 + index]
  if expected == MISSING then
    if current[index] then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  elseif not current[index] or current[index] ~= expected then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
end
local confirmed = current[3]
local fence = current[2]
if not confirmed then return {'ERR', 'COORDINATION_UNINITIALIZED'} end
if not fence or not canonical_nonnegative(confirmed) or not canonical_positive(fence) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local proof_raw = current[5]
local proof = decode_object(proof_raw or '')
if not proof or type(proof.schema) ~= 'string' then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if proof.schema == 'shajra.confirmed-commit-receipt' then
  proof = strict_object(proof_raw, 'shajra.confirmed-commit-receipt', {
    schema=true,version=true,scope_hmac=true,permit=true,commit_json=true,
    commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true})
  if not proof or not proof.permit or proof.permit.revision ~= confirmed then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
elseif proof.schema == 'shajra.reconciled-head-receipt' then
  proof = strict_object(proof_raw, 'shajra.reconciled-head-receipt', {
    schema=true,version=true,scope_hmac=true,revision=true,
    semantic_checksum=true,head_commit_sha256=true,evidence_sha256=true,
    admin_request_nonce_hmac=true,proof_sha256=true})
  if not proof or proof.revision ~= confirmed then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
else
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if proof.scope_hmac ~= key_scope_hmac or not valid_digest(proof.scope_hmac) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lock = current[1]
local pttl = current_pttl
if not lock or pttl <= 0 then return {'ERR', 'LEASE_LOST'} end
local lock_value = strict_object(lock, 'shajra.graph-lock', {
  schema=true,version=true,domain=true,scope_hmac=true,
  acquisition_id_hmac=true,fencing_token=true,base_revision=true,
  expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
if not lock_value or lock_value.domain ~= 'GRAPH_COMMIT'
    or lock_value.scope_hmac ~= key_scope_hmac
    or not valid_digest(lock_value.scope_hmac)
    or not valid_digest(lock_value.acquisition_id_hmac)
    or not canonical_positive(lock_value.fencing_token)
    or not canonical_nonnegative(lock_value.base_revision)
    or not canonical_positive(lock_value.expires_at_ms)
    or not canonical_positive(lock_value.ttl_ms)
    or not canonical_nonnegative(lock_value.renew_deadline_ms)
    or decimal_subtract(lock_value.expires_at_ms,
      lock_value.renew_deadline_ms) ~= '5000'
    or pttl > tonumber(lock_value.ttl_ms) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if lock ~= ARGV[3] then return {'ERR', 'LEASE_LOST'} end
if not proposed
    or lock_value.base_revision ~= confirmed
    or lock_value.fencing_token ~= fence
    or proposed.scope_hmac ~= lock_value.scope_hmac
    or proposed_permit.fencing_token ~= lock_value.fencing_token then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if canonical_increment(confirmed) ~= proposed_permit.revision then
  return {'ERR', 'RESERVATION_CONFLICT'}
end
redis.call('SET', KEYS[4], ARGV[2])
return {'OK', 'RESERVATION_CREATED', ARGV[2]}
"""
)


COORDINATION_STATUS_LUA = (
    "-- shajra:coordination-status:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 5 or #ARGV ~= 1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if not graph_core_key_parts(KEYS) or not valid_text(ARGV[1], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local values = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
local pttl = -2
if values[1] then pttl = redis.call('PTTL', KEYS[1]) end
return {'OK', 'STATUS', values[3] or '', values[2] or '', values[1] or '',
  tostring(pttl), values[4] or '', values[5] or ''}
"""
)


CONFIRM_COMMIT_LUA = (
    "-- shajra:commit-confirm:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
local MISSING = '__SHAJRA_MISSING_V1__'
if #KEYS ~= 5 or #ARGV ~= 14 or not canonical_positive(ARGV[3])
    or not canonical_positive(ARGV[4]) or not canonical_nonnegative(ARGV[9]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local core_tag, key_scope_hmac = graph_core_key_parts(KEYS)
local requested_commit, requested_revision, requested_fence = strict_graph_commit(
  ARGV[7])
if not core_tag or not valid_text(ARGV[1], 512)
    or not valid_text(ARGV[2], 512) or not valid_text(ARGV[5], 512)
    or not valid_digest(ARGV[6]) or not valid_digest(ARGV[8])
    or not requested_commit or requested_commit.operation_id ~= ARGV[2]
    or requested_revision ~= ARGV[3] or requested_fence ~= ARGV[4]
    or requested_commit.permit_id ~= ARGV[5] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local proof_raw = redis.call('GET', KEYS[5])
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
  if not proof or proof.scope_hmac ~= key_scope_hmac
      or not valid_digest(proof.scope_hmac) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if proof.schema == 'shajra.confirmed-commit-receipt' and proof.permit
      and proof.permit.operation_id == ARGV[2]
      and proof.permit.revision == ARGV[3]
      and proof.permit.fencing_token == ARGV[4]
      and proof.permit.permit_id == ARGV[5]
      and proof.commit_sha256 == ARGV[6] then
    return {'OK', 'CONFIRMATION_REPLAYED', proof_raw}
  end
end
local current = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
for index = 1, 5 do
  local expected = ARGV[9 + index]
  if expected == MISSING then
    if current[index] then return {'ERR', 'CONFIRMATION_CONFLICT'} end
  elseif not current[index] or current[index] ~= expected then
    return {'ERR', 'CONFIRMATION_CONFLICT'}
  end
end
local confirmed = current[3]
if not confirmed or not canonical_nonnegative(confirmed) then
  return {'ERR', 'COORDINATION_UNINITIALIZED'}
end
local fence = current[2]
if not fence or not canonical_positive(fence) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local core_proof_raw = current[5]
local core_proof = decode_object(core_proof_raw or '')
if not core_proof or type(core_proof.schema) ~= 'string' then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if core_proof.schema == 'shajra.confirmed-commit-receipt' then
  core_proof = strict_object(core_proof_raw, 'shajra.confirmed-commit-receipt', {
    schema=true,version=true,scope_hmac=true,permit=true,commit_json=true,
    commit_sha256=true,staged_write_receipt_json=true,
    staged_write_receipt_sha256=true})
  if not core_proof or not core_proof.permit
      or core_proof.permit.revision ~= confirmed
      or not canonical_positive(core_proof.permit.fencing_token)
      or decimal_compare(core_proof.permit.fencing_token, fence) > 0 then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
elseif core_proof.schema == 'shajra.reconciled-head-receipt' then
  core_proof = strict_object(core_proof_raw, 'shajra.reconciled-head-receipt', {
    schema=true,version=true,scope_hmac=true,revision=true,
    semantic_checksum=true,head_commit_sha256=true,evidence_sha256=true,
    admin_request_nonce_hmac=true,proof_sha256=true})
  if not core_proof or core_proof.revision ~= confirmed then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
else
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if core_proof.scope_hmac ~= key_scope_hmac
    or not valid_digest(core_proof.scope_hmac) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local lock_raw = current[1]
if lock_raw then
  local lock = strict_object(lock_raw, 'shajra.graph-lock', {
    schema=true,version=true,domain=true,scope_hmac=true,
    acquisition_id_hmac=true,fencing_token=true,base_revision=true,
    expires_at_ms=true,ttl_ms=true,renew_deadline_ms=true})
  local lock_pttl = redis.call('PTTL', KEYS[1])
  if not lock or lock.domain ~= 'GRAPH_COMMIT' or lock.scope_hmac ~= key_scope_hmac
      or not valid_digest(lock.acquisition_id_hmac)
      or lock.fencing_token ~= fence or lock.base_revision ~= confirmed
      or not canonical_positive(lock.ttl_ms) or lock_pttl <= 0
      or lock_pttl > tonumber(lock.ttl_ms)
      or decimal_subtract(lock.expires_at_ms,
        lock.renew_deadline_ms) ~= '5000' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
end
if decimal_lte(ARGV[3], confirmed) then
  return {'ERR', 'CONFIRMATION_PROOF_EVICTED', confirmed}
end
if canonical_increment(confirmed) ~= ARGV[3] or confirmed ~= ARGV[9] then
  return {'ERR', 'CONFIRMATION_CONFLICT'}
end
local reservation_raw = current[4]
if not reservation_raw then return {'ERR', 'CONFIRMATION_CONFLICT'} end
local reservation, reservation_permit = strict_reservation(reservation_raw)
if not reservation or reservation.scope_hmac ~= key_scope_hmac then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if decimal_compare(reservation_permit.fencing_token, fence) > 0 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if reservation_permit.operation_id ~= ARGV[2]
    or reservation_permit.revision ~= ARGV[3]
    or reservation_permit.fencing_token ~= ARGV[4]
    or reservation_permit.permit_id ~= ARGV[5]
    or reservation_permit.commit_sha256 ~= ARGV[6]
    or reservation.commit_sha256 ~= ARGV[6]
    or reservation.commit_json ~= ARGV[7] then
  return {'ERR', 'CONFIRMATION_CONFLICT'}
end
local proof = canonical_json({schema='shajra.confirmed-commit-receipt',version=1,
  scope_hmac=reservation.scope_hmac,permit=reservation.permit,
  commit_json=reservation.commit_json,commit_sha256=reservation.commit_sha256,
  staged_write_receipt_json=reservation.staged_write_receipt_json,
  staged_write_receipt_sha256=reservation.staged_write_receipt_sha256})
redis.call('SET', KEYS[3], ARGV[3])
redis.call('SET', KEYS[5], proof)
redis.call('DEL', KEYS[4])
return {'OK', 'CONFIRMED', proof}
"""
)


COORDINATION_INSPECT_LUA = (
    "-- shajra:coordination-inspect:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 5 or #ARGV ~= 1 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if not graph_core_key_parts(KEYS) or not valid_text(ARGV[1], 512) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local values = redis.call('MGET', KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5])
return {'OK', 'INSPECTION', values[1] or '', values[2] or '', values[3] or '',
  values[4] or '', values[5] or ''}
"""
)


COORDINATION_ADMIN_LUA = (
    "-- shajra:coordination-admin:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
local MISSING = '__SHAJRA_MISSING_V1__'
if #KEYS ~= 6 or #ARGV ~= 17 or not canonical_nonnegative(ARGV[9])
    or not canonical_positive(ARGV[10]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local core_tag, key_scope_hmac = graph_core_key_parts(KEYS)
local receipt_tag, receipt_scope_hmac, receipt_suffix = scope_key_parts(
  KEYS[6], 'graph')
local receipt_nonce_hmac = receipt_suffix
  and digest_suffix(receipt_suffix, 'admin-result:')
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
if not core_tag or receipt_tag ~= core_tag
    or receipt_scope_hmac ~= key_scope_hmac or not receipt_nonce_hmac
    or not valid_digest(ARGV[2]) or not valid_digest(ARGV[4])
    or not valid_digest(ARGV[8]) or not valid_digest(ARGV[17])
    or not valid_text(ARGV[5], 512) or not valid_text(ARGV[6], 512)
    or (ARGV[11] ~= 'initialize' and ARGV[11] ~= 'reconcile')
    or not request or request.method ~= ARGV[11]
    or request.scope_hmac ~= key_scope_hmac
    or request.evidence_sha256 ~= (evidence and evidence.evidence_sha256)
    or request.expected_state_sha256 ~= ARGV[4]
    or not valid_digest(request.scope_hmac)
    or not valid_digest(request.evidence_sha256)
    or not valid_digest(request.expected_state_sha256)
    or not evidence or evidence.scope ~= ARGV[5]
    or evidence.committed_head_revision ~= ARGV[9]
    or evidence.fencing_floor ~= ARGV[10]
    or not canonical_nonnegative(evidence.committed_head_revision)
    or not canonical_nonnegative(evidence.max_durable_fencing_token)
    or not canonical_positive(evidence.fencing_floor)
    or decimal_compare(evidence.max_durable_fencing_token,
      evidence.fencing_floor) >= 0
    or not valid_digest(evidence.committed_head_semantic_checksum)
    or not valid_digest(evidence.evidence_sha256)
    or (evidence.committed_head_commit_sha256 ~= cjson.null
      and not valid_digest(evidence.committed_head_commit_sha256))
    or (evidence.committed_head_revision == '0'
      and evidence.committed_head_commit_sha256 ~= cjson.null)
    or (evidence.committed_head_revision ~= '0'
      and evidence.committed_head_commit_sha256 == cjson.null)
    or not proof or proof.scope_hmac ~= key_scope_hmac
    or proof.revision ~= ARGV[9]
    or proof.semantic_checksum ~= evidence.committed_head_semantic_checksum
    or proof.head_commit_sha256 ~= evidence.committed_head_commit_sha256
    or proof.evidence_sha256 ~= evidence.evidence_sha256
    or proof.admin_request_nonce_hmac ~= receipt_nonce_hmac
    or not valid_digest(proof.scope_hmac)
    or not valid_digest(proof.semantic_checksum)
    or not valid_digest(proof.evidence_sha256)
    or not valid_digest(proof.admin_request_nonce_hmac)
    or not valid_digest(proof.proof_sha256) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[6])
if retained then
  local receipt = strict_object(retained, 'shajra.coordination-admin-result', {
    schema=true,version=true,input_sha256=true,method=true,scope_hmac=true,
    request_nonce_hmac=true,evidence_sha256=true,expected_state_sha256=true,
    result=true,receipt_expires_at_ms=true})
  if not receipt or not receipt.result
      or not valid_digest(receipt.input_sha256)
      or receipt.scope_hmac ~= key_scope_hmac
      or receipt.request_nonce_hmac ~= receipt_nonce_hmac
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
local server_ms = redis_time_ms(clock)
local receipt_expires = server_ms and decimal_add(server_ms, '60000')
if not server_ms or not receipt_expires then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local transition = {schema='shajra.coordination-admin-transition',version=1,
  code=code,previous_state_sha256=ARGV[4],state_sha256=ARGV[8],
  confirmed_revision=ARGV[9],fencing_floor=ARGV[10]}
local result = canonical_json({schema='shajra.coordination-admin-result',version=1,
  input_sha256=ARGV[2],method=ARGV[11],scope_hmac=request.scope_hmac,
  request_nonce_hmac=proof.admin_request_nonce_hmac,
  evidence_sha256=evidence.evidence_sha256,expected_state_sha256=ARGV[4],
  result=transition,receipt_expires_at_ms=receipt_expires})
redis.call('SET', KEYS[2], ARGV[10])
redis.call('SET', KEYS[3], ARGV[9])
redis.call('SET', KEYS[5], ARGV[7])
redis.call('SET', KEYS[6], result, 'PXAT', receipt_expires)
return {'OK', code, result}
"""
)


REVOCATION_REVOKE_LUA = (
    "-- shajra:revocation-revoke:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 6 or not canonical_nonnegative(ARGV[4])
    or not canonical_nonnegative(ARGV[5]) or not decimal_lte(ARGV[5], '300') then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local token_expiry_ms = decimal_times_1000(ARGV[4])
local leeway_ms = decimal_times_1000(ARGV[5])
local expires = token_expiry_ms and leeway_ms
  and decimal_add(token_expiry_ms, leeway_ms)
if not expires then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local entry_tag, entry_suffix = global_key_parts(KEYS[1], 'revocation')
local receipt_tag, receipt_suffix = global_key_parts(KEYS[2], 'revocation')
local entry_jti_hmac = entry_suffix and digest_suffix(entry_suffix, 'entry:')
local receipt_nonce_hmac = receipt_suffix and digest_suffix(receipt_suffix, 'nonce:')
local request = strict_object(ARGV[1], 'shajra.revocation-request', {
  schema=true,version=true,jti_hmac=true,token_expires_at_s=true,leeway_s=true})
local entry_proposed = strict_object(ARGV[6], 'shajra.revocation-entry', {
  schema=true,version=true,jti_hmac=true,expires_at_ms=true,entry_sha256=true})
if not entry_tag or receipt_tag ~= entry_tag or not entry_jti_hmac
    or not receipt_nonce_hmac or not valid_digest(ARGV[2])
    or not valid_text(ARGV[3], 4096) or not request or not entry_proposed
    or request.jti_hmac ~= entry_jti_hmac
    or request.token_expires_at_s ~= ARGV[4]
    or request.leeway_s ~= ARGV[5]
    or not valid_digest(request.jti_hmac)
    or entry_proposed.jti_hmac ~= request.jti_hmac
    or entry_proposed.expires_at_ms ~= expires
    or not valid_digest(entry_proposed.entry_sha256) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.revocation-result', {
    schema=true,version=true,input_sha256=true,jti_hmac=true,
    token_expires_at_s=true,leeway_s=true,code=true,revoked=true,
    server_time_ms=true,expires_at_ms=true,receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
      or not canonical_nonnegative(receipt.token_expires_at_s)
      or not canonical_nonnegative(receipt.leeway_s)
      or not canonical_nonnegative(receipt.server_time_ms)
      or not canonical_nonnegative(receipt.expires_at_ms)
      or not canonical_nonnegative(receipt.receipt_expires_at_ms)
      or type(receipt.revoked) ~= 'boolean' then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if receipt.input_sha256 ~= ARGV[2] then return {'ERR', 'NONCE_REUSE_CONFLICT'} end
  local revoked_code = receipt.code == 'REVOKED' or receipt.code == 'ALREADY_REVOKED'
  if not request or receipt.jti_hmac ~= request.jti_hmac
      or receipt.token_expires_at_s ~= request.token_expires_at_s
      or receipt.leeway_s ~= request.leeway_s
      or receipt.revoked ~= revoked_code
      or (not revoked_code and receipt.code ~= 'TOKEN_ALREADY_EXPIRED') then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local retained_server_floor = decimal_add(receipt.server_time_ms, '60000')
  if not retained_server_floor then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  local expected_receipt_expires = decimal_compare(expires, retained_server_floor) >= 0
    and expires or retained_server_floor
  if receipt.expires_at_ms ~= expires
      or receipt.receipt_expires_at_ms ~= expected_receipt_expires then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if not ensure_exact_expiry(KEYS[2], receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'OK', receipt.code, retained}
end
if not request or not entry_proposed or request.token_expires_at_s ~= ARGV[4]
    or request.leeway_s ~= ARGV[5] or entry_proposed.jti_hmac ~= request.jti_hmac
    or entry_proposed.expires_at_ms ~= expires
    or not valid_digest(entry_proposed.entry_sha256) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
if not server_ms then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local existing = redis.call('GET', KEYS[1])
local code = 'REVOKED'
local revoked = true
if decimal_compare(server_ms, expires) >= 0 then
  code = 'TOKEN_ALREADY_EXPIRED'
  revoked = false
elseif existing then
  if existing ~= ARGV[6] then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if not ensure_exact_expiry(KEYS[1], expires) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  code = 'ALREADY_REVOKED'
end
local server_receipt_floor = decimal_add(server_ms, '60000')
if not server_receipt_floor then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local receipt_expires = decimal_compare(expires, server_receipt_floor) >= 0
  and expires or server_receipt_floor
local result = canonical_json({schema='shajra.revocation-result',version=1,
  input_sha256=ARGV[2],jti_hmac=request.jti_hmac,token_expires_at_s=ARGV[4],
  leeway_s=ARGV[5],code=code,revoked=revoked,server_time_ms=server_ms,
  expires_at_ms=expires,receipt_expires_at_ms=receipt_expires})
if code == 'REVOKED' then
  redis.call('SET', KEYS[1], ARGV[6], 'PXAT', expires)
end
redis.call('SET', KEYS[2], result, 'PXAT', receipt_expires)
return {'OK', code, result}
"""
)


REVOCATION_CHECK_LUA = (
    "-- shajra:revocation-check:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 1 or #ARGV ~= 4 or not canonical_nonnegative(ARGV[2])
    or not canonical_nonnegative(ARGV[3]) or not decimal_lte(ARGV[3], '300') then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local token_expiry_ms = decimal_times_1000(ARGV[2])
local leeway_ms = decimal_times_1000(ARGV[3])
local expires = token_expiry_ms and leeway_ms
  and decimal_add(token_expiry_ms, leeway_ms)
if not expires then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local entry_tag, entry_suffix = global_key_parts(KEYS[1], 'revocation')
local entry_jti_hmac = entry_suffix and digest_suffix(entry_suffix, 'entry:')
local expected_entry = strict_object(ARGV[1], 'shajra.revocation-entry', {
  schema=true,version=true,jti_hmac=true,expires_at_ms=true,entry_sha256=true})
if not entry_tag or not entry_jti_hmac or not valid_digest(ARGV[4])
    or entry_jti_hmac ~= ARGV[4] or not expected_entry
    or expected_entry.jti_hmac ~= ARGV[4]
    or expected_entry.expires_at_ms ~= expires
    or not valid_digest(expected_entry.entry_sha256) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
if not server_ms then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
if decimal_compare(server_ms, expires) >= 0 then
  return {'OK', 'TOKEN_ALREADY_EXPIRED', 'false', server_ms, expires}
end
local raw = redis.call('GET', KEYS[1])
if not raw then return {'OK', 'NOT_REVOKED', 'false', server_ms, expires} end
if raw ~= ARGV[1] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
if not ensure_exact_expiry(KEYS[1], expires) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
return {'OK', 'REVOKED', 'true', server_ms, expires}
"""
)


RATE_TIME_LUA = (
    "-- shajra:rate-time:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 0 or #ARGV ~= 0 then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
if not server_ms then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
return {'OK', 'TIME', server_ms}
"""
)


RATE_CONSUME_LUA = (
    "-- shajra:rate-consume:v1\n"
    + _LUA_DECIMAL_VALIDATION
    + r"""
if #KEYS ~= 2 or #ARGV ~= 7 or not canonical_nonnegative(ARGV[5])
    or not canonical_positive(ARGV[6]) or not canonical_positive(ARGV[7]) then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local request = strict_object(ARGV[1], 'shajra.rate-request', {
  schema=true,version=true,policy_id=true,subject_kind=true,subject_hmac=true,
  window_start_ms=true,window_ms=true,limit=true})
local counter_tag, counter_suffix = global_key_parts(KEYS[1], 'rate')
local receipt_tag, receipt_suffix = global_key_parts(KEYS[2], 'rate')
local receipt_nonce_hmac = receipt_suffix and digest_suffix(receipt_suffix, 'nonce:')
local valid_policy = ARGV[3] == 'login' or ARGV[3] == 'submit'
  or ARGV[3] == 'upload' or ARGV[3] == 'comment' or ARGV[3] == 'story'
  or ARGV[3] == 'search' or ARGV[3] == 'email-verification'
local expected_counter_suffix = request and ('counter:' .. ARGV[3] .. ':'
  .. request.subject_hmac .. ':' .. ARGV[5]) or ''
if not counter_tag or receipt_tag ~= counter_tag or not receipt_nonce_hmac
    or counter_suffix ~= expected_counter_suffix or not valid_policy
    or (ARGV[4] ~= 'IP' and ARGV[4] ~= 'IDENTITY')
    or not valid_digest(ARGV[2]) or not request
    or request.policy_id ~= ARGV[3] or request.subject_kind ~= ARGV[4]
    or request.window_start_ms ~= ARGV[5] or request.window_ms ~= ARGV[6]
    or request.limit ~= ARGV[7] or not valid_digest(request.subject_hmac)
    or not decimal_lte(ARGV[6], '86400000') then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local retained = redis.call('GET', KEYS[2])
if retained then
  local receipt = strict_object(retained, 'shajra.rate-result', {
    schema=true,version=true,input_sha256=true,policy_id=true,subject_kind=true,
    subject_hmac=true,window_start_ms=true,window_ms=true,limit=true,allowed=true,
    observed_count=true,remaining=true,server_time_ms=true,reset_at_ms=true,
    retry_after_ms=true,receipt_expires_at_ms=true})
  if not receipt or not valid_digest(receipt.input_sha256)
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
  if not request or receipt.policy_id ~= request.policy_id
      or receipt.subject_kind ~= request.subject_kind
      or receipt.subject_hmac ~= request.subject_hmac
      or receipt.window_start_ms ~= request.window_start_ms
      or receipt.window_ms ~= request.window_ms or receipt.limit ~= request.limit then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  local reset = decimal_add(receipt.window_start_ms, receipt.window_ms)
  local receipt_expires = reset and decimal_add(reset, '60000')
  local comparison = decimal_compare(receipt.observed_count, receipt.limit)
  local expected_allowed = comparison <= 0
  local expected_remaining = expected_allowed
    and decimal_subtract(receipt.limit, receipt.observed_count) or '0'
  local expected_retry = (not expected_allowed and reset)
    and decimal_subtract(reset, receipt.server_time_ms) or '0'
  if not reset or not receipt_expires or not expected_remaining or not expected_retry
      or receipt.reset_at_ms ~= reset
      or receipt.receipt_expires_at_ms ~= receipt_expires
      or receipt.remaining ~= expected_remaining
      or receipt.retry_after_ms ~= expected_retry
      or decimal_compare(receipt.server_time_ms, receipt.window_start_ms) < 0
      or decimal_compare(receipt.server_time_ms, reset) >= 0
      or receipt.allowed ~= expected_allowed then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  if not ensure_exact_expiry(KEYS[2], receipt.receipt_expires_at_ms) then
    return {'ERR', 'COORDINATION_STATE_CORRUPT'}
  end
  return {'OK', (receipt.allowed and 'RATE_LIMIT_ALLOWED' or 'RATE_LIMIT_DENIED'), retained}
end
if not request or request.policy_id ~= ARGV[3] or request.subject_kind ~= ARGV[4]
    or request.window_start_ms ~= ARGV[5] or request.window_ms ~= ARGV[6]
    or request.limit ~= ARGV[7] then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local window = tonumber(ARGV[6])
if not window or window > 86400000
    or decimal_mod_small(ARGV[5], window) ~= 0 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local clock = redis.call('TIME')
local server_ms = redis_time_ms(clock)
local reset = decimal_add(ARGV[5], ARGV[6])
if not server_ms or not reset
    or decimal_compare(server_ms, ARGV[5]) < 0
    or decimal_compare(server_ms, reset) >= 0 then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local raw_count = redis.call('GET', KEYS[1])
local count = '0'
if raw_count then
  if not canonical_nonnegative(raw_count) then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  count = raw_count
end
if count == I64_MAX then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local counter_ttl = redis.call('PTTL', KEYS[1])
if raw_count then
  local remaining_text = decimal_subtract(reset, server_ms)
  local remaining = remaining_text and tonumber(remaining_text)
  if not remaining then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
  if counter_ttl > remaining then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
end
count = canonical_increment(count)
if not count then return {'ERR', 'COORDINATION_STATE_CORRUPT'} end
local allowed = decimal_compare(count, ARGV[7]) <= 0
local remaining = allowed and decimal_subtract(ARGV[7], count) or '0'
local retry_after = allowed and '0' or decimal_subtract(reset, server_ms)
local receipt_expires = decimal_add(reset, '60000')
if not remaining or not retry_after or not receipt_expires then
  return {'ERR', 'COORDINATION_STATE_CORRUPT'}
end
local result = canonical_json({schema='shajra.rate-result',version=1,
  input_sha256=ARGV[2],policy_id=ARGV[3],subject_kind=ARGV[4],
  subject_hmac=request.subject_hmac,window_start_ms=ARGV[5],window_ms=ARGV[6],
  limit=ARGV[7],allowed=allowed,observed_count=count,remaining=remaining,
  server_time_ms=server_ms,reset_at_ms=reset,
  retry_after_ms=retry_after,receipt_expires_at_ms=receipt_expires})
redis.call('SET', KEYS[1], count, 'PXAT', reset)
redis.call('SET', KEYS[2], result, 'PXAT', receipt_expires)
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


@dataclass(frozen=True, slots=True)
class _RawGraphCore:
    values: tuple[str | None, str | None, str | None, str | None, str | None]
    lock_pttl: str


class UpstashCommitCoordinator:
    def __init__(self, redis: EvalAdapter, keys: RedisKeyBuilder) -> None:
        self._redis = redis
        self._keys = keys

    def _read_core(self, scope: str) -> _RawGraphCore:
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
        return _RawGraphCore(
            (
                lock_raw or None,
                fence_raw or None,
                confirmed_raw or None,
                reservation_raw or None,
                proof_raw or None,
            ),
            pttl_raw,
        )

    def _decode_core(
        self, scope: str, snapshot: _RawGraphCore
    ) -> CommitCoordinatorStatus:
        lock_raw, fence_raw, confirmed_raw, reservation_raw, proof_raw = snapshot.values
        if confirmed_raw is None and fence_raw is None:
            if (
                lock_raw is not None
                or reservation_raw is not None
                or proof_raw is not None
                or snapshot.lock_pttl != "-2"
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            raise CoordinationError("COORDINATION_UNINITIALIZED")
        if confirmed_raw is None or fence_raw is None or proof_raw is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        confirmed = parse_canonical_decimal(confirmed_raw, minimum=0)
        fence = parse_canonical_decimal(fence_raw, minimum=1)
        if lock_raw is not None:
            pttl = parse_canonical_decimal(snapshot.lock_pttl, minimum=1)
            lock = inspect_graph_lock(lock_raw, self._keys, scope)
            if (
                pttl > lock.ttl_ms
                or lock.fencing_token != fence
                or lock.base_revision != confirmed
            ):
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
        elif snapshot.lock_pttl != "-2":
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
            if reservation_raw is not None
            else None
        )
        if reservation is not None and (
            reservation.commit.revision != confirmed + 1
            or reservation.commit.fencing_token > fence
        ):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        state_digest = coordination_state_sha256(snapshot.values)
        return CommitCoordinatorStatus(
            scope,
            "COMMITTING" if reservation is not None else "READY",
            confirmed,
            fence,
            reservation,
            proof,
            state_digest,
        )

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
        script_keys = [
            self._keys.graph_lock(scope),
            self._keys.graph_fence(scope),
            self._keys.graph_confirmed_revision(scope),
            self._keys.graph_reservation(scope),
            self._keys.graph_last_confirmation(scope),
            self._keys.graph_acquisition_result(scope, acquisition_id),
        ]
        base_args = [request.text, request.sha256, scope, acquisition_id, str(ttl_ms)]
        result = self._redis.eval(
            GRAPH_ACQUIRE_LUA,
            script_keys,
            base_args,
            nonce_idempotent=False,
        )
        code, payload = _tagged(result, {"GRAPH_PREFLIGHT", "LEASE_REPLAYED"})
        if code == "LEASE_REPLAYED":
            receipt = deserialize_lease_acquisition_receipt(
                _one_text_payload(payload), request, self._keys, scope, acquisition_id
            )
            if type(receipt.lease) is not GraphLease:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            return receipt.lease
        if len(payload) != 6 or not all(isinstance(value, str) for value in payload):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        confirmed_raw, fence_raw, lock_raw, pttl_raw, reservation_raw, proof_raw = (
            payload
        )
        snapshot = _RawGraphCore(
            (
                lock_raw or None,
                fence_raw or None,
                confirmed_raw or None,
                reservation_raw or None,
                proof_raw or None,
            ),
            pttl_raw,
        )
        status = self._decode_core(scope, snapshot)
        if status.active_reservation is not None:
            raise CoordinationError("COMMIT_RECOVERY_REQUIRED")
        if snapshot.values[0] is not None:
            raise CoordinationError("LOCK_UNAVAILABLE")
        if status.confirmed_revision != committed_revision:
            raise CoordinationError("COORDINATION_REVISION_MISMATCH")
        result = self._redis.eval(
            GRAPH_ACQUIRE_LUA,
            script_keys,
            [
                *base_args,
                *(
                    value if value is not None else "__SHAJRA_MISSING_V1__"
                    for value in snapshot.values
                ),
            ],
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
        script_keys = [
            self._keys.graph_lock(lease.scope),
            self._keys.graph_fence(lease.scope),
            self._keys.graph_confirmed_revision(lease.scope),
            self._keys.graph_reservation(lease.scope),
            self._keys.graph_last_confirmation(lease.scope),
        ]
        base_args = [
            lease.scope,
            reservation_raw,
            serialize_graph_lock(lease, self._keys),
        ]
        result = self._redis.eval(
            AUTHORIZE_COMMIT_LUA,
            script_keys,
            base_args,
            nonce_idempotent=False,
        )
        code, payload = _tagged(
            result, {"AUTHORIZATION_PREFLIGHT", "RESERVATION_REPLAYED"}
        )
        if code == "RESERVATION_REPLAYED":
            persisted = deserialize_commit_reservation(
                _one_text_payload(payload), self._keys, lease.scope
            )
            if persisted != reservation:
                raise CoordinationError("RESERVATION_CONFLICT")
            return persisted.permit
        if len(payload) != 6 or not all(isinstance(value, str) for value in payload):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        confirmed_raw, fence_raw, lock_raw, pttl_raw, current_reservation, proof_raw = (
            payload
        )
        snapshot = _RawGraphCore(
            (
                lock_raw or None,
                fence_raw or None,
                confirmed_raw or None,
                current_reservation or None,
                proof_raw or None,
            ),
            pttl_raw,
        )
        status = self._decode_core(lease.scope, snapshot)
        if status.active_reservation is not None:
            raise CoordinationError("RESERVATION_CONFLICT")
        if snapshot.values[0] != base_args[2]:
            raise CoordinationError("LEASE_LOST")
        result = self._redis.eval(
            AUTHORIZE_COMMIT_LUA,
            script_keys,
            [
                *base_args,
                *(
                    value if value is not None else "__SHAJRA_MISSING_V1__"
                    for value in snapshot.values
                ),
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
        return self._decode_core(scope, self._read_core(scope))

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
        snapshot = self._read_core(permit.scope)
        proof_raw = snapshot.values[4]
        if proof_raw is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        try:
            prior_proof = deserialize_confirmed_commit_receipt(
                proof_raw, self._keys, permit.scope
            )
        except CoordinationError:
            deserialize_reconciled_head_receipt(proof_raw, self._keys, permit.scope)
        else:
            if prior_proof.permit == permit and prior_proof.commit == commit:
                return ConfirmationResult(
                    "CONFIRMATION_REPLAYED", permit, commit.revision
                )
        confirmed_raw = snapshot.values[2]
        reservation_raw = snapshot.values[3]
        if confirmed_raw is None:
            raise CoordinationError("CONFIRMATION_CONFLICT")
        confirmed_revision = parse_canonical_decimal(confirmed_raw, minimum=0)
        if permit.revision <= confirmed_revision:
            return ConfirmationResult(
                "CONFIRMATION_PROOF_EVICTED", permit, confirmed_revision
            )
        if reservation_raw is None:
            raise CoordinationError("CONFIRMATION_CONFLICT")
        preflight_reservation = deserialize_commit_reservation(
            reservation_raw, self._keys, permit.scope
        )
        if preflight_reservation.commit.revision != confirmed_revision + 1:
            raise CoordinationError("CONFIRMATION_CONFLICT")
        status = self._decode_core(permit.scope, snapshot)
        if (
            status.active_reservation is None
            or status.active_reservation.permit != permit
            or status.active_reservation.commit != commit
            or status.confirmed_revision != commit.revision - 1
        ):
            raise CoordinationError("CONFIRMATION_CONFLICT")
        result = self._redis.eval(
            CONFIRM_COMMIT_LUA,
            [
                self._keys.graph_lock(permit.scope),
                self._keys.graph_fence(permit.scope),
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
                *(
                    value if value is not None else "__SHAJRA_MISSING_V1__"
                    for value in snapshot.values
                ),
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
            or reservation.commit.fencing_token > fence
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
