# Shajra Reliability and Relationship Model Design

Status: Approved in conversation on 2026-08-03

## Summary

Shajra currently has two distinct classes of failure:

1. Production wiring is broken. Both Vercel projects build from the repository
   root even though the applications live in `frontend/` and `backend/`. The
   public frontend falls back to a Railway API that no longer exists.
2. The family graph is not modeled safely. Parent and spouse IDs are mixed with
   free-text names, a person can have only one spouse, substring matching can
   create incorrect links, mutations run a global write-side "self-heal", and
   the renderer relies on ad hoc spouse and root suppression.

The repair will be staged. First, make the existing services deployable on
Vercel and expose failures clearly. Then introduce a normalized relationship
model, deterministic graph projection, guarded admin workflows, and a reviewed
migration. Airtable remains the system of record for this iteration.

## Goals

- Run the frontend and FastAPI backend on the existing Vercel Hobby projects,
  with no Railway dependency.
- Stay within the existing free-tier services and quotas; introduce no paid
  infrastructure or new vendor account without explicit approval.
- Represent multiple unions, remarriage, single parents, children from distinct
  unions, unresolved relationships, and disconnected family branches.
- Make every graph mutation deterministic, validated, auditable, and reversible.
- Prevent cycles, self-links, duplicate edges, invalid references, contradictory
  dates, and silent fuzzy-link writes.
- Give public contributors a clear form and give administrators canonical member
  selectors plus an explicit workflow for unresolved names.
- Render a stable, component-aware ancestry DAG from normalized relationships
  without losing people or recursively duplicating the same branch.
- Preserve all current data and require review of ambiguous migrations.
- Add automated coverage for graph logic, APIs, forms, and the deployed preview.

## Non-Goals

- Replacing Airtable with a new database in this iteration.
- Automatically deciding that similarly named people are the same person.
- Publishing private submissions, email addresses, or phone numbers in public
  graph responses.
- Mutating production data as part of deployment verification.
- Supporting arbitrary genetic analysis or calculating legal kinship.

## Current-State Findings

- The frontend project must use `frontend/` as its Vercel root directory.
- The backend project must use `backend/` as its Vercel root directory.
- `NEXT_PUBLIC_API_URL` is absent, so the frontend uses a dead Railway fallback.
- The current approved dataset contains 27 people and 11 raw root components.
- The current records contain no detected cycles or dangling IDs, but they do
  contain name/ID mismatches, one non-reciprocal spouse link, and unresolved
  parent and spouse names.
- `self_heal_graph` and `relink_potential_orphans` use substring matching and
  write to Airtable automatically after mutations.
- The in-memory undo stack cannot be reliable on stateless Vercel functions.
- The frontend production build succeeds locally, but lint currently reports 44
  errors and 11 warnings.
- The installed Next.js 16.2.1 release and transitive dependencies have known
  advisories with a non-major patched Next.js release available.

## Chosen Approach

Use a staged normalized repair:

1. Correct deployment configuration and error visibility.
2. Extract pure graph-domain logic and enforce invariants around all writes.
3. Add normalized relationship tables while retaining legacy fields as a
   temporary compatibility source.
4. Migrate only unambiguous relationships automatically; review everything else.
5. Switch reads to the normalized model, then retire legacy write paths.

A minimal patch was rejected because it would preserve the single-spouse limit
and unsafe fuzzy linking. A complete database replacement was rejected because
it adds migration and operational risk without being necessary for this dataset.

## Data Model

### People

The existing `ApprovedMembers` table remains the canonical person table. Public
person serialization must omit administrative and private contact fields unless
an authenticated route explicitly requires them.

Each person receives an immutable application-level `PersonId`. Airtable record
IDs remain repository-local and are never used as public identities. People are
archived rather than hard-deleted so references, audit history, and restored
records retain the same identity. Each person also has zero or one
`PrimaryFamilyUnit` used only to place that person in the main visual ancestry;
all other valid relationships remain references.

Legacy `FatherRecordId`, `MotherRecordId`, `SpouseRecordId`, `FatherName`,
`MotherName`, and `SpouseName` fields remain readable during migration. Once the
normalized model is active, application writes must not update those fields.

### FamilyUnits

Each record represents either a one-parent family or a relationship between two
known people:

- `FamilyUnitId`: immutable application identity
- `Kind`: single-parent or union
- `AdultA`: linked record to `ApprovedMembers`
- `AdultB`: optional linked record to `ApprovedMembers`
- `Status`: unknown, married, separated, divorced, or widowed
- `StartDate`, `StartDatePrecision`, `EndDate`, and `EndDatePrecision`
- `Notes`: optional administrator-only context
- `MigrationRunId` and `SourceRecordId`: optional repository-only provenance

