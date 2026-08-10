# Task 4 Report: Upstash Commit Coordination, Revocation, and Rate Limits

## Status

`STATUS: DONE`

Implemented locally on branch `codex/shajra-reliability` from exact base
`85ef909072b79c1713112b6cbee3ade65517d249` and committed as
`086b575fc39ab044d91e80ce09732f263add75a1`
(`feat: coordinate Shajra serverless mutations`). No push, merge, deployment,
production, Vercel, Airtable, Upstash, or other network service action was
performed.

## Changed Files

- `.env.example`
- `backend/config.py`
- `backend/requirements.txt`
- `backend/coordination/__init__.py`
- `backend/coordination/protocols.py`
- `backend/coordination/serialization.py`
- `backend/coordination/sdk.py`
- `backend/coordination/upstash.py`
- `backend/tests/test_config.py`
- `backend/tests/unit/coordination/test_serialization.py`
- `backend/tests/unit/coordination/test_sdk.py`
- `backend/tests/unit/coordination/test_upstash.py`

Task 1/3 repository contracts were consumed without modification. No
`backend/.env.example` was created, and no planning or specification document
was edited.

## Implementation Summary

- Added the seven exact dependency pins, preview/production Upstash settings,
  secret-typed Redis credentials, strict deployment namespace validation, and
  the shared `JWT_LEEWAY_SECONDS=30` setting with range `0..300`.
- Added frozen, slotted generic lease, graph lease, commit coordinator, admin,
  revocation, and typed rate-limit protocols and result types.
- Added compact sorted-key ASCII JSON, duplicate-key rejection, exact schemas,
  canonical signed-64 decimal handling, digest recomputation, canonical byte
  comparison, and fail-closed decoding for every Task 4 envelope.
- Added one versioned HMAC Redis key builder with scope-local graph/generic hash
  tags and fixed deployment-wide revocation/rate hash tags.
- Added the Upstash Redis 1.7.0 `eval(script, keys, args)` adapter with
  `rest_retries=0` and one retry only for a nonce-idempotent ambiguous
  `httpx.TransportError`, preserving byte-identical inputs.
- Added generic and graph lease acquisition, renewal, assertion, and release;
  graph authorization, status, confirmation, operator initialization and
  reconciliation; fail-closed revocation; and fixed-window rate limiting.
- Added canonical nonce result receipts, receipt-first replay/conflict handling,
  Redis `TIME`/`PTTL` timing, fencing, exact-state CAS, revocation retention and
  TTL repair, and rate counter/receipt TTL repair.

## TDD Evidence

Production code was introduced only after the corresponding focused test slice
failed. Representative RED evidence recorded during the implementation:

1. The settings/dependency preflight produced 22 expected failures before the
   five settings, namespace validation, secret handling, leeway range, env
   placeholders, and exact dependency pins existed. The same slice passed after
   the minimal config changes.
2. Protocol tests initially failed during collection with
   `ModuleNotFoundError: No module named 'coordination'`. They passed after the
   frozen slotted contracts and package exports were added.
3. Serialization was split into RED slices of 13, 8, and 7 failures for strict
   JSON/decimals and key topology, lease request/result receipts, and
   commit/admin evidence envelopes. Each slice passed before the next was
   started.
4. The SDK slice initially had 7 failures because the adapter did not exist.
   It passed after adding the published Upstash 1.7.0 call shape, disabled SDK
   retries, and the bounded transport retry rule.
5. Lease, coordinator/admin, revocation, and rate-limit behavior each began with
   missing implementation failures and were made green incrementally against
   the local stateful fake. No test made a network call.
6. A four-test hardening RED exposed signed-decimal acceptance, graph TTL
   validation occurring too late, reconcile accepting absent state, and unknown
   Redis result tags. All four passed after fail-closed validation was moved
   ahead of mutation and result-tag decoding was restricted.
