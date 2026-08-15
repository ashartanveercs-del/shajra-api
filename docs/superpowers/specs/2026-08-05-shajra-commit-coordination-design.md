# Shajra Commit Coordination Design

**Date:** 2026-08-05

**Status:** Task 4 preflight design correction; implementation remains blocked pending review

**Scope:** Resolve commit visibility, compensation, repository/coordination ownership,
atomic commit authorization, and HTTP precondition consistency. This design does not
change the completed graph-core command model, introduce Postgres, or authorize any
production or cloud operation.

## Goals

- Prevent rows staged by one operation from becoming visible through another
  operation's commit.
- Make compensation exact for additions, replacements, and removals without adding
  graph-core remove commands.
- Keep repository persistence independent of live lease management.
- Turn the cross-system commit boundary into an explicit, recoverable protocol.
- Apply revision preconditions only to graph-state mutations while retaining
  idempotency for other writes.

## Ownership Boundaries

`backend/repositories/protocols.py` owns the persistence value types needed from
Task 1:

```python
@dataclass(frozen=True, slots=True)
class WriteContext:
    operation_id: OperationId
    revision: int
    fencing_token: int
    actor_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class CommitPermit:
    scope: str
    operation_id: OperationId
    revision: int
    fencing_token: int
    permit_id: str
    commit_sha256: str


@dataclass(frozen=True, slots=True)
class GraphWriteSet:
    person_upserts: tuple[Person, ...] = ()
    person_tombstones: tuple[PersonId, ...] = ()
    family_unit_upserts: tuple[FamilyUnit, ...] = ()
    family_unit_tombstones: tuple[FamilyUnitId, ...] = ()
    parent_child_link_upserts: tuple[ParentChildLink, ...] = ()
    parent_child_link_tombstones: tuple[LinkId, ...] = ()
    unresolved_upserts: tuple[UnresolvedRelationship, ...] = ()
    unresolved_tombstones: tuple[UnresolvedRelationshipId, ...] = ()


class GraphRepository(Protocol):
    def load_committed(self, revision: int | None = None) -> GraphSnapshot: ...
    def stage(
        self, write_set: GraphWriteSet, context: WriteContext
    ) -> StagedWriteReceipt: ...
    def verify_staged(self, receipt: StagedWriteReceipt) -> None: ...
    def append_commit(
        self, commit: GraphCommit, permit: CommitPermit
    ) -> GraphState: ...
```

`GraphCommit` contains `operation_id`, `revision`, `fencing_token`, `permit_id`,
`semantic_checksum`, and `committed_at`. Task 1 defines the only canonical commit
serialization and digest functions:

```python
def canonical_graph_commit_json(commit: GraphCommit) -> str:
    value = {
        "operation_id": str(commit.operation_id),
        "revision": commit.revision,
        "fencing_token": commit.fencing_token,
        "permit_id": commit.permit_id,
        "semantic_checksum": commit.semantic_checksum,
        "committed_at": commit.committed_at.astimezone(UTC).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def graph_commit_sha256(commit: GraphCommit) -> str:
    return hashlib.sha256(canonical_graph_commit_json(commit).encode("ascii")).hexdigest()
```

`committed_at` must be timezone-aware and is normalized to UTC before hashing.
The digest covers every canonical `GraphCommit` field, including `permit_id`.
`append_commit` validates the permit identity fields, recomputes the digest, and
compares it with `permit.commit_sha256` before any Airtable read or write. The
repository instance also requires `permit.scope` to equal its configured graph
namespace. Scope is coordination routing metadata and is not persisted as an
Airtable record ID. Repository protocols do not import or mention `Lease`,
`LeaseManager`, Upstash, or Redis.

`backend/coordination/protocols.py`, introduced in Task 4, owns separate generic,
runtime graph, and operator-only contracts. A generic lease and a graph lease are
different types; a generic lease can never satisfy `authorize_commit`:

```python
@dataclass(frozen=True, slots=True)
class Lease:
    scope: str
    acquisition_id: str
    expires_at_ms: int
    ttl_ms: int
    renew_deadline_ms: int


@dataclass(frozen=True, slots=True)
class GraphLease:
    scope: str
    acquisition_id: str
    fencing_token: int
    base_revision: int
    expires_at_ms: int
    ttl_ms: int
    renew_deadline_ms: int


@dataclass(frozen=True, slots=True)
class CommitReservation:
    scope: str
    state: Literal["COMMITTING"]
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str
    staged_write_receipt: StagedWriteReceipt


@dataclass(frozen=True, slots=True)
class ConfirmedCommitReceipt:
    scope: str
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str
    staged_write_receipt: StagedWriteReceipt


@dataclass(frozen=True, slots=True)
class ReconciledHeadReceipt:
    scope: str
    revision: int
    semantic_checksum: str
    head_commit_sha256: str | None
    evidence_sha256: str
    admin_request_nonce_hmac: str


@dataclass(frozen=True, slots=True)
class CommitCoordinatorStatus:
    scope: str
    mode: Literal["READY", "COMMITTING"]
    confirmed_revision: int
    fencing_floor: int
    active_reservation: CommitReservation | None
    last_confirmation_proof: ConfirmedCommitReceipt | ReconciledHeadReceipt
    state_sha256: str


@dataclass(frozen=True, slots=True)
class CoordinationEvidence:
    scope: str
    committed_head_revision: int
    committed_head_semantic_checksum: str
    committed_head_commit_sha256: str | None
    max_durable_fencing_token: int
    fencing_floor: int
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class LeaseReleaseResult:
    code: Literal["LEASE_RELEASED", "LEASE_RELEASE_REPLAYED"]
    acquisition_id: str
    released_at_ms: int


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    code: Literal[
        "CONFIRMED", "CONFIRMATION_REPLAYED", "CONFIRMATION_PROOF_EVICTED"
    ]
    requested_permit: CommitPermit
    confirmed_revision: int


@dataclass(frozen=True, slots=True)
class CoordinationInspection:
    scope: str
    mode: Literal["UNINITIALIZED", "READY", "COMMITTING", "CORRUPT"]
    confirmed_revision: int | None
    fencing_floor: int | None
    lock_present: bool
    active_reservation: CommitReservation | None
    last_confirmation_proof: ConfirmedCommitReceipt | ReconciledHeadReceipt | None
    state_sha256: str


@dataclass(frozen=True, slots=True)
class CoordinationAdminResult:
    code: Literal["ADMIN_INITIALIZED", "ADMIN_RECONCILED"]
    previous_state_sha256: str
    state_sha256: str
    confirmed_revision: int
    fencing_floor: int


class LeaseManager(Protocol):
    def acquire(
        self, scope: str, acquisition_id: str, ttl_ms: int = 15_000
    ) -> Lease: ...
    def renew(
        self, lease: Lease, request_nonce: str, ttl_ms: int = 15_000
    ) -> Lease: ...
    def assert_owned(self, lease: Lease) -> None: ...
    def release(self, lease: Lease, request_nonce: str) -> LeaseReleaseResult: ...


class CommitCoordinator(Protocol):
    def acquire(
        self,
        scope: str,
        committed_revision: int,
        acquisition_id: str,
        ttl_ms: int = 15_000,
    ) -> GraphLease: ...
    def renew(
        self, lease: GraphLease, request_nonce: str, ttl_ms: int = 15_000
    ) -> GraphLease: ...
    def assert_owned(self, lease: GraphLease) -> None: ...
    def authorize_commit(
        self,
        lease: GraphLease,
        commit: GraphCommit,
        staged_write_receipt: StagedWriteReceipt,
        request_nonce: str,
    ) -> CommitPermit: ...
    def get_status(self, scope: str) -> CommitCoordinatorStatus: ...
    def confirm_commit(
        self, permit: CommitPermit, commit: GraphCommit, request_nonce: str
    ) -> ConfirmationResult: ...
    def release(
        self, lease: GraphLease, request_nonce: str
    ) -> LeaseReleaseResult: ...


class CoordinationAdmin(Protocol):
    def inspect(self, scope: str) -> CoordinationInspection: ...
    def initialize(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult: ...
    def reconcile(
        self,
        evidence: CoordinationEvidence,
        expected_state_sha256: str,
        request_nonce: str,
    ) -> CoordinationAdminResult: ...
```