Adult ordering is canonicalized by `PersonId` before uniqueness checks. A pair
can have more than one historical union only when an administrator explicitly
confirms that distinction. A single-parent unit has only `AdultA`. Unknown adults
are not materialized as fake people.

### ParentChildLinks

Each record represents one directed relationship:

- `LinkId`: immutable application identity
- `Parent`: linked record to `ApprovedMembers`
- `Child`: linked record to `ApprovedMembers`
- `Role`: father, mother, or parent
- `RelationshipType`: biological, adoptive, step, guardian, or unknown
- `FamilyUnit`: optional linked record to `FamilyUnits`
- `Notes`: optional administrator-only context
- `MigrationRunId` and `SourceRecordId`: optional repository-only provenance

The pair `(Parent, Child, RelationshipType)` is unique in application logic. A
child can have multiple valid relationship types, but has exactly zero or one
`PrimaryFamilyUnit` across all relationship types. That unit contains the one or
two parent links used for main-tree placement. Extra relationships remain visible
on the person page without causing recursive duplication in the main graph.

### PendingSubmissions

Public submissions remain proposals. They may contain free-text relation names
and optional selected candidate `PersonId` values, but they never create graph
links directly.
Approval requires the administrator to resolve each proposed relationship to a
known person, deliberately create a new person, or leave it unresolved.

### ChangeLog

Replace the process-local undo stack with persistent audit records:

- immutable `OperationId` and expected graph revision
- actor and timestamp
- operation and target record
- before and after snapshots with private fields protected
- request correlation ID
- state: pending, committed, failed, or compensated
- reversal status and reversing operation ID

Undo is implemented as a compensating validated mutation, not as deletion of
history. Audit records are never exposed through public routes.

### GraphState and Coordination

A singleton `GraphState` record stores the current committed graph revision and
last committed `OperationId`. The distributed lock uses a unique lease token,
bounded expiry, renewal, and a monotonic fencing token. Each staged write records
that fencing token; stale operations cannot become visible after a newer token
commits. Lock loss or coordination-store failure makes mutations fail closed.

Redis stores only lock leases, fencing counters, session revocation IDs, and
rate-limit counters. It stores no family records, contact data, biographies, or
audit snapshots.

## Graph Invariants

All mutation paths use one validation service before persistence:

- A person cannot be their own parent, child, or partner.
- Every ancestry-bearing biological, adoptive, step, or unknown parent-child
  relationship must remain acyclic, whether or not it is the primary display
  relationship. Guardian links do not participate in ancestry traversal.
- Every referenced person and family unit must exist.
- A family unit cannot contain the same adult twice.
- Duplicate family units and duplicate parent-child links are rejected.
- A link's `FamilyUnit`, when present, must contain that parent and be compatible
  with the child's other link in the same unit.
- A child has at most one `PrimaryFamilyUnit`, and each parent link used by that
  primary unit must name the same unit.
- A child cannot have contradictory links for the same role without an explicit
  relationship type distinction.
- Deleting a person is blocked until dependents are reassigned or the admin
  confirms a validated archival operation that handles the related links.
- Birth and death values must be parseable as a year or supported date format;
  death cannot precede birth; a person with a real death date cannot be alive.
- All dates carry explicit day, month, or year precision. Family-unit end dates
  cannot precede start dates. Impossible parent-child chronology is blocking;
  suspicious but possible age gaps are warnings.
- Ambiguous names never become relationships without administrator confirmation.

Validation returns structured issue codes, affected IDs, severity, and suggested
manual actions. It does not repair data as a side effect.

## Backend Architecture

Introduce focused modules with no Airtable calls in graph algorithms:

- `domain/models.py`: person, family-unit, link, graph, and validation types
- `domain/validation.py`: invariant checks and proposed-mutation validation
- `domain/projection.py`: deterministic component-aware ancestry DAG and person
  relationship projections
- `repositories/`: Airtable adapters behind repository protocols
- `services/relationships.py`: transactional-style mutation orchestration,
  persistent audit writes, and compensating rollback
- `services/migration.py`: snapshot parsing, dry-run planning, and reviewed apply

Airtable does not provide a transaction spanning these records. Every mutation
therefore includes an idempotency key and expected graph revision. A distributed
lock serializes graph writes across Vercel instances; the approved implementation
target is an Upstash Redis free-tier integration. Until that integration is
explicitly approved and configured, graph mutation endpoints remain disabled.
Revision mismatch returns `409 Conflict`.