7. Two PTTL hardening tests failed because impossible lock timing was accepted;
   five related timing tests passed after exact PTTL invariants were enforced.
8. Malformed revocation/rate receipt tests failed before strict receipt-first
   decode. The corrected 19-test abuse-control slice passed with stable
   `COORDINATION_STATE_CORRUPT` results and no partial mutation.
9. Malformed reservation/proof, acquisition receipt, contending lock, status
   invariant, surrogate-key, and receipt-expiry mutations each produced an
   expected focused RED before their strict validation was added. Their focused
   reruns and the complete coordination suite passed.
10. Lua source validation initially caught the lease renew/release block edit;
    after correction, all 14 embedded scripts parsed successfully.

## Final Verification

All commands below were run from `backend/` unless stated otherwise, against
commit content immediately before the implementation commit.

```powershell
..\.venv\Scripts\python.exe -m pytest tests/unit/coordination -q
```

Result: `137 passed in 6.06s`.

```powershell
..\.venv\Scripts\ruff.exe check coordination tests/unit/coordination
```

Result: `All checks passed!`

```powershell
..\.venv\Scripts\mypy.exe coordination
```

Result: `Success: no issues found in 5 source files`.

```powershell
..\.venv\Scripts\ruff.exe format --check coordination tests/unit/coordination
```

Result: `8 files already formatted`.

```powershell
..\.venv\Scripts\python.exe -m pytest -q
```

Result: `566 passed, 1 warning in 19.97s`.

```powershell
@'
from contextlib import redirect_stdout
from io import StringIO
from luaparser import ast
import coordination.upstash as u

names = (
    "GENERIC_ACQUIRE_LUA", "GRAPH_ACQUIRE_LUA", "LEASE_RENEW_LUA",
    "LEASE_RELEASE_LUA", "LEASE_ASSERT_LUA", "AUTHORIZE_COMMIT_LUA",
    "COORDINATION_STATUS_LUA", "CONFIRM_COMMIT_LUA",
    "COORDINATION_INSPECT_LUA", "COORDINATION_ADMIN_LUA",
    "REVOCATION_REVOKE_LUA", "REVOCATION_CHECK_LUA", "RATE_TIME_LUA",
    "RATE_CONSUME_LUA",
)
with redirect_stdout(StringIO()):
    for name in names:
        ast.parse(getattr(u, name))
print(f"{len(names)} Lua scripts parsed successfully")
'@ | ..\.venv\Scripts\python.exe -
```

Result: `14 Lua scripts parsed successfully` using locally installed
`luaparser==3.3.0` as a verification-only tool.

```powershell
git diff --cached --check
```

Result: clean before commit.

Additional scope checks confirmed:

- Branch and merge base were exactly `codex/shajra-reliability` and
  `85ef909072b79c1713112b6cbee3ade65517d249` before commit.
- Each required normalized dependency name occurs exactly once with its exact
  required version.
- `backend/.env.example` does not exist.
- No `backend/repositories/` or planning/specification file changed.
- The coordination implementation contains no direct HTTP request or cloud test
  surface; only the SDK constructor is present, and tests inject local fakes.
- No `TODO`, `FIXME`, `HACK`, or coverage-suppression marker remains in the new
  coordination package or its tests.

## Self-Review

- Verified acquisition receipts are checked before lock contention, state
  initialization checks, and graph fence increment; retained exact requests
  replay original lease timing and changed inputs conflict.
- Verified generic and graph lock types, HMAC labels, key suffixes, and hash tags
  remain collision-separated, while each revocation/rate subsystem deliberately
  shares its required fixed one-slot tag.
- Verified renew and release bind the exact lock digest, method, TTL where
  applicable, nonce HMAC, original result, and absolute receipt expiry.
- Verified authorization checks an existing reservation first, new reservations
  require the exact live graph lock, and the complete canonical Task 3 staged
  write receipt remains durable for process-loss recovery.