`LeaseManager` is the generic TTL lease used by enrichment. `CommitCoordinator`
is the graph-specific runtime contract. `CoordinationAdmin` is constructed only
by authenticated operator CLI dependencies and is never injected into FastAPI
routes or ordinary service code. `get_status` routes by explicit scope and
`confirm_commit` routes only by `permit.scope`.

Every new logical acquisition uses a cryptographically random UUID acquisition
ID. It is not an actor, process, username, or other reusable owner identity. The
same ID is retained only while retrying an ambiguously answered acquisition; a
later logical acquisition must use a fresh ID. An acquisition ID must never be
intentionally reused after its 60,000-ms result receipt expires. Authorization,
confirmation, lease-operation, and admin CAS calls likewise retain one random
request nonce across transport retries. Graph acquisition/request nonces are
conflict-detectable only within that graph scope's graph key domain; generic lease
nonces are conflict-detectable only within that generic scope's key domain. They
do not claim global uniqueness across scopes or coordination domains. Revocation
and rate-limit nonces use the separate deployment-wide domain registries defined
below, where changed canonical input is atomically detectable while a receipt
remains retained.

Runtime `CommitCoordinator.acquire` never creates `confirmed-revision` or `fence`.
It requires both to have been initialized by `CoordinationAdmin`, requires the
stored confirmed revision to equal the actual committed Airtable head supplied by
the service, and otherwise returns `COORDINATION_UNINITIALIZED` or
`COORDINATION_REVISION_MISMATCH`. A request's untrusted `If-Match` value is never
an acquire argument and cannot initialize or reconcile coordination.

## Canonical Coordination Storage

`REDIS_NAMESPACE` is a non-secret deployment label validated as lowercase ASCII
letters, digits, and internal hyphens, with length `1..32`. `REDIS_KEY_HMAC_SECRET`
and `UPSTASH_REDIS_REST_TOKEN` are secret settings. Scope, acquisition nonce,
request nonce, JTI, IP, email, user identity, and other sensitive or unbounded
values are HMAC-SHA-256 digested before use in a key. Raw scope, owner identity,
IP, email, JTI, tokens, and secrets never appear in Redis keys or error details.

The versioned, collision-separated key domains are:

```text
{sj:v1:<deployment>:graph:<scope-hmac>}:lock
{sj:v1:<deployment>:graph:<scope-hmac>}:fence
{sj:v1:<deployment>:graph:<scope-hmac>}:confirmed-revision
{sj:v1:<deployment>:graph:<scope-hmac>}:commit-reservation
{sj:v1:<deployment>:graph:<scope-hmac>}:last-confirmation
{sj:v1:<deployment>:graph:<scope-hmac>}:lease-result:acquire:<acquisition-id-hmac>
{sj:v1:<deployment>:graph:<scope-hmac>}:lease-result:operation:<request-nonce-hmac>
{sj:v1:<deployment>:graph:<scope-hmac>}:admin-result:<request-nonce-hmac>
{sj:v1:<deployment>:generic:<scope-hmac>}:lock
{sj:v1:<deployment>:generic:<scope-hmac>}:lease-result:acquire:<acquisition-id-hmac>
{sj:v1:<deployment>:generic:<scope-hmac>}:lease-result:operation:<request-nonce-hmac>
{sj:v1:<deployment>:revocation}:entry:<jti-hmac>
{sj:v1:<deployment>:revocation}:nonce:<nonce-hmac>
{sj:v1:<deployment>:rate}:counter:<policy-id>:<subject-hmac>:<window-start>
{sj:v1:<deployment>:rate}:nonce:<nonce-hmac>
```

The braces are Redis Cluster hash tags. Every key used by one script shares the
same tag; graph, generic lease, revocation, and rate-limit domains cannot collide.
All revocation entries and revocation nonce receipts for one deployment
intentionally occupy one revocation slot. All rate counters and rate nonce receipts
for one deployment intentionally occupy one separate rate slot. This concentrates
each subsystem in one slot, a deliberate correctness-over-horizontal-throughput
tradeoff that makes changed-input nonce detection atomic. Current policy volume is
expected to fit that constraint; sharding would require a new nonce-registry
protocol rather than silently restoring subject-derived hash tags. Only fixed
server-owned policy IDs and canonical decimal window timestamps appear undigested.

Locks, reservations, staged-write receipts, confirmation proofs, admin evidence,
and nonce receipts use compact sorted-key ASCII JSON envelopes with exact key sets,
a fixed `schema` tag, and integer `version:1`. Revision, fence, TTL, and epoch
values inside envelopes are canonical decimal strings so Lua never compares
precision-losing doubles. A staged receipt envelope contains exactly its
`operation_id`, `revision`, `fencing_token`, Task 3 `write_set_json`, and
`write_set_sha256`. Its decoder rejects duplicate JSON keys, unknown or missing
fields, non-canonical nested write-set JSON, invalid IDs, non-canonical decimal
strings, and any recomputed write-set or envelope digest mismatch.

Python serialization uses a duplicate-detecting `object_pairs_hook`, then
re-encodes and byte-compares the canonical form. Mutating adapters first obtain a
coherent raw state read, strictly decode and recompute every digest, and pass that
exact state as a CAS input to the Lua mutation. The script byte-compares the CAS
inputs before writing, so malformed state is detected before mutation and a race
returns a retry tag rather than accepting unchecked state. Any malformed,
partially missing, non-canonical, digest-invalid, or invariant-breaking state maps
to the single stable `COORDINATION_STATE_CORRUPT` result. It is repairable only by
the evidence-gated operator CAS below.

