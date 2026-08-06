# Shajra Commit Coordination Design

**Date:** 2026-08-05

**Status:** Approved design correction for the backend persistence and migration plan

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

`backend/coordination/protocols.py`, introduced in Task 4, owns `Lease`,
`CommitReservation`, `ConfirmedCommitReceipt`, `CommitCoordinatorStatus`, and
`CommitCoordinator`:

```python
@dataclass(frozen=True, slots=True)
class Lease:
    scope: str
    owner: str
    fencing_token: int
    base_revision: int | None = None


@dataclass(frozen=True, slots=True)
class CommitReservation:
    scope: str
    state: Literal["COMMITTING"]
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str


@dataclass(frozen=True, slots=True)
class ConfirmedCommitReceipt:
    scope: str
    permit: CommitPermit
    commit: GraphCommit
    commit_sha256: str


@dataclass(frozen=True, slots=True)
class CommitCoordinatorStatus:
    scope: str
    confirmed_revision: int
    active_reservation: CommitReservation | None
    last_confirmed_receipt: ConfirmedCommitReceipt | None


class LeaseManager(Protocol):
    def acquire(self, scope: str, owner: str, ttl_ms: int) -> Lease: ...
    def renew(self, lease: Lease, ttl_ms: int) -> Lease: ...
    def assert_owned(self, lease: Lease) -> None: ...
    def release(self, lease: Lease) -> None: ...


class CommitCoordinator(Protocol):
    def acquire(
        self,
        scope: str,
        owner: str,
        committed_revision: int,
        ttl_ms: int,
    ) -> Lease: ...
    def renew(self, lease: Lease, ttl_ms: int) -> Lease: ...
    def assert_owned(self, lease: Lease) -> None: ...
    def authorize_commit(
        self, lease: Lease, commit: GraphCommit
    ) -> CommitPermit: ...
    def get_status(self, scope: str) -> CommitCoordinatorStatus: ...
    def confirm_commit(
        self, permit: CommitPermit, commit: GraphCommit
    ) -> None: ...
    def release(self, lease: Lease) -> None: ...
```

`LeaseManager` is the generic TTL lease used by enrichment. Its leases have
`base_revision=None` and cannot authorize graph commits. `CommitCoordinator` is
the graph-specific coordination contract; its `acquire` always returns a lease
whose `base_revision` equals the supplied committed revision. `get_status` is
read-only and routes by its explicit scope. `confirm_commit` routes by
`permit.scope`; callers do not supply a second, potentially inconsistent scope.

`acquire` initializes an absent coordination revision from the committed Airtable
head supplied by the caller. Otherwise it requires an exact revision match. A
missing or mismatched coordination state after initialization is a recovery
condition, not permission to reset state during an API request.

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

The coordinator has one graph commit state per namespace:

```text
READY(revision=N)
  -> lease acquired and proposed revision N+1 staged and verified
  -> COMMITTING(immutable reservation for revision N+1)
  -> matching GraphCommit appended and read back from Airtable
  -> READY(revision=N+1)
```

The lease itself remains TTL-bound. The `COMMITTING` reservation has no TTL and
cannot be aborted, replaced, or expired. While it exists, ordinary graph lease
acquisition and commit authorization fail with `COMMIT_RECOVERY_REQUIRED`.

### Authorization

After staging and verification, `authorize_commit` computes Task 1 canonical JSON
and `graph_commit_sha256(commit)`, then passes both to one Upstash Lua operation.
The script atomically:

1. verifies the lock value still matches the lease owner and fencing token;
2. verifies the coordination revision equals `commit.revision - 1` and the
   lease's `base_revision`;
3. verifies that no conflicting reservation exists;
4. stores canonical `GraphCommit` JSON, that exact digest, and a caller-generated
   `cpr_<uuid4 hex>` permit ID in an immutable `COMMITTING` reservation without
   expiry; and
5. returns `CommitPermit(scope=lease.scope, ..., commit_sha256=digest)`.