- Verified confirmation replays the exact one-slot proof before reservation
  checks, advances only one contiguous revision, and distinguishes evicted proof
  recovery.
- Verified READY/COMMITTING status invariants cover scalar, lock, reservation,
  proof, fence, revision, PTTL, and state-digest consistency.
- Verified admin transitions are operator-only exact-state CAS operations, reject
  busy/stale/decreasing evidence, preserve valid scalar lower bounds during
  corrupt-envelope repair, and place the fresh admin nonce HMAC in the head proof.
- Verified revocation and rate-limit outages, malformed state, TTL anomalies,
  and signed-64 overflow fail closed rather than returning an unsafe negative or
  capacity grant.
- Verified all public Redis errors are stable codes and do not echo keys,
  identities, tokens, secrets, or malformed raw state.

## Concerns

No task-blocking concern remains. The full suite emits one pre-existing
`StarletteDeprecationWarning` because the installed FastAPI test client still
imports the deprecated `httpx` integration. Per the task constraint, runtime
behavior was tested only with local stubs/fakes and all Lua was syntax-parsed;
no live Redis or Upstash integration call was made.

---

# Task 4 Fix Round 1 Report

## Status And Commits

`STATUS: DONE_WITH_CONCERNS`

- Reviewed fix base: `086b575fc39ab044d91e80ce09732f263add75a1`.
- Branch: `codex/shajra-reliability`.
- Fix-wave commit: the local commit containing this report; its exact SHA is
  returned in the completion response because a Git commit cannot contain its
  own final SHA.
- No push, deployment, cloud call, production action, Vercel action, Airtable
  action, Upstash request, or `main` branch action was performed.

## Changed Files

- `backend/config.py`
- `backend/upstash_url.py`
- `backend/coordination/sdk.py`
- `backend/coordination/upstash.py`
- `backend/requirements-dev.txt`
- `backend/tests/test_config.py`
- `backend/tests/unit/coordination/conftest.py`
- `backend/tests/unit/coordination/test_lua_integration.py`
- `backend/tests/unit/coordination/test_sdk.py`
- `backend/tests/unit/coordination/test_upstash.py`
- `.superpowers/sdd/2026-08-03-shajra-backend-persistence-and-migration/task-4-report.md`

No repository contract, runtime dependency file, root sample environment file,
planning document, or architecture specification was changed.

## Harness Choice

Pinned `fakeredis[lua]==2.36.2` in `backend/requirements-dev.txt`. The
`ProductionLuaRedis` fixture executes the exact script strings exported by
`coordination/upstash.py` through fakeredis/Lupa in-process. It supports
deterministic Redis command results and pre-EVAL races. It requires no Docker,
Redis service, network, or cloud credentials. The older Python fake remains for
fast contract coverage, but every finding and representative success/replay path
has exact-production-Lua coverage.

## Finding Evidence

### C1 - Confirmation Recovery Preflight And CAS

- Tests: `test_confirmation_rejects_corrupt_nested_recovery_data_without_mutation`
  (18 permit/commit/staged/HMAC identity and digest cases) and
  `test_confirmation_rejects_core_cas_race_without_mutation` in
  `test_lua_integration.py`.
- RED: the focused malformed-staged test changed confirmed revision `0 -> 1`,
  replaced proof bytes, and deleted the reservation before Python rejected the
  corrupt nested state.
- GREEN: `pytest ... -k "confirmation_rejects_corrupt_nested_recovery_data"`
  -> `18 passed`; the CAS-race test also passes with exact raced bytes retained.
- Fix: Python strictly decodes the complete five-key graph core and reservation;
  the confirmation mutation receives every expected raw core value, checks an
  exact atomic CAS, independently validates N+1 and core relationships, then
  mutates. Exact Lua plus Python preflight exercise the behavior.

### C2 - Coherent Five-Key Graph Core

- Tests: torn-core parameter matrices for acquire and authorization, both core
  CAS-race tests, acquisition receipt replay after core damage, and reservation
  replay after fence/proof loss in `test_lua_integration.py`.