An acquisition request is canonical ASCII JSON. Its exact generic fields are
`schema:"shajra.lease-acquire-request"`, `version:1`, `domain:"GENERIC"`,
`scope_hmac`, `acquisition_id_hmac`, and canonical-decimal `requested_ttl_ms`.
The graph form changes `domain` to `"GRAPH_COMMIT"` and adds canonical-decimal
`committed_revision`, which is the resulting `GraphLease.base_revision`.
`input_sha256` is SHA-256 over those exact canonical bytes. The two HMACs use
domain-separated input labels, so acquisition IDs cannot collide with operation
nonces or another lease domain.

The acquisition script stores
`schema:"shajra.lease-acquisition-result"`, `version:1`, `input_sha256`, `domain`,
`scope_hmac`, `acquisition_id_hmac`, canonical-decimal `requested_ttl_ms`, the
exact original canonical `Lease` or `GraphLease` payload, and canonical-decimal
`receipt_expires_at_ms`. The graph receipt also repeats `committed_revision`.
The receipt expiry is exactly the first successful script's Redis
`server_time_ms + 60,000`, and the script applies that absolute time with
`PEXPIREAT`. Receipt and lock, plus graph `INCR`, are one atomic mutation. Before
reading the live lock, testing contention, or incrementing a graph fence, acquire
strictly validates any retained receipt. Equal `input_sha256` returns
`LEASE_REPLAYED` plus the exact stored original lease without mutation; a different
digest returns `NONCE_REUSE_CONFLICT`. This ordering applies even when the original
lock has expired or a newer acquisition owns the lock.

Renew/release request JSON has the exact fields
`schema:"shajra.lease-operation-request"`, `version:1`, method (`"renew"` or
`"release"`), domain, `scope_hmac`, `acquisition_id_hmac`,
`request_nonce_hmac`, canonical lock-envelope SHA-256, and, for renew only,
canonical-decimal `requested_ttl_ms`. Its result receipt has
`schema:"shajra.lease-operation-result"`, `version:1`, `input_sha256`, method,
domain, all three HMAC fields, the exact original canonical `Lease` or
`LeaseReleaseResult` payload, and canonical-decimal `receipt_expires_at_ms`.
It is retained with `PEXPIREAT` until exactly first Redis server time plus 60,000
ms. The script validates this receipt before live-lock checks: an exact retry
returns `LEASE_RENEW_REPLAYED` or `LEASE_RELEASE_REPLAYED` and the original payload
without mutation; changed canonical input returns `NONCE_REUSE_CONFLICT`.

Every Lua result is a tagged array: success starts with `OK` and a stable result
code; expected contention or precondition failure starts with `ERR` and a stable
code. Success codes are `LEASE_ACQUIRED`, `LEASE_REPLAYED`, `LEASE_RENEWED`,
`LEASE_RENEW_REPLAYED`, `LEASE_RELEASED`, `LEASE_RELEASE_REPLAYED`,
`RESERVATION_CREATED`, `RESERVATION_REPLAYED`, `CONFIRMED`,
`CONFIRMATION_REPLAYED`, `ADMIN_INITIALIZED`, `ADMIN_RECONCILED`, `REVOKED`,
`ALREADY_REVOKED`, `NOT_REVOKED`, `TOKEN_ALREADY_EXPIRED`,
`RATE_LIMIT_ALLOWED`, and `RATE_LIMIT_DENIED`. Error codes are
`COORDINATION_UNINITIALIZED`,
`COORDINATION_REVISION_MISMATCH`, `COORDINATION_STATE_CORRUPT`,
`LOCK_UNAVAILABLE`, `LEASE_LOST`, `COMMIT_RECOVERY_REQUIRED`,
`RESERVATION_CONFLICT`, `CONFIRMATION_CONFLICT`,
`CONFIRMATION_PROOF_EVICTED`, `ADMIN_STATE_CHANGED`, `ADMIN_BUSY`,
`ADMIN_EVIDENCE_INVALID`, `NONCE_REUSE_CONFLICT`, and
`COORDINATION_UNAVAILABLE`. Errors disclose no key material or supplied identity.

All scripts validate the exact key count, argument count, ASCII/prefix/length
rules, canonical positive or non-negative decimals, and signed 64-bit bounds
before mutation. Decimal equality is lexical after canonicalization; scripts do
not use `tonumber` for revision, fence, epoch, or equality decisions. Every
existing key is validated before the first write, and all contention checks occur
before `INCR`, so a failed acquire never consumes a fencing token. Multi-key
scripts complete validation before any `SET`, `INCR`, `PEXPIRE`, or `DEL` and have
no remaining error-producing operation after their first mutation.

## Operator Initialization And Reconciliation

The operator CLI builds `CoordinationEvidence` from a fresh Airtable scan. It
proves one non-conflicting logical commit for every revision `1..head`, materializes
that head, verifies its semantic checksum, recomputes the head `GraphCommit`
SHA-256, and scans all scoped `GraphCommits` and every staged entity row for the
maximum durable fencing token. At revision zero, the commit digest is `None` and
the semantic checksum is the canonical initial-snapshot checksum. The proposed
`fencing_floor` must be strictly greater than every observed durable token. The
`evidence_sha256` covers version, scope, committed head revision, semantic checksum,
head commit digest, maximum durable fencing token, and proposed fencing floor.

`initialize` requires the exact canonical ABSENT-state digest, no lock, no
reservation, no confirmed key, and no fence key. In one CAS it writes the proven
head revision, fencing floor, and a `ReconciledHeadReceipt` as the one-slot proof.
`reconcile` requires an initialized but idle state, no lock or reservation, and an
exact `expected_state_sha256` from `CoordinationAdmin.inspect`. It may advance or
retain the confirmed revision and fencing floor, but can never decrease either;
the new floor must also strictly exceed every durable fencing token in the fresh
evidence.

Each initialize/reconcile call canonicalizes an admin request containing exactly
`schema:"shajra.coordination-admin-request"`, `version:1`, method
(`"initialize"` or `"reconcile"`), `scope_hmac`, `evidence_sha256`, and
`expected_state_sha256`. Its `input_sha256` covers those exact bytes. The fixed
graph-scope `admin-result:<request-nonce-hmac>` key stores
`schema:"shajra.coordination-admin-result"`, `version:1`, `input_sha256`, method,
`scope_hmac`, `request_nonce_hmac`, `evidence_sha256`, `expected_state_sha256`, the
exact original canonical `CoordinationAdminResult`, and canonical-decimal
`receipt_expires_at_ms`. The script writes the state transition and receipt
atomically, using `PEXPIREAT` at exactly first Redis server time plus 60,000 ms.
It strictly validates a retained receipt before inspecting current state or
evaluating the CAS. Equal input returns the exact original result without mutation;
different input returns `NONCE_REUSE_CONFLICT`. After receipt expiry, replaying an
old request cannot mutate because its stale expected-state digest fails the CAS.