An exact retry after an ambiguous Upstash response returns the existing permit.
Any different operation, revision, fencing token, permit ID, or commit digest is a
conflict and cannot replace the reservation.

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
`permit.scope` and run an atomic Lua operation that verifies the immutable
reservation, recomputes and verifies the commit digest, advances the confirmed
coordination revision, records a `ConfirmedCommitReceipt`, and clears the active
reservation. Audit state normally transitions from `COMMITTING` to `COMMITTED`;
if the intermediate audit write failed, repair transitions the durable `PENDING`
record directly to `COMMITTED` after proof.

`get_status(scope)` returns `CommitCoordinatorStatus` containing the confirmed
revision, active reservation, and one-slot last-confirmed receipt. The receipt is
read-only evidence, not graph visibility authority.

`GraphCommits` remains the graph visibility authority. Redis contains only
coordination state: leases, fencing counters, the confirmed coordination revision,
the active reservation, and the last-confirmed retry receipt. It contains no graph
entities or snapshots.

## Crash Recovery

Recovery is deterministic and never aborts an authorized commit.

| Failure point | Required outcome |
|---|---|
| Before reservation | Mark the audit operation failed when possible; staged rows remain invisible. |
| Authorization response lost | Retry identical authorization and recover the same permit. |
| After reservation, before Airtable append | Recovery verifies staged rows and checksum, then appends the reserved commit. |
| Airtable response lost | Read by revision and canonical commit identity; accept identical duplicates. |
| After Airtable append, before confirmation | Recovery confirms the existing matching commit in Upstash. |
| After confirmation, before audit transition | Repair `PENDING` or `COMMITTING` audit from coordinator status and the exact logical `GraphCommit`; do not repeat a graph write. |
| Upstash unavailable | Reads may continue from Airtable; graph writes and coordination recovery fail closed. |
| Conflicting commit or staged payload | Report corruption, perform no further mutation, and require operator investigation. |

Active-reservation recovery remains exact and irrevocable. `recover-operation`
loads `get_status(scope)`, requires the requested operation ID to match the active
reservation, validates the reserved canonical commit and matching staged row set,
reconstructs the proposed snapshot, validates graph invariants and checksum, then
either confirms an already-present identical commit or appends and confirms the
reserved commit. It never marks that operation failed and never creates a
replacement permit.

When no matching active reservation exists, recovery performs audit repair only.
The audit record supplies canonical `GraphCommitJson` and `CommitSha256`. A matching
logical `GraphCommit` plus an exact `last_confirmed_receipt` directly proves
confirmation. If the one-slot receipt has advanced, then
`status.confirmed_revision >= target.revision`, a contiguous non-conflicting
logical commit sequence through that revision, and the exact target
`GraphCommit`/digest prove the earlier sequential confirmation. The service or CLI
appends the missing `COMMITTED` audit transition and performs no repository append
or coordinator confirmation.

An absent or incompatible Redis coordination state is not silently rebuilt by an
API writer. Operator recovery may reconcile an idle coordination revision to the
highest contiguous, non-conflicting Airtable commit only when no active
reservation exists.

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

- Authorization is one atomic script and rejects wrong owner, fencing token,
  revision, or conflicting reservation.
- Authorization returns permit scope from the lease and the exact Task 1 commit
  digest stored in the reservation.
- Exact authorization retry returns the same permit.
- Reservation has no expiry and blocks new writers after lease expiry.
- Confirmation requires the exact permit and commit digest.
- Confirmation advances revision once and is idempotent.
- `get_status(scope)` returns confirmed revision, active reservation, and the
  last-confirmed receipt without mutation.
- Upstash failure at each coordinator call fails closed.

### Task 5 Service Tests

- Failures before authorization produce no visible commit.
- Every failure after authorization leaves recoverable `PENDING` or `COMMITTING`
  audit and never transitions it to `FAILED`.
- Recovery completes a reserved commit after lease expiry.
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

## Operational Constraints

All implementation and tests remain local unless an existing opt-in staging test
is deliberately enabled under the plan's staging guards. No command in this design
targets production Airtable, production Vercel, or any other production/cloud
state. This design adds no database and does not alter the migration target.