- RED: missing proof during acquire and missing fence/proof during authorization
  produced three `DID NOT RAISE` failures and could consume a fence or create a
  reservation.
- GREEN: focused expanded command -> `22 passed`.
- Fix: graph acquisition and authorization use receipt/reservation-first
  two-pass scripts. Python deeply validates one coherent core snapshot; the
  mutation pass CASes all five exact raw values before mutation. Production Lua
  also validates key order, core relationships, proof scope, lock timing, and
  reservation bounds. Exact Lua exercises all cases.

### I1 - Cross-Method Nonce Reuse

- Tests: `test_renew_then_release_nonce_reuse_is_a_conflict_in_production_lua`
  and `test_release_then_renew_nonce_reuse_is_a_conflict_in_production_lua`.
- RED: both production scripts returned `COORDINATION_STATE_CORRUPT`.
- GREEN: focused command -> `2 passed`.
- Fix: retained operation receipts validate shape, compare input digest first,
  and only inspect method for an equal digest. Production Lua and fake agree.

### I2 - Commit Sequencing And Nested Relationships

- Tests: direct production-script authorization and confirmation tests,
  parameterized for `N -> N+2` and nested staged identity mismatch.
- RED: all four direct scripts returned `OK` and mutated state.
- GREEN: focused command -> `4 passed`; combined graph slice later passed.
- Fix: exact decimal N+1 checks and strict permit/commit/staged repeated identity,
  fence, revision, digest, and canonical JSON checks run before mutation.

### I3 - Applied PTTL And KEEPTTL Races

- Tests: acquire in generic/graph domains and renew for PTTL `0`, `-1`, `-2`,
  and greater-than-requested; plus three KEEPTTL expiration-race cases.
- RED: 12 invalid-PTTL cases left locks/receipts and advanced graph fences.
- GREEN: invalid-PTTL command -> `12 passed`; KEEPTTL race -> `3 passed`.
- Fix: provisional lock writes are fully timing-checked before fence/receipt
  publication. Graph fence publication is deferred. Renewal captures exact raw
  bytes and absolute expiry and restores both on every post-write anomaly.

### I4 - Renewal Deadline And Current TTL Integrity

- Tests: late live lock, exact deadline equality, external TTL extension,
  nonpositive PTTL `0/-1/-2`, and impossible expiry/deadline envelope.
- RED: late renewal and external extension both reported `DID NOT RAISE`.
- GREEN: initial expanded command -> `6 passed`; exact boundary -> `1 passed`.
- Fix: renewal validates canonical envelope timing, current PTTL, exact
  `PEXPIRETIME`, Redis `TIME`, absolute deadline, and graph fields before write.

### I5 - Exact Signed-64 Arithmetic

- Tests: revocation at `9007199254740993` seconds, largest whole-second
  signed-64 millisecond expiry, derived overflow with unchanged state, maximum
  graph fence, exact rate count above `2^53`, and maximum rate counter.
- RED: revocation raised `value is not an integer or out of range` after a
  partial write; rate count `9007199254740993` was incorrectly allowed against
  limit `9007199254740992`.
- GREEN: revocation boundary slice -> `3 passed`; rate boundary -> `2 passed`;
  maximum fence also passes.
- Fix: canonical decimal-string add/subtract/increment/multiply/comparison and
  exact Redis-time derivation replace precision-losing epoch/revision/fence/count
  arithmetic. Derived expiries are validated before `SET PXAT`.

### I6 - COMMITTING Fence Bound

- Tests: `test_lock_free_reservation_fence_is_bounded_by_the_stored_floor` for
  reservation token 9/floor 10 and token 11/floor 10.
- RED: token 9 was labeled `CORRUPT`.
- GREEN: focused command -> `2 passed`.
- Fix: status and admin inspection reject only reservation fence greater than
  the stored floor; active lock fence equality remains strict. Fake coverage was
  updated to use a genuinely ahead reservation as its corrupt fixture.