Every successful initialize/reconcile writes the request's context-separated
`request_nonce_hmac` into the canonical `ReconciledHeadReceipt`. Operator callers
use a fresh random nonce for each logical admin transition, so this core
`last-confirmation` value and the resulting post-state digest change even when a
reconcile retains the same head revision, fence, and evidence. That required state
change is what makes the original expected-state digest stale after the separate
60,000-ms admin result receipt expires.

Admin inspection computes `state_sha256` over the versioned ordered tuple of exact
raw graph-key values, including explicit missing markers, before decoding. It can
therefore return mode `CORRUPT` plus a CAS digest while runtime `get_status` returns
only `COORDINATION_STATE_CORRUPT`. Reconcile may repair corrupt envelopes only when
the raw lock and reservation keys are both absent, the expected raw-state digest
matches, and fresh evidence passes. Any valid current revision/fence scalar remains
a lower bound; if a scalar itself is absent or malformed, durable Airtable head and
max-fence evidence are authoritative and the new floor still strictly exceeds all
durable tokens. The script never writes a revision/fence below any valid current or
proven durable value.

The inspected graph state tuple contains only `lock`, `fence`,
`confirmed-revision`, `commit-reservation`, and `last-confirmation`. Ephemeral
lease-result and admin-result receipt keys are deliberately excluded from both
`state_sha256` and runtime status invariants; creating or expiring a replay receipt
therefore cannot perturb an expected-state CAS digest.

Neither admin method accepts an API `If-Match`, guesses a head, skips contiguous
history proof, or clears an active lock/reservation. An absent, corrupt, or drifted
runtime state therefore fails closed until an operator presents fresh evidence
and the exact expected-state digest.

## Upstash Adapter And Lease Timing

`backend/coordination/sdk.py` is a thin `upstash-redis==1.7.0` adapter. Its only
script call is the real SDK shape `client.eval(script, keys, args)`; it never passes
redis-py's `numkeys`. A local autospecced/stub client test asserts those three
positional arguments and makes no network call. The SDK client's built-in REST
retries are disabled with `rest_retries=0`. The adapter performs at most one
additional attempt only for an ambiguous transport failure and only by replaying
the byte-identical idempotent EVAL with the same acquisition/request nonce. Tagged
protocol errors are never retried, and a second transport failure becomes
`COORDINATION_UNAVAILABLE`.

Lease scripts use Redis `TIME` and `PTTL`. The default requested TTL is exactly
15,000 ms. On the first successful acquire or renew, the script reads current
Redis time and current lock PTTL, then returns
`expires_at_ms = server_time_ms + PTTL` and
`renew_deadline_ms = expires_at_ms - 5,000` in the canonical lease payload. Callers
must start renewal on or before that absolute server-derived deadline; zero,
negative, `-1`, `-2`, or a PTTL greater than the requested TTL is lease loss or
corrupt state as appropriate. Renew and release compare the complete canonical
lock envelope, including the random acquisition ID. Generic and graph lock schemas
are distinct, and only `GraphLease` contains a fencing token and base revision.

Acquisition and renew receipt replays return the exact stored original lease
payload, including its original PTTL-derived `ttl_ms`, absolute `expires_at_ms`,
and `renew_deadline_ms`; they do not claim a current PTTL or recompute timing.
Those absolute timestamps govern safety. Thus `LEASE_REPLAYED` may safely return
an already-expired lease after the original lock expires or another acquisition
succeeds: `assert_owned`, renew, authorization, and all other live-lock checks
still fail for that old acquisition. The 60,000-ms receipt is an ambiguous-response
retry window, not a lease extension. After it expires, reuse of the same acquisition
ID is caller misuse and has no idempotency guarantee; every new logical acquisition
must use a fresh cryptographically random ID. Renew and release likewise use a
fresh random request nonce per logical operation and replay the exact original
result for 60,000 ms without extending or deleting twice.

## Persisted Row Contract

Each entity table has an `IsTombstone` checkbox. `Archived` on `PersonVersions`
remains a semantic person property and is not a persistence tombstone.

Every staged entity row contains:

- its logical entity ID;
- `GraphScope` equal to the configured repository namespace;
- `Revision`;
- `OperationId`;
- `FencingToken`;
- `IsTombstone`;
- semantic fields when `IsTombstone` is false.

The four tables use the same marker name: `PersonVersions`, `FamilyUnits`,
`ParentChildLinks`, and `UnresolvedRelationships` all use `IsTombstone`.

`GraphCommits` stores `GraphScope`, `OperationId`, `Revision`, `FencingToken`,
`PermitId`, `SemanticChecksum`, and `CommittedAt`. `ChangeLog` stores canonical
`CommandsJson`, `BeforeSnapshotJson`, `AfterSnapshotJson`,
`InverseWriteSetJson`, `CommitScope`, `GraphCommitJson`, and `CommitSha256` in
independent fields. The canonical commit identity is present from `PENDING`
onward so audit repair never has to infer `permit_id` or `committed_at`.

`GraphScope` partitions every entity and commit read before row parsing,
deduplication, or authorization. `GraphState.StateKey` equals that configured
scope. Audit repositories are configured with the same scope, require
`AuditOperation.commit_scope` to match it, and ignore other scopes before
resolving idempotency or transition state.

The normalized schema is a pre-deployment path only after an operator preflight
proves every target normalized table is empty. If any target row already exists,
rollout must stop until a scoped backfill supplies `GraphScope` on entity and
commit rows and supplies the dedicated `BeforeSnapshotJson` and
`AfterSnapshotJson` audit columns. The backfill must be verified before readers
or writers use the normalized repositories; no additional table is introduced.

## Operation-Bound Visibility

A committed revision authorizes rows by the exact tuple:

```text
(Revision, OperationId, FencingToken)
```

Within one `GraphScope`, a reader considers only rows whose tuple exactly matches
a valid logical `GraphCommit` at that revision. It then chooses the highest
authorized revision at or below the selected committed revision. The logical
commit history must be exactly the positive contiguous sequence `1..head`.
`load_committed()` selects `head`; `load_committed(N)` requires an exact commit
at `N`. Revision `0` returns the initial snapshot only when the scope has no
commits. An authorized tombstone removes that logical ID from the materialized
snapshot.

Readers parse only `GraphScope`, `Revision`, `OperationId`, and `FencingToken`
before authorization. Rows outside a relevant commit or receipt tuple are
discarded without parsing semantic fields. Semantic mapping occurs only for an
authorized candidate, and malformed semantics on such a candidate fail closed.

Rows with a revision but a different operation ID or fencing token are ignored.
Therefore, if operations A and B both stage revision 7 and only B commits, no row
from A can become visible through B's commit.