Each operation validates the complete proposed state before its first Airtable
write, records durable pending, committed, failed, or compensated state in
`ChangeLog`, and verifies record versions before compensation. Projection exposes
only committed revisions. Recovery never relies on work continuing after the HTTP
response; an interrupted operation is completed or compensated by an explicit,
idempotent recovery command. A retry resumes or returns the recorded result
instead of duplicating links.

The public tree endpoint remains compatible while it transitions to a versioned
response. A new normalized endpoint may be introduced as `/api/v2/tree`; the
existing `/api/tree` becomes an adapter until the frontend migration is complete.

`self_heal_graph` and `relink_potential_orphans` are removed from mutation paths.
Name matching can generate ranked suggestions using normalized exact tokens, but
must include confidence and must never write.

Airtable formulas built from user input must use a centralized escaping helper.
Backend configuration must fail closed in production when required secrets are
missing. The API must not return secret values to the admin frontend; it may only
report whether a server-managed integration is configured.

## Tree Projection

Projection is deterministic and side-effect free:

1. Load people, family units, and parent-child links into indexed immutable
   structures.
2. Validate the complete snapshot and separate blocking errors from warnings.
3. Build primary ancestry edges and calculate roots from the resulting DAG.
4. Group children under their explicit family unit when one exists.
5. Represent single-parent children under a single-parent family node.
6. Sort roots, family units, and children by stable application IDs rather than
   Airtable order.
7. Emit every disconnected component.
8. Detect pedigree collapse, cousin unions, and ancestors reachable by multiple
   paths; emit later appearances as labelled references rather than recursively
   expanding an already visited person.

Unknown names are annotations, not synthetic person nodes. A response with
blocking graph errors returns a structured diagnostic to administrators and a
safe partial result or explicit unavailable state to public clients, never a
silent empty tree.

## Graph Quality Bar

The graph is accepted only when both its family logic and its visual geometry are
correct:

- Every approved person appears exactly once as a primary visual person and is
  reachable through a component root or the list fallback.
- Every normalized union and primary parent-child relationship appears in the
  visual graph, with no relationship implied solely from name text.
- Children connect to the union or single parent that owns their relationship;
  connector lines never attach to an unrelated spouse card.
- Remarriages are shown as distinct unions in chronological order when dates are
  known, with each union's children kept in the correct branch.
- Cross-family and non-primary relationships are visible as labelled references
  without expanding the same descendants repeatedly.
- Nodes never overlap, labels stay inside their controls, and connector endpoints
  remain attached at every supported zoom level.
- The initial viewport fits a useful portion of the family, preserves zoom and
  pan controls, and offers one-action fit-to-tree and fit-to-branch behavior.
- Desktop and mobile layouts are verified with screenshots and geometry checks
  against fixtures for narrow, wide, deep, disconnected, and multi-union trees.
- A deterministic layout input produces the same node and edge ordering on every
  request and browser refresh.
- The graph-health panel accounts for omitted or unresolved relationships, so a
  visually clean result can never conceal data loss.

## Frontend Design

### Public tree

- Replace the nested-list CSS renderer with `@xyflow/react` custom nodes and
  edges laid out by the ELK layered algorithm through `elkjs`.
- Model people and unions as distinct layout nodes with fixed, named ports. Use
  orthogonal relationship edges and deterministic ELK options to reduce crossings
  and keep children attached to the intended union.
- Show loading, unavailable, empty, partial-data, and ready states distinctly.
- Render explicit family-unit nodes so children attach to the correct partnership
  or single parent.
- Support multiple unions without cloning a person's descendants.
- Keep pan, zoom, reset, and fit controls stable on desktop and mobile.
- Provide a list fallback so every person remains discoverable even when a large
  graph is difficult to inspect visually.

### Public submission

- Use explicit alive/deceased controls and conditionally show death fields.
- Validate dates, email, phone, file type, file size, and required values on both
  client and server.
- Offer relation-name autocomplete as a suggestion, while allowing "not listed"
  without inventing an ID.
- Explain submission errors inline and preserve entered values after failures.
- Treat edits as proposals tied to the target member, not as duplicate people.
- Persist the raw proposal before optional AI enrichment. AI matching must not
  block or determine whether the public submission is accepted, and must not
  depend on work continuing after a Vercel function has returned.

### Admin editor

- Load canonical people and relationships rather than flattening the rendered
  tree back into editable data.