### I7 - Key Topology And Full Arguments

- Tests: direct confirmed/fence swap; wrong suffix/domain/hash tag; malformed
  digest/HMAC; and missing/extra arity across all 14 scripts.
- RED: all six focused topology/grammar cases returned `OK` and mutated state,
  including confirmation through swapped confirmed/fence keys.
- GREEN: topology command -> `6 passed`; all-script arity command -> `28 passed`.
- Fix: shared Lua parsers enforce deployment grammar, exact graph/generic/global
  hash tags, ordered suffixes, same-slot tags, dynamic HMAC suffixes, canonical
  integer ranges, request schemas, and digest/HMAC grammar before reads/writes.

### I8 - Exact Production Lua Harness

- Tests: the complete `test_lua_integration.py` file plus explicit generic lease
  lifecycle, graph acquire/authorize/confirm, admin initialize/reconcile,
  revocation/check, and rate-limit success/replay tests.
- RED: initial harness test failed because fixture `production_lua` did not
  exist.
- GREEN: smallest harness command -> `1 passed`; final exact-Lua slice ->
  `118 passed`.
- Fix: pinned fakeredis/Lua fixture executes byte-identical production script
  constants. Replays assert unchanged bytes and absolute expiries.

### I9 - Ambiguous Response Decode Retry

- Tests: first `JSONDecodeError` then success, two decode failures, and
  `nonce_idempotent=False`; existing tagged result and generic `ValueError` tests
  remain.
- RED: first decode failure was translated after one call; second-failure test
  observed call count 1 instead of 2.
- GREEN: complete SDK command -> `10 passed` before URL parameter expansion;
  final config/SDK command -> `84 passed`.
- Fix: nonce-idempotent EVAL retries once for transport, HTTP content decoding,
  JSON decoding, and Unicode response decoding failures with unchanged
  script/keys/args. Tagged and deterministic local failures are not retried.

### I10 - HTTPS Upstash URL

- Tests: preview/production and adapter matrices for HTTP, userinfo, fragment,
  query, trailing slash, uppercase host, malformed/empty DNS labels, leading
  hyphen, and non-URL input; accepted canonical HTTPS remains covered.
- RED: initial matrix -> `21 failed`; malformed leading-hyphen follow-up ->
  `3 failed`.
- GREEN: final config plus SDK command -> `84 passed`.
- Fix: one shared validator accepts only an exact canonical HTTPS DNS origin,
  rejects credentials/port/path/query/fragment/unsafe or malformed hosts, and
  raises fixed errors that never echo the supplied URL. Development settings and
  direct fake-client injection still require no credentials.

## Final Verification

Commands were run from `backend/` unless stated otherwise.

- Exact production Lua:
  `python -m pytest tests/unit/coordination/test_lua_integration.py -q`
  -> `118 passed in 140.18s`.
- Python-fake coordination adapter:
  `python -m pytest tests/unit/coordination/test_upstash.py -q`
  -> `77 passed in 0.96s`.
- All coordination:
  `python -m pytest tests/unit/coordination -q`
  -> `265 passed in 152.63s`.
- Configuration:
  `python -m pytest tests/test_config.py -q`
  -> `65 passed in 1.82s`; final config/SDK security command
  -> `84 passed in 14.92s`.
- Ruff:
  `ruff check coordination tests/unit/coordination config.py upstash_url.py tests/test_config.py`
  -> `All checks passed!`.
- Format:
  `ruff format --check coordination tests/unit/coordination upstash_url.py tests/test_config.py`
  -> `12 files already formatted`.
- Mypy:
  `mypy coordination upstash_url.py config.py`
  -> `Success: no issues found in 7 source files`.
- Full backend:
  `python -m pytest -q`
  -> `714 passed, 1 warning in 148.36s`.