Network retries may create physically duplicated Airtable rows. Identical entity
rows under the same authorization tuple and logical ID are one logical version.
Conflicting payloads under that same identity are corruption and make snapshot
loading fail closed.

`GraphCommits` follows the same rule. Physical duplicates whose canonical commit
fields are identical are one logical commit. Multiple rows for the same revision
with different canonical fields are `COMMIT_LOG_CORRUPTION` and reads fail closed.
Airtable record IDs never participate in either identity.

## Commit State Machine

The coordinator has one initialized graph commit state per namespace:

```text
READY(revision=N)
  -> lease acquired and proposed revision N+1 staged and verified
  -> COMMITTING(immutable reservation for revision N+1)
  -> matching GraphCommit appended and read back from Airtable
  -> READY(revision=N+1)
```

The lease itself remains TTL-bound. The `COMMITTING` reservation has no TTL and
cannot be aborted, replaced, or expired. While it exists, a new graph lease
acquisition fails with `COMMIT_RECOVERY_REQUIRED`.

`get_status(scope)` performs one Lua call whose first operation is one `MGET` of
confirmed revision, fence, lock, reservation, and last-confirmation proof, followed
by `PTTL` for a present lock. The adapter strictly decodes that one coherent
snapshot and accepts only these invariants:

- `READY`: confirmed revision and fence exist, no reservation exists, and the
  one-slot proof names the confirmed revision; a valid unexpired lock may exist;
- `COMMITTING`: the same initialized fields exist, the immutable reservation's
  revision is exactly `confirmed_revision + 1`, its fence is at most the stored
  fence, and the one-slot proof still names `confirmed_revision`; the lock may
  exist or have expired.

Revision zero has an initialization proof with no head commit digest. Any missing
half of initialized state, unexpected proof revision, malformed lock, impossible
PTTL, reservation gap, or inconsistent fence is `COORDINATION_STATE_CORRUPT`.
`get_status` never assembles state from separate client reads and never mutates it.

### Authorization

After staging and verification, `authorize_commit` accepts the exact Task 3
`StagedWriteReceipt`, strictly validates its canonical write-set JSON and digest,
and requires its operation, revision, and fencing token to equal the commit and
graph lease. It computes Task 1 canonical commit JSON and
`graph_commit_sha256(commit)`, then executes the CAS script. The script atomically:

1. inspects a present reservation before consulting the lock; if its canonical
   scope, commit, commit digest, staged receipt, and receipt digest all match, it
   returns the exact originally stored permit even after lease expiry;
2. if no reservation exists, verifies the complete graph lock envelope still
   matches the graph lease and has positive PTTL;
3. verifies the coordination revision equals `commit.revision - 1` and the
   lease's `base_revision`;
4. stores canonical `GraphCommit` JSON, exact commit digest, canonical staged
   receipt envelope and digest, request-nonce digest, and a caller-generated
   `cpr_<uuid4 hex>` permit ID in an immutable `COMMITTING` reservation without
   expiry; and
5. returns `CommitPermit(scope=lease.scope, ..., commit_sha256=digest)` with a
   tagged `RESERVATION_CREATED` or `RESERVATION_REPLAYED` result.

An exact canonical retry after an ambiguous Upstash response returns the original
permit regardless of current lease state. Any different operation, revision,
fencing token, permit ID, commit digest, staged write-set JSON, or staged receipt
digest is a conflict and cannot replace the reservation. Persisting the receipt
makes recovery after process loss independent of request memory or the audit write.

Before authorization, lease loss leaves only invisible staged rows and the audit
operation may transition to `FAILED`. After authorization, lease expiry does not
invalidate the permit. The operation is commit-bound and must reach the exact
reserved `GraphCommit` through the original request or recovery.

### Append And Confirmation

`GraphRepository.append_commit(commit, permit)` has no Redis dependency. Before
any Airtable access it checks the configured repository namespace against
`permit.scope`, checks every identity field shared by the commit and permit, then
recomputes `graph_commit_sha256(commit)` and compares it in constant time with
`permit.commit_sha256`. A mismatch rejects the call without adding a commit. It
then appends the Airtable commit if an identical logical commit is not already
present and reads the logical commit back. An ambiguous Airtable response is
resolved by read-back. Identical physical duplicates are accepted; conflicting
duplicates fail closed.

Only after read-back succeeds does `CommitCoordinator.confirm_commit` route through
`permit.scope`, recompute the commit digest, and run the CAS script. The script
first checks whether the one-slot last-confirmation proof is the exact requested
`ConfirmedCommitReceipt`; if so it returns code `CONFIRMATION_REPLAYED` with the
exact original `requested_permit` and `confirmed_revision` payload, performs no
mutation, and does so even if a newer reservation exists. Code `CONFIRMED` is
reserved for the first successful state transition. For a new confirmation it
then requires the exact active reservation and enforces
`confirmed_revision == commit.revision - 1` before any write. It advances the
revision, writes the full confirmed receipt including the staged receipt, and
deletes the reservation.

If the request revision is at or below the confirmed revision but the one-slot
proof is no longer the exact requested receipt, confirm returns
`CONFIRMATION_PROOF_EVICTED`, not generic conflict or success. The caller may use
the independent contiguous Airtable proof described under recovery for audit-only
repair, but must not repeat confirmation or a graph write. Other mismatches return
`CONFIRMATION_CONFLICT`. Audit normally transitions from `COMMITTING` to
`COMMITTED`; if the intermediate audit write failed, repair may transition the
durable `PENDING` record directly to `COMMITTED` after proof.

`GraphCommits` remains the graph visibility authority. Redis contains only
coordination state: leases, fencing counters, the confirmed coordination revision,
the active reservation, and the one-slot last-confirmation proof. It contains no graph
entities or snapshots.

## Crash Recovery

Recovery is deterministic and never aborts an authorized commit.

| Failure point | Required outcome |
|---|---|
| Before reservation | Mark the audit operation failed when possible; staged rows remain invisible. |
| Authorization response lost | Retry identical authorization and recover the same permit. |
| After reservation, before Airtable append | Recovery verifies the reservation's persisted staged receipt, rows, and checksum, then appends the reserved commit. |
| Airtable response lost | Read by revision and canonical commit identity; accept identical duplicates. |
| After Airtable append, before confirmation | Recovery confirms the existing matching commit in Upstash. |
| Confirmation response lost | Exact proof retry returns `CONFIRMATION_REPLAYED` with the original permit/revision payload and no mutation. |
| After confirmation, before audit transition | Repair `PENDING` or `COMMITTING` audit from coordinator status and the exact logical `GraphCommit`; do not repeat a graph write. |
| Upstash unavailable | Reads may continue from Airtable; graph writes and coordination recovery fail closed. |
| Conflicting commit or staged payload | Report corruption, perform no further mutation, and require operator investigation. |