- Use member selectors for structural links and a separate unresolved-name field.
- Validate drag and drop before saving; show the exact rejected invariant.
- Make multi-union and parent-child relationships first-class editable records.
- Preview a change and its affected branch before confirmation.
- Block unsafe deletion and offer reassignment or explicit relationship removal.
- Display a graph-health panel with errors, warnings, and unresolved proposals.
- Replace transient browser alerts with durable inline or toast feedback.

## Deployment Design

Use the existing Vercel projects:

- `backend`: root directory `backend`
- `frontend`: root directory `frontend`
- production frontend `NEXT_PUBLIC_API_URL`: the verified backend production
  domain
- backend: existing Airtable, Groq, Cloudinary, and admin settings plus a strong,
  required `JWT_SECRET`

Restrict CORS to the production frontend domain, Vercel preview domains needed
for testing, and local development. Do not use a dead-service fallback in
production; a missing API URL must be visible at build or startup time.

Git previews use Vercel Related Projects or branch-scoped configuration so each
frontend preview resolves its matching backend preview. Preview backend
credentials target a fixture or staging Airtable base and a separate Cloudinary
folder, never production data. If isolated preview credentials are unavailable,
preview writes remain disabled and tests use the in-memory repository.

Deploy backend preview first, verify health and read-only endpoints, then deploy
the frontend preview against it. Production promotion occurs backend first and
frontend second. No form submission or admin mutation is used as a smoke test
without explicit approval.

## Migration and Rollback

Migration is an operator-run CLI. It never runs in a Vercel HTTP request or as
unreliable post-response background work. Before production apply, it preflights
Airtable schema permissions, batches requests within provider limits, retries
`429` responses with bounded backoff, and proves backup restoration against the
staging repository.

1. Export all relevant Airtable tables to a timestamped, encrypted local backup
   outside the repository and record a checksum.
2. Run a read-only audit and generate a migration plan containing proposed
   people, unions, links, warnings, and ambiguous items.
3. Automatically plan only exact existing ID relationships and reciprocal pairs.
4. Do not infer relationships from substring names. Put unresolved names in the
   review report.
5. Review the plan before creating normalized tables or records.
6. Apply in idempotent batches with stable migration keys and record every write.
7. Re-read Airtable and verify counts, invariants, and a semantic graph checksum.
8. Enable normalized reads behind a feature flag in preview, then production.
9. Keep legacy fields and the old read path available for a defined read-only
   rollback window. Relationship mutations remain disabled during this window.

Rollback switches reads back to the legacy adapter but never deletes normalized
records automatically. Cleanup requires a dependency scan, count and checksum
verification, and separate approval. Once normalized relationship writes are
enabled, normalized data remains authoritative and application rollback must use
the compatible normalized schema. Original person records are never deleted by
migration.

## Test Strategy

### Backend unit tests

Use in-memory repositories and fixtures for:

- one person and multiple disconnected roots
- single-parent and two-parent families
- siblings and multiple generations
- remarriage and children from different unions
- adoptive, step, and guardian links
- cousin unions and an ancestor reached through multiple paths
- partner-only components, components with multiple roots, and two parent sets
- repeated historical unions between the same pair
- duplicate names with distinct IDs
- missing references and unresolved annotations
- self-links, cycles, duplicate links, and duplicate family units
- deletion with dependents
- stable ordering and repeatable projection
- legacy migration idempotency and ambiguity reporting

### API tests

- Public responses exclude private fields.
- Invalid graph mutations return stable issue codes and make no writes.
- Admin authentication fails when configuration is invalid.
- Submission validation covers dates, contact fields, uploads, and relation
  proposals.
- Audit and compensating undo survive separate application instances.
- Concurrent writes with the same or stale graph revision are serialized or
  rejected without partial visible state.
- Expired leases, stale fencing tokens, interrupted operations, and coordination
  store outages fail closed and preserve the last committed projection.
- Airtable query values are escaped.

### Frontend tests

- Component tests cover tree states, form validation, preserved errors, canonical
  selectors, and graph-health feedback.
- Browser tests cover desktop and mobile tree navigation, multi-union rendering,
  submission without sending, admin preview with mocked writes, search, map, and
  person pages.
- The frontend must pass lint, type checking, and production build.

### Preview verification

- Verify backend health, members, tree, map, search, and authentication failure
  paths with read-only requests.
- Verify every public route and check browser console and network errors.
- Capture desktop and mobile screenshots of the tree and forms.
- Verify keyboard navigation, visible focus, readable labels, and the non-canvas
  list fallback for users who cannot operate the visual graph.
- Confirm production domains remain on their previous deployment until promotion.

## Security and Privacy