- Lua parser: all 14 script constants parsed with `luaparser==3.3.0`:
  `14 Lua scripts parsed successfully`.
- `git diff --check` -> clean, with only Git's Windows line-ending notices.

## Self-Review

- Verified receipt-first acquisition and reservation replay remain available
  under torn current core, while every new mutation requires coherent raw CAS.
- Verified no graph fence is published until lock timing is fully valid and all
  post-write timing errors restore or delete the provisional state atomically.
- Verified confirmation cannot advance, replace proof, or delete recovery state
  after malformed preflight or a changed expected raw value.
- Verified exact arithmetic has no `tonumber` use for unbounded revision, fence,
  epoch, expiry, or counter equality; remaining numeric conversions are bounded
  microseconds, TTLs, windows, or values already proven at most `2^53-1`.
- Verified every script checks exact key count/order/domain/tag/suffix before
  state access and every public error remains a stable non-sensitive code.
- Verified the SDK still uses published Upstash 1.7.0
  `Redis.eval(script, keys, args)` and `rest_retries=0`.
- Verified no live service client was constructed by integration tests and no
  repository contract or cloud-facing action entered the change.

## Concerns

- The repository-wide `ruff format --check .` remains red on approximately 30
  unrelated pre-existing legacy files. The established Task 4 formatting scope
  is green; unrelated files were deliberately not reformatted.
- The full suite emits the pre-existing `StarletteDeprecationWarning` from
  FastAPI's test client importing the deprecated httpx integration.
- No task-blocking correctness concern remains. All Redis/Lua verification is
  deterministic and in-process; no live Upstash compatibility call was made by
  design.

---

# Task 4 Fix Round 2 Report

## Status And Scope

`STATUS: DONE_WITH_CONCERNS`

- Fix base and starting `HEAD`:
  `98a8e3c90d64cd5d124d74823296b30c7c12f6ee`.
- Branch: `codex/shajra-reliability` in the existing shared worktree.
- Preserved and completed the inherited uncommitted changes in
  `backend/coordination/upstash.py` and
  `backend/tests/unit/coordination/test_lua_integration.py`.
- No Redis, Upstash, cloud, deployment, push, merge, or `main` action was
  performed.

## Round 2 TDD Evidence

### Internal Digest Recalculation

- Test:
  `test_authorization_lua_recomputes_every_recovery_payload_digest_before_mutation`
  in `test_lua_integration.py`, parameterized for commit JSON, staged receipt,
  and write-set payload mismatches.
- RED: loaded the exact base script from
  `98a8e3c90d64cd5d124d74823296b30c7c12f6ee` in-process and ran the new test:
  `3 failed`; each invalid recovery bundle returned success on the base script.
- GREEN: the exact shipped `AUTHORIZE_COMMIT_LUA` test returned `3 passed in
  4.35s`.
- Implementation: the atomic script runs a Redis-Lua-compatible SHA-256 over
  each exact payload byte string and compares all three internal digest
  relationships before mutation. The test executes the production Lua itself
  and snapshots all key bytes and absolute expiries.

### Exact Commit Revision And Fence Parsing

- Tests:
  `test_authorization_lua_preserves_exact_high_commit_revision_and_fence` and
  `test_authorization_lua_rejects_noncanonical_or_overflow_commit_numbers`.
- RED: the initial focused production-Lua command returned `2 failed, 10
  passed`; both `2^53+1` and signed-64 maximum were rejected by the double-based
  path.
- GREEN: the exact high, malformed, noncanonical, and overflow matrix returned
  `9 passed, 121 deselected in 10.36s`.
- Implementation: a fixed-schema lexical parser extracts and validates the two
  canonical signed-64 decimal tokens, masks them with safe integers only for
  the remaining `cjson` canonicality check, and retains the exact strings for
  all identity comparisons. The amended path performs no `tonumber`, numeric
  formatting, or arithmetic on revision/fence values. The tests execute the
  production Lua itself and verify unchanged state on every rejection.