Active-reservation recovery remains exact and irrevocable. `recover-operation`
loads `get_status(scope)`, requires the requested operation ID to match the active
reservation, validates the reserved canonical commit and persisted
`StagedWriteReceipt`, calls `verify_staged` with that receipt, reconstructs the
proposed snapshot, validates graph invariants and checksum, then either confirms
an already-present identical commit or appends and confirms the reserved commit.
No request-process memory is required. Recovery never marks that operation failed
and never creates a replacement permit.

When no matching active reservation exists, recovery performs audit repair only.
The audit record supplies canonical `GraphCommitJson` and `CommitSha256`. A matching
logical `GraphCommit` plus an exact `ConfirmedCommitReceipt` in
`last_confirmation_proof` directly proves
confirmation. If the one-slot receipt has advanced, then
`status.confirmed_revision >= target.revision`, a contiguous non-conflicting
logical commit sequence through that revision, and the exact target
`GraphCommit`/digest prove the earlier sequential confirmation. The service or CLI
appends the missing `COMMITTED` audit transition and performs no repository append
or coordinator confirmation. A direct call to `confirm_commit` after the one-slot
proof advances returns `CONFIRMATION_PROOF_EVICTED`; this distinct result triggers
the same independent Airtable proof path rather than being treated as success.

An absent or incompatible Redis coordination state is not silently rebuilt by an
API writer or by `recover-operation`. The separate operator-only evidence command
and `CoordinationAdmin.initialize` or `reconcile` CAS are the only repair paths.

## Revocation And Rate-Limit Contracts

Task 4 defines complete fail-closed protocols rather than boolean or untyped
dictionary adapters:

```python
@dataclass(frozen=True, slots=True)
class RevocationResult:
    code: Literal[
        "REVOKED", "ALREADY_REVOKED", "NOT_REVOKED", "TOKEN_ALREADY_EXPIRED"
    ]
    revoked: bool
    server_time_ms: int
    expires_at_ms: int


class RevocationStore(Protocol):
    def revoke(
        self, jti: str, token_expires_at_s: int, request_nonce: str
    ) -> RevocationResult: ...
    def is_revoked(
        self, jti: str, token_expires_at_s: int
    ) -> RevocationResult: ...


class RateLimitPolicyId(StrEnum):
    LOGIN = "login"
    SUBMIT = "submit"
    UPLOAD = "upload"
    COMMENT = "comment"
    STORY = "story"
    SEARCH = "search"
    EMAIL_VERIFICATION = "email-verification"


@dataclass(frozen=True, slots=True)
class IpRateLimitSubject:
    kind: Literal["IP"]
    normalized_ip: str


@dataclass(frozen=True, slots=True)
class IdentityRateLimitSubject:
    kind: Literal["IDENTITY"]
    identity_id: str


RateLimitSubject = IpRateLimitSubject | IdentityRateLimitSubject


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    policy: RateLimitPolicyId
    allowed: bool
    limit: int
    observed_count: int
    remaining: int
    server_time_ms: int
    reset_at_ms: int
    retry_after_ms: int


class RateLimiter(Protocol):
    def consume(
        self,
        policy: RateLimitPolicyId,
        subject: RateLimitSubject,
        request_nonce: str,
    ) -> RateLimitResult: ...
```

The store is constructed with `JWT_LEEWAY_SECONDS`, the same server-configured
integer used by JWT claim validation. Its default is exactly 30 seconds and valid
range is `0..300`; API callers cannot choose it.
Both revocation operations validate canonical JTI, expiry, and leeway, obtain time
from Redis `TIME`, and retain a revocation through
`expires_at_ms = token_expires_at_s * 1000 + leeway_s * 1000`.

`revoke` derives `jti_hmac = HMAC(secret, "revocation-jti\0" + canonical_jti)` and
`nonce_hmac = HMAC(secret, "revocation-nonce\0" + request_nonce)`. Its canonical
request input is compact sorted-key ASCII JSON with exactly `schema` equal to
`shajra.revocation-request`, `version` equal to `1`, `jti_hmac`, canonical decimal
`token_expires_at_s`, and canonical decimal `leeway_s`; `input_sha256` hashes those
bytes. The versioned nonce receipt contains exactly `schema`, `version`,
`input_sha256`, `jti_hmac`, `token_expires_at_s`, `leeway_s`, `code`, `revoked`,
`server_time_ms`, `expires_at_ms`, and `receipt_expires_at_ms`. Stored integers are
canonical decimal strings except the envelope's numeric `version:1`.

The revoke script computes Redis time and inspects the fixed-domain nonce key
before the JTI entry. A matching `input_sha256` returns the receipt's exact original
`RevocationResult` without changing revocation state; a missing or short receipt
TTL is repaired to its stored exact expiry, while an overlong TTL is corrupt. A
different JTI, token expiry, or leeway under the same nonce returns
`NONCE_REUSE_CONFLICT` before touching either entry. A new receipt uses
`receipt_expires_at_ms = max(expires_at_ms, server_time_ms + 60_000)` and
`PEXPIREAT` at that exact instant, including a 60-second receipt for an already
expired token. The revocation entry itself expires exactly at `expires_at_ms`. If
an existing valid entry has no TTL or a TTL shorter than the required remaining
lifetime, the same script repairs it with `PEXPIREAT` before returning. An
impossible value or overlong entry/receipt TTL is corrupt. When Redis time is
already at or beyond `expires_at_ms`, `revoke` writes only the retained nonce
receipt with `TOKEN_ALREADY_EXPIRED` and does not create or extend a JTI entry.

`is_revoked` returns `REVOKED` for a valid entry, `NOT_REVOKED` for an absent entry,
and `TOKEN_ALREADY_EXPIRED` once server time reaches expiry; JWT claim validation
still rejects the token. Redis/transport/malformed-state failure is never
interpreted as `NOT_REVOKED`.

Rate-limit policies and subject kinds are immutable server-owned data:

| Policy | Limit and exact window | Subject kind |
|---|---|---|
| `LOGIN` | 5 per 900,000 ms | normalized IP |
| `SUBMIT` | 5 per 3,600,000 ms | normalized IP |
| `UPLOAD` | 10 per 3,600,000 ms | authenticated identity |
| `COMMENT` | 20 per 3,600,000 ms | authenticated identity |
| `STORY` | 20 per 3,600,000 ms | authenticated identity |
| `SEARCH` | 60 per 60,000 ms | normalized IP |
| `EMAIL_VERIFICATION` | 10 per 3,600,000 ms | normalized IP |

The caller chooses only a policy enum and a typed subject of the policy's required
kind; it cannot supply a limit, window, or reset time. Comments and stories have
distinct counters even though their numeric limits match. The script uses Redis
`TIME` and computes
`window_start = floor(server_time_ms / window_ms) * window_ms`; a request at the
exact `reset_at_ms = window_start + window_ms` belongs to the new window. Calls
`1..N` are allowed, call `N+1` and later are denied, `remaining` never goes below
zero, and denied calls report the actual observed count.