- Rotate the Vercel credential shared during this work after access is no longer
  needed.
- Never write service credentials, environment exports, Airtable snapshots, or
  private submissions to Git.
- Remove default production secrets and prevent secret values from being returned
  by API routes.
- Use named administrator identities with hashed credentials or an approved
  identity provider. Issue short-lived sessions with validated issuer, audience,
  ID, expiry, and revocation state.
- Store admin sessions in secure, HTTP-only, same-site cookies through a
  same-origin frontend proxy. Do not store bearer tokens in `localStorage`.
- Throttle login attempts and require signed, timestamped webhook requests with
  replay protection.
- Define exact public-field allowlists. Keep contact data, pending submissions,
  moderation notes, audit snapshots, and repository IDs administrator-only.
- Rate-limit every public mutation plus search and email verification through the
  approved Vercel-compatible lock and rate-limit store.
- Isolate pending uploads in a non-public moderation folder. Validate MIME type,
  file signature, size, and Cloudinary result; strip EXIF metadata before public
  promotion.
- Configure and document retention periods for raw PII, rejected submissions,
  pending media, authentication records, and audit history before production.
- Use structured server logs without tokens, private contact data, or biographies.

## Acceptance Criteria

- Browser network logs contain zero requests to `railway.app` on every route.
- Both applications deploy from their correct Vercel roots, and production health,
  members, tree, search, map, and expected authentication-failure requests return
  their documented status and schema.
- Every public route renders an explicit loading, ready, empty, partial, or error
  state; no request failure can produce an unlabelled blank graph region.
- Fixtures for single parents, multiple unions, cousin unions, repeated ancestors,
  disconnected components, and multiple roots produce their exact expected
  people, family-unit, primary-edge, and reference-edge ID sets.
- For the approved production snapshot, every active `PersonId`, `FamilyUnitId`,
  and primary ancestry `LinkId` is accounted for exactly once by projection
  assertions.
- At 390x844, 768x1024, and 1440x900 viewports, automated geometry checks report
  zero node-box intersections and zero control-to-node intersections at initial
  fit and 100 percent zoom.
- Every connector endpoint is within two CSS pixels of its named port. The current
  production snapshot has no unintended connector crossings; any topologically
  unavoidable cross-reference crossing is labelled and covered by a fixture.
- Ten repeated layouts of the same fixture produce the same rounded
  node-edge-coordinate hash. Fit, fit-to-branch, pan, and zoom retain the selected
  node and do not shift toolbar geometry.
- All graph mutation invariants are enforced server-side.
- No mutation invokes fuzzy automatic linking or global self-healing.
- Migration dry-run and apply report identical planned and actual counts, preserve
  all legacy people records, and produce the expected semantic graph checksum.
- A staging restoration and application rollback drill completes successfully
  before production migration.
- Ambiguous current relationships remain explicitly unresolved until reviewed.
- Admin edits are persistent, auditable, and reversible across serverless
  instances.
- Automated accessibility checks report zero critical or serious violations, and
  keyboard-only traversal reaches every graph control and list-fallback person.
- Documented backend tests, frontend tests, lint, type checks, production build,
  migration checks, and preview browser checks complete with zero failures before
  production promotion.
- No real production submission or relationship mutation is made solely for
  testing without explicit approval.

## Implementation Sequence

1. Establish tests and pure graph-domain fixtures.
2. Patch dependencies and clear the existing lint baseline.
3. Extract repository interfaces, validators, and deterministic projection.
4. Add normalized table adapters and persistent audit support.
5. Build the dry-run migration and review report.
6. Update public and admin APIs.
7. Rework the tree, forms, and admin editor.
8. Configure Vercel preview deployments and verify read-only behavior.
9. Back up Airtable, review migration, and apply only after approval.
10. Promote backend then frontend, monitor, and retain the rollback path.

## Technical References

- Vercel monorepos: https://vercel.com/docs/monorepos
- Vercel project settings: https://vercel.com/docs/project-configuration/general-settings
- React Flow layouting: https://reactflow.dev/learn/layouting/layouting
- React Flow custom nodes: https://reactflow.dev/learn/customization/custom-nodes
- React Flow custom edges: https://reactflow.dev/learn/customization/custom-edges
- ELK layered algorithm: https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html
- Vercel function limits: https://vercel.com/docs/functions/limitations
- Airtable API limits: https://support.airtable.com/managing-api-call-limits-in-airtable
- Upstash Redis free tier: https://upstash.com/pricing/redis
- Upstash Vercel integration: https://upstash.com/docs/redis/howto/vercelintegration