### Strict Release Envelope Validation

- Test:
  `test_release_lua_rejects_invalid_current_lock_envelope_without_mutation`,
  covering domain, scope/acquisition HMACs, expiry, TTL, renewal deadline,
  applied expiry, no-expiry state, and graph fence/base revision.
- RED: direct `LEASE_RELEASE_LUA` execution returned `10 failed`; nine cases
  deleted the lock and wrote a success receipt, while no-expiry state returned
  the less strict lost result.
- GREEN: the same exact-production-Lua matrix returned `10 passed in 15.87s`.
- Implementation: release now recomputes the canonical request and expected-lock
  digests, validates the complete generic/graph lock against request and key
  identity, checks all timing and graph fields, and compares PTTL plus absolute
  expiry before deleting the lock. Every corrupt case preserves lock, receipt,
  bytes, and TTLs.

### Fail-Closed High Expiry

- Test:
  `test_revocation_fails_closed_for_unverifiable_retained_high_expiry_without_mutation`,
  parameterized over retained receipts and retained entries with exact,
  overlong, underlong, missing-receipt, and no-expiry conditions above `2^53`.
  Existing largest-whole-second and derived signed-64 overflow tests remain.
- RED: direct production-Lua replay returned `8 failed`; the old helper accepted
  or repaired every unverifiable high-expiry retained state.
- GREEN: the exact replay matrix returned `8 passed in 9.22s`.
- Implementation: retained expiry validation now returns corruption without any
  mutation when the expected absolute expiry exceeds Lua's exact integer range.
  New revocations still pass the exact canonical signed-64 decimal directly to
  Redis `SET PXAT`, without converting it to a Lua number. Exact high-expiry
  creation and the signed-64 upper/overflow boundaries execute through the
  production Lua tests.

## Final Verification

Commands were run from `backend/` unless stated otherwise.

- Four-blocker focused production-Lua slice:
  `python -m pytest tests/unit/coordination/test_lua_integration.py -q -k
  "...round-2 selectors..."` -> `33 passed, 115 deselected in 38.89s`.
- Exact production Lua:
  `python -m pytest tests/unit/coordination/test_lua_integration.py -q` ->
  `148 passed in 190.61s`.
- All coordination:
  `python -m pytest tests/unit/coordination -q` ->
  `297 passed in 197.90s`.
- Configuration:
  `python -m pytest tests/test_config.py -q` -> `65 passed in 0.92s`.
- Ruff:
  `ruff check coordination tests/unit/coordination config.py upstash_url.py
  tests/test_config.py` -> `All checks passed!`.
- Scoped format:
  `ruff format --check coordination tests/unit/coordination upstash_url.py
  tests/test_config.py` -> `12 files already formatted`.
- Mypy:
  `mypy coordination upstash_url.py config.py` ->
  `Success: no issues found in 7 source files`.
- Full backend:
  `python -m pytest -q` -> `744 passed, 1 warning in 198.08s`.
- Lua syntax: all 14 exported production script constants parsed with
  `luaparser==3.3.0` -> `14 Lua scripts parsed successfully`.
- `git diff --check` -> clean after the report update.

## Changed Files

- `backend/coordination/upstash.py`
- `backend/tests/unit/coordination/test_lua_integration.py`
- `.superpowers/sdd/2026-08-03-shajra-backend-persistence-and-migration/task-4-report.md`

## Concerns

- Retained absolute expiries above `2^53` deliberately fail closed even when
  their metadata claims an exact match because Redis exposes `PEXPIRETIME` to
  Lua as an inexact double; silently accepting or repairing them would violate
  the binding exactness contract. Exact new-state `PXAT` creation remains
  supported with canonical decimal strings.
- The full suite emits the pre-existing `StarletteDeprecationWarning` from
  FastAPI's test client. The deterministic fakeredis/Lupa harness executes the
  exact production Lua but, per task constraints, no live Redis compatibility
  call was made.