For each logical request, the script derives
`subject_hmac = HMAC(secret, "rate-subject\0" + kind + "\0" + canonical_subject)`
and `nonce_hmac = HMAC(secret, "rate-nonce\0" + request_nonce)`. After Redis `TIME`
selects the current window, the canonical request input is compact sorted-key ASCII
JSON with exactly `schema` equal to `shajra.rate-request`, `version` equal to `1`,
`policy_id`, `subject_kind`, `subject_hmac`, canonical decimal `window_start_ms`,
`window_ms`, and `limit`. `input_sha256` hashes those bytes.

The versioned rate nonce receipt contains exactly `schema`, `version`,
`input_sha256`, `policy_id`, `subject_kind`, `subject_hmac`, `window_start_ms`,
`window_ms`, `limit`, `allowed`, `observed_count`, `remaining`, `server_time_ms`,
`reset_at_ms`, `retry_after_ms`, and `receipt_expires_at_ms`. Stored integers are
canonical decimal strings except the envelope's numeric `version:1`. Its
fixed-domain nonce key expires exactly at
`receipt_expires_at_ms = reset_at_ms + 60_000`; the counter expires exactly at
`reset_at_ms`. Thus the receipt remains for one minute after the window boundary.

The script computes the current canonical input, then inspects the fixed-domain
nonce key before reading or incrementing the counter. A matching digest returns the
exact stored `RateLimitResult` without increment; a missing or short receipt TTL is
repaired to its stored exact expiry, while an overlong TTL is corrupt. Reusing that
nonce with a changed policy, subject kind/value, server-owned policy parameters,
or current window returns `NONCE_REUSE_CONFLICT` while the receipt is retained. In
particular, a retry that crosses the reset boundary conflicts rather than charging
the new window; the caller uses a fresh nonce for a new logical request. Counter
and receipt TTLs are atomically set or repaired to their exact expiry instants with
`PEXPIREAT`; a missing TTL, stale TTL, malformed count/receipt, or count outside
signed 64-bit bounds is handled before increment. Redis or adapter failure denies
the request with a stable unavailable result; it never grants extra capacity.

## Exact Compensation

Compensation uses write sets, not new graph-core remove commands.

For every logical ID touched by a forward write set, the service constructs a
canonical inverse write set from the committed before-image:

- before-image exists: inverse contains an upsert of that exact prior entity;
- before-image does not exist: inverse contains a tombstone;
- forward tombstone removes an existing entity: inverse contains its exact prior
  entity as an upsert.

`ChangeLog.InverseWriteSetJson` stores that typed structure using sorted keys,
compact separators, ASCII JSON, stable entity-kind order, and stable logical-ID
order. Deserialization requires exactly the eight `GraphWriteSet` fields and the
exact typed domain payload for each entity kind; it rejects duplicate logical
IDs, invalid ID prefixes, extra/private fields, and malformed enums or dates.
Before and after semantic snapshots remain available for conflict checks and
audit display in their dedicated fields.

Compensation loads the current committed snapshot and requires every touched
entity to still equal the target operation's after-image. A mismatch returns
`409 COMPENSATION_CONFLICT`; the service does not guess how to rebase later work.
When the check passes, it overlays the inverse write set, validates the complete
proposed snapshot, computes a new checksum, creates the inverse of the compensation
for auditability, and executes the normal stage, authorize, append, and confirm
flow as a new revision and operation.

Tombstones exist only in repository write sets and rows. Materialized
`GraphSnapshot` values continue to use the completed graph-core models and contain
no tombstone objects.

## HTTP Preconditions

- Every graph-state mutation requires both `Idempotency-Key` and integer
  `If-Match`. A stale graph revision returns `409`.
- Non-graph create and attempt routes require `Idempotency-Key` but do not require
  the graph's `If-Match` header.
- A non-graph state transition uses its own explicit expected state or resource
  version when its contract defines one. It does not borrow the graph revision.
- Read routes and login do not require mutation precondition headers.

This applies to submissions, media attempts, enrichment attempts, and review
creation. A review produces a graph mutation draft; applying that draft later is a
graph-state mutation and therefore requires both graph headers.

## Failure Semantics

- Missing initialized coordination: ordinary writes return
  `COORDINATION_UNINITIALIZED`; only the operator admin CAS may initialize it.
- Airtable/coordinator head mismatch: ordinary writes fail before staging; only an
  idle evidence-gated reconcile may advance coordination.
- Lease loss before authorization: fail closed; no commit may be appended.
- Lease loss after authorization: continue or recover the exact reserved commit.
- Upstash outage before authorization: return unavailable and leave staged rows
  invisible.
- Upstash outage after Airtable append: the commit is visible, but subsequent
  writers remain blocked until confirmation recovery succeeds.
- Permit/commit mismatch: reject before an Airtable append.
- Conflicting commit duplicates or entity duplicates: report corruption and fail
  closed.
- Audit failure before authorization: no commit; operation may fail.
- Audit failure after authorization: commit completion takes precedence; audit is
  repaired from an active reservation or, after confirmation, from coordinator
  status plus the exact logical `GraphCommit`; no graph write is repeated.
- Replaced one-slot confirmation proof: return `CONFIRMATION_PROOF_EVICTED` and
  require independent contiguous Airtable proof for audit-only repair.
- Malformed or invariant-breaking Redis state: return
  `COORDINATION_STATE_CORRUPT`; no runtime method mutates or repairs it.
- `GraphState` update failure: tolerate it because the table remains a cache.

## Required Tests

### Task 1 Unit Tests

- Operations A and B stage the same revision with different operation IDs and
  fencing tokens; committing only B exposes only B's rows.
- A row matching revision but not operation ID remains invisible.
- A row matching revision and operation ID but not fencing token remains invisible.
- Upserts and tombstones materialize correctly for all four entity kinds.
- `append_commit` rejects a permit mismatch without adding a commit.
- Changing only `semantic_checksum` under an otherwise matching permit is rejected
  before append.
- Changing only `committed_at` under an otherwise matching permit is rejected
  before append.
- Identical logical commit duplicates are idempotent; conflicting duplicates fail
  closed.
- Repository protocols import no coordination type.

### Task 3 Repository Tests

- Airtable mappers persist `IsTombstone` and the full authorization tuple.
- Snapshot loading filters rows through the exact commit tuple before selecting a
  logical version.
- Stage retry duplicates are deduplicated only when canonical payloads match.
- Commit response loss and identical duplicate rows return one logical success.
- Conflicting commits or entity rows return stable corruption errors.
- Wrong permit scope and commit-digest mismatches, including changed checksum or
  commit time, are rejected before Airtable access.

### Task 4 Coordinator Tests

- Runtime acquire on absent confirmed/fence keys returns
  `COORDINATION_UNINITIALIZED`, creates no key, and never consumes a request
  `If-Match` value.
- Acquire accepts only the actual committed Airtable revision, rejects mismatch,
  checks lock contention before `INCR`, and leaves the fence unchanged on every
  failed acquisition.
- Generic and graph acquire atomically persist an HMAC-keyed, versioned acquisition
  receipt with the exact request digest, original lease, and absolute
  Redis-time-plus-60,000-ms receipt expiry. A retained equal-input retry returns
  `LEASE_REPLAYED` with that original lease before lock/contention/`INCR` checks;
  changed TTL or graph committed revision under that retained scope/domain receipt
  returns `NONCE_REUSE_CONFLICT`.
- Acquisition-response loss followed by original-lock expiry and even a successful
  fresh acquisition still replays the original lease and fencing token without
  mutation or another `INCR`. Its unchanged expired absolute deadline makes it
  unusable for assert, renew, or commit authorization. Acquisition IDs are fresh
  per logical acquisition and are never intentionally reused after receipt expiry.
- First acquire/renew success exposes Redis-derived PTTL, expiry, and the exact
  expiry-minus-5,000-ms renewal deadline for the 15,000-ms default. Receipt replay
  returns the exact original timing payload rather than claiming current PTTL.
- Renew/release receipts contain the exact canonical operation input digest,
  original result, and Redis-time-plus-60,000-ms expiry; receipt-first exact replay
  returns the original result without extending/deleting twice, while changed
  operation, lease, or TTL returns `NONCE_REUSE_CONFLICT`.
- Generic and graph leases have distinct lock/receipt domains, and a generic lease
  cannot authorize a commit.
- Authorization rejects wrong lease kind, acquisition ID, fencing token, base
  revision, non-sequential revision, invalid nonce, or conflicting reservation.
- Authorization validates and persists canonical Task 3 `StagedWriteReceipt`
  JSON/digest and rejects changed write-set JSON, digest, operation, revision, or
  fence.
- Authorization inspects an existing reservation first and returns its exact
  original permit on a canonical match after lease expiry; only new reservation
  creation requires a live lock.
- Reservation has no expiry, contains complete recovery data, and blocks new graph
  acquisitions after process and lease loss.
- Confirmation checks the exact last-confirmation proof retry before active
  reservation, returns `CONFIRMATION_REPLAYED` with the original permit/revision
  payload and no mutation, requires `confirmed_revision == commit.revision - 1`
  for a new `CONFIRMED` transition, advances once, and returns
  `CONFIRMATION_PROOF_EVICTED` after its proof is replaced.
- One `get_status` Lua/MGET snapshot validates READY and COMMITTING invariants;
  torn, partial, malformed, non-canonical, or digest-invalid state returns exactly
  `COORDINATION_STATE_CORRUPT` without mutation.
- Strict decoders reject duplicate keys and extra/missing fields for lock,
  acquisition/operation/admin result receipt, reservation, staged receipt,
  confirmation proof, and admin evidence envelopes.
- Script tables cover invalid ARGV/KEYS, canonical decimal grammar, signed-64
  minimum/maximum/overflow, no `tonumber` equality, validate-before-write, and no
  partial mutation on every error tag.
- Versioned HMAC key tests prove domain separation, cluster hash-tag co-location,
  and absence of raw scope, actor/owner, IP, email/identity, JTI, token, and secret
  material from keys and errors. Every revocation entry and nonce key shares the
  fixed deployment revocation tag; every rate counter and nonce key shares the
  separate fixed deployment rate tag, across different JTIs, policies, subjects,
  and windows. Graph/generic nonce tests assert only scope/domain-local guarantees.
- Admin initialize/reconcile rejects an active lock or reservation, stale expected
  digest, non-contiguous or conflicting Airtable evidence, head checksum/digest
  mismatch, and a fencing floor not strictly above every durable token. It never
  decreases revision/fence. Its canonical request digest covers method, evidence
  digest, expected-state digest, and scope HMAC; its fixed graph-scope nonce receipt
  stores the exact original result for 60,000 ms and is checked before state/CAS.
  Exact retry returns that result without mutation, changed input conflicts, and
  post-expiry retry fails stale expected-state CAS. A fresh admin nonce HMAC in
  every reconciled-head proof changes the core digest even for a retained head.
  Lease/admin result keys never affect the inspected graph state digest.
- The Upstash 1.7.0 adapter calls `eval(script, keys, args)` against a local
  autospecced/stub client, disables SDK retries, retries one ambiguous transport
  failure with identical nonce/input, and never retries tagged failures.
- Revocation tests cover `revoke`/`is_revoked`, exact canonical input/receipt digest,
  expiry plus leeway, Redis `TIME`, matching nonce replay with the exact original
  result, changed JTI/expiry/leeway `NONCE_REUSE_CONFLICT`, exact receipt retention
  through `max(expires_at_ms, server_time_ms + 60_000)`, atomic short/missing-TTL
  repair, malformed state, and fail-closed outage.
- Rate-limit tests cover every typed policy/subject pairing, separate comment and
  story buckets, exact canonical input/receipt digest, exact fixed-window boundary,
  N/N+1, matching replay without double-charge, changed policy/subject/window
  `NONCE_REUSE_CONFLICT` while the nonce receipt remains through reset plus 60,000
  ms, atomic counter/receipt TTL repair, malformed counter/receipt or overflow, and
  fail-closed outage.

### Task 5 Service Tests

- Failures before authorization produce no visible commit.
- Service order is load actual Airtable head, acquire against initialized
  coordination, reload under lease, then compare the request `If-Match`; an absent
  coordinator never bootstraps from the request.
- Every failure after authorization leaves recoverable `PENDING` or `COMMITTING`
  audit and never transitions it to `FAILED`.
- Recovery completes a reserved commit after process and lease loss using only the
  reservation's persisted canonical staged receipt and commit identity.
- Idempotent retry repairs `PENDING` or `COMMITTING` audit after reservation
  clearance using direct last-receipt proof or later-revision sequential proof,
  without repeating a graph write.
- Compensation restores exact before-images and tombstones entities introduced by
  the target operation.
- Compensation rejects later modifications to any touched entity.

### API And CLI Tests

- Graph mutation routes require both graph headers.
- Submission, enrichment-attempt, and review-create routes require only
  `Idempotency-Key` among graph precondition headers.
- With an active reservation, `recover-operation` accepts only that reservation's
  operation ID and always completes or confirms that exact commit.
- `recover-operation` repairs audit only after confirmation when coordinator status
  and the exact logical commit prove success; it does not append the graph commit
  again.
- `recover-operation` never initializes or reconciles coordination. Separate admin
  CLI tests require reviewed evidence, exact expected-state CAS, idle state, and
  production confirmation guards.

## Operational Constraints

All implementation and tests remain local unless an existing opt-in staging test
is deliberately enabled under the plan's staging guards. No command in this design
targets production Airtable, production Vercel, or any other production/cloud
state. This design adds no database and does not alter the migration target.
