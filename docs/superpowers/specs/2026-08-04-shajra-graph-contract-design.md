# Shajra Graph Contract Decisions

**Status:** Approved design addendum

**Date:** 2026-08-04

## Purpose

This addendum resolves the graph-core preflight contradictions found between the
Shajra reliability design and the graph/backend/frontend implementation plans.
Where those documents conflict on the subjects below, this addendum governs.
It does not expand deployment scope or permit any cloud or production mutation.

## 1. Canonical Rendered Topology

The v2 tree response separates source relationship records from canonical
rendering topology.

- `parent_child_links` contains only the allowlisted `ProjectedLink` fields for
  underlying relationship records. It is
  relationship/detail data and is never rendered as one connector per record.
- `adult_memberships` is the authoritative rendered set of adult-to-family-unit
  edges.
- `descendant_edges` is the authoritative rendered set of family-unit-to-child
  edges.
- The frontend consumes the two canonical edge collections directly. It must not
  infer adult memberships, group raw links, or manufacture primary edges.

For a valid two-adult family with one child, the response contains two adult
memberships and exactly one descendant edge, even when two parent-child records
establish the child's relationships to both adults. A single-parent family has
one adult membership and one descendant edge per child.

Canonical edge IDs are semantic and stable:

```text
adult:{family_unit_id}:{person_id}
child:{family_unit_id}:{child_id}
```

Every edge collection is sorted by its semantic edge ID. A primary
`ParentChildLink` must have a non-null `family_unit_id` equal to the child's
`primary_family_unit_id`. Raw links expose a required backend-provided `primary`
boolean for relationship detail, but that flag is not a rendering instruction.

The projection contract adds:

```python
@dataclass(frozen=True, slots=True)
class AdultMembershipEdge:
    edge_id: str
    family_unit_id: FamilyUnitId
    adult_id: PersonId
    slot: Literal["adult_a", "adult_b"]


@dataclass(frozen=True, slots=True)
class DescendantEdge:
    edge_id: str
    family_unit_id: FamilyUnitId
    child_id: PersonId
```

`TreeProjection` includes both tuples alongside `parent_child_links`.

## 2. Complete Projection DTOs

The pure domain projection uses these public fields:

```python
@dataclass(frozen=True, slots=True)
class ProjectedPerson:
    person_id: PersonId
    full_name: str
    gender: Gender
    birth: PartialDate | None
    death: PartialDate | None
    is_alive: bool | None


@dataclass(frozen=True, slots=True)
class ProjectedFamilyUnit:
    family_unit_id: FamilyUnitId
    kind: FamilyUnitKind
    adult_a_id: PersonId
    adult_b_id: PersonId | None
    status: UnionStatus
    start: PartialDate | None
    end: PartialDate | None


@dataclass(frozen=True, slots=True)
class ProjectedLink:
    link_id: LinkId
    parent_id: PersonId
    child_id: PersonId
    role: ParentRole
    relationship_type: RelationshipType
    family_unit_id: FamilyUnitId | None
    primary: bool


@dataclass(frozen=True, slots=True)
class RelationshipReference:
    reference_id: str
    source_person_id: PersonId
    target_person_id: PersonId
    family_unit_id: FamilyUnitId | None
    relationship_type: RelationshipType
    label: Literal["repeated_ancestor", "cross_family", "non_primary"]


@dataclass(frozen=True, slots=True)
class GraphComponent:
    component_id: str
    root_person_ids: tuple[PersonId, ...]
    person_ids: tuple[PersonId, ...]
    family_unit_ids: tuple[FamilyUnitId, ...]
    link_ids: tuple[LinkId, ...]
```

Component and reference IDs use full lowercase SHA-256 digests of canonical
UTF-8 strings, with no truncation or delimiter ambiguity:

```text
component_id = "cmp_" + sha256("|".join(sorted(person_ids))).hexdigest()
reference_id = "ref_" + sha256(
  canonical_json([source_person_id, target_person_id,
                  family_unit_id_or_empty, relationship_type, label])
).hexdigest()
```

Every component contains at least one person. `canonical_json` uses sorted keys,
ASCII escaping, and separators `(",", ":")`. All nested IDs are sorted.

No projected type contains contact data, source record IDs, audit metadata,
provider details, or Airtable IDs.

### Archived topology

The public projection begins with people whose `archived` flag is false.

- It omits every raw link whose parent or child is archived. It also omits a raw
  link whose non-null `family_unit_id` names any family unit omitted by this
  filter, even when that link's parent and child are both retained.
- It omits every family unit whose `adult_a_id` or `adult_b_id` is archived, plus
  all memberships and descendant edges for that omitted unit.
- It omits every reference whose source or target is archived or whose family
  unit was omitted.
- It recomputes components and roots after filtering; no public collection can
  contain a dangling person or family-unit ID.
- An unarchived child whose primary family unit is omitted remains visible and
  becomes a root unless another retained primary placement exists. The projector
  never rewrites that family as a synthetic single-parent relationship.
- Omitting any incident topology adds the allowlisted warning code
  `ARCHIVED_RELATIONSHIP_OMITTED` and makes the public status `partial`.

The graph-core `GraphSnapshot` is the admin-domain snapshot: it contains current
archived people, current unresolved annotations, and confirmation flags, but no
repository provenance or contact fields. The v2 API uses camel-case JSON and the
following exact strict DTOs; every object rejects unknown fields:

```typescript
type PublicIssueCode =
  | "DUPLICATE_UNRESOLVED_RELATIONSHIP"
  | "SUSPICIOUS_PARENT_AGE"
  | "ARCHIVED_RELATIONSHIP_OMITTED"
  | "GRAPH_WARNING";

type PublicGraphIssueDto = {
  code: PublicIssueCode;
  severity: "error" | "warning";
  message: string;
  personIds: PersonId[];
  familyUnitIds: FamilyUnitId[];
  linkIds: LinkId[];
};

type AdminPartialDateDto = {
  value: string;
  precision: "year" | "month" | "day";
};

type AdminPersonDto = {
  personId: PersonId;
  fullName: string;
  gender: Gender;
  birth: AdminPartialDateDto | null;
  death: AdminPartialDateDto | null;
  isAlive: boolean | null;
  primaryFamilyUnitId: FamilyUnitId | null;
  archived: boolean;
  versionRevision: number;
};

type AdminFamilyUnitDto = {
  familyUnitId: FamilyUnitId;
  kind: FamilyUnitKind;
  adultAId: PersonId;
  adultBId: PersonId | null;
  status: UnionStatus;
  start: AdminPartialDateDto | null;
  end: AdminPartialDateDto | null;
  distinctUnionConfirmed: boolean;
  createdRevision: number;
};

type AdminParentChildLinkDto = {
  linkId: LinkId;
  parentId: PersonId;
  childId: PersonId;
  role: ParentRole;
  relationshipType: RelationshipType;
  familyUnitId: FamilyUnitId | null;
  createdRevision: number;
};

type AdminUnresolvedRelationshipDto = {
  unresolvedId: UnresolvedRelationshipId;
  subjectPersonId: PersonId;
  kind: UnresolvedRelationshipKind;
  unresolvedName: string;
  createdRevision: number;
};

type AdminGraphSnapshot = {
  schemaVersion: "2";
  revision: number;
  semanticChecksum: string;
  people: AdminPersonDto[];
  familyUnits: AdminFamilyUnitDto[];
  parentChildLinks: AdminParentChildLinkDto[];
  unresolvedRelationships: AdminUnresolvedRelationshipDto[];
};
```

`headOperationId`, `fencingToken`, repository row IDs, source IDs, provider
metadata, and contact fields are not API fields. Public issue `message` is the
only user-facing text field: it is selected from `PUBLIC_ISSUE_MESSAGES` by
allowlisted `code`, never copied from `GraphIssue.message` or provider text. A
non-allowlisted warning maps to allowlisted code `GRAPH_WARNING` and its fixed
generic message; blocking errors prevent projection. There is no public `copy`,
`rawMessage`, or `internalMessage` field.

The public warning allowlist is exact:

```text
DUPLICATE_UNRESOLVED_RELATIONSHIP = "Some relationships need review."
SUSPICIOUS_PARENT_AGE = "Some dates may need review."
ARCHIVED_RELATIONSHIP_OMITTED = "Some relationships are hidden because an archived person is involved."
GRAPH_WARNING = "Some family-tree details need review."
```

## 3. Unresolved Relationship Annotations

Unresolved names are first-class annotations, never synthetic people or graph
edges.

Task 1 adds `UnresolvedRelationshipId = NewType("UnresolvedRelationshipId",
str)` with the `unr_` prefix and new/migrated UUID factories matching the other
stable ID rules. Task 2 owns the annotation model, snapshot collection, and
commands. `GraphSnapshot.unresolved` is an immutable
`Mapping[UnresolvedRelationshipId, UnresolvedRelationship]`, not a history list.

The migrated factory accepts
`(source_table, source_record_id, source_relation_slot)`. The slot is a non-empty,
case-sensitive mapper-owned field/ordinal discriminator such as `FatherName#0`,
`MotherName#0`, or `SpouseName#0`. Its UUID5 name is the compact ASCII JSON array
`["shajra","unresolved","v1",source_table,source_record_id,source_relation_slot]`
encoded with separators `(",", ":")`; no delimiter-joined string is used. The
current `ApprovedMembers` schema has scalar `FatherName`, `MotherName`, and
`SpouseName` fields, so each uses ordinal `#0`; no delimiter splitting or
synthetic plural spouse field is permitted. Every relation from one row has a
distinct ID and an unchanged row produces the same IDs on every migration rerun.

The immutable model is:

```python
class UnresolvedRelationshipKind(StrEnum):
    FATHER = "father"
    MOTHER = "mother"
    PARENT = "parent"
    PARTNER = "partner"


@dataclass(frozen=True, slots=True)
class UnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
    subject_person_id: PersonId
    kind: UnresolvedRelationshipKind
    unresolved_name: str
    created_revision: int = 0
```

Names are trimmed and must be non-empty. The subject person must exist. Duplicate
keys are `(subject_person_id, kind, casefolded_whitespace_normalized_name)` and
produce a deterministic `DUPLICATE_UNRESOLVED_RELATIONSHIP` warning.

The command union includes these exact payloads:

```python
@dataclass(frozen=True, slots=True)
class AddUnresolvedRelationship:
    annotation: UnresolvedRelationship


@dataclass(frozen=True, slots=True)
class SupersedeUnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
    replacement: UnresolvedRelationship


@dataclass(frozen=True, slots=True)
class RemoveUnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
```

Add rejects an existing ID. Supersede requires an existing target and a
replacement with the same `unresolved_id`; it replaces the current map value.
Remove requires an existing target and removes it from the current snapshot.
Repository version/tombstone rows retain history, but only the highest committed,
non-removed value enters `GraphSnapshot`, duplicate detection,
`unresolved_count`, admin-domain output, and the semantic checksum.

Public projection exposes only `unresolved_count` and allowlisted issue codes.
Raw unresolved names remain in the admin-domain `GraphSnapshot` and migration
review surfaces.

Any unresolved annotation makes an otherwise valid public projection `partial`;
it never creates a connector or changes ancestry roots.

## 4. Explicit Historical Unions

`FamilyUnit` adds this semantic field:

```python
distinct_union_confirmed: bool = False
```

The normalized family key is the canonical adult pair after sorting adult IDs;
a single-parent unit uses `(adult_a_id, None)`. The comparison population is
every family unit in the current committed `GraphSnapshot.family_units` map,
regardless of status, start date, end date, divorce, separation, or widowhood.
Repository versions superseded at or before the committed revision are not
separate snapshot entries: `SupersedeFamilyUnit` replaces the current value under
the same stable `family_unit_id`, while the repository retains prior versions.

If more than one current unit has the same normalized key, every unit in that
duplicate group must have `distinct_union_confirmed=True`. Otherwise validation
emits the blocking `DUPLICATE_FAMILY_UNIT` issue. Divorced, widowed, separated,
ended, and unknown-status units cannot bypass this rule.

Confirmation is an explicit graph mutation reviewed in preview. Services must
not infer confirmation from dates, statuses, notes, IDs, or record order. The
flag is included in semantic checksums and admin DTOs. It is not required in the
public family-unit DTO.

This permits remarriage between the same adults while making accidental duplicate
unions fail closed.

## 5. Provenance and Semantic Checksums

Airtable record IDs, migration run IDs, provider metadata, and audit timestamps
belong to repository row wrappers and commit/audit records. They are not fields
on `Person`, `FamilyUnit`, `ParentChildLink`, `UnresolvedRelationship`, or
`TreeProjection`.

`semantic_checksum(snapshot: GraphSnapshot)` is the only checksum entry point.
It includes stable IDs and every current family semantic, including normalized
unresolved annotation names and `distinct_union_confirmed`. It excludes:

- graph revision
- head operation ID
- fencing token
- any previously stored checksum
- repository source IDs and migration IDs
- audit timestamps and delivery metadata
- every entity `created_revision` or `version_revision`

The graph-core provenance test is replaced with a pure-domain test proving that
identical graph maps with different `GraphState` revision, operation, fencing,
stored-checksum, and entity revision metadata produce the same checksum.
Repository mapper tests in the persistence plan separately prove that different
source record IDs map to the same domain checksum.

`project_graph` computes the domain checksum from its input snapshot and copies
that lowercase SHA-256 value into `TreeProjection.semantic_checksum`. A
`TreeProjection` is never accepted by `semantic_checksum`; therefore public
privacy filtering and hidden unresolved text cannot create a second checksum
meaning. Tests assert
`project_graph(snapshot).semantic_checksum == semantic_checksum(snapshot)`.

## 6. Determinism and Validation

- Canonical adult ordering is by `PersonId`.
- People, units, links, unresolved annotations, rendered edges, references, and
  components are sorted by stable semantic IDs.
- Input mapping order cannot change projection dictionaries or checksums.
- Raw parent-child records remain unique by
  `(parent_id, child_id, relationship_type)`.
- Guardian links never participate in ancestry or canonical descendant edges.
- Biological, adoptive, step, and unknown primary links remain cycle-checked.
- A blocking validation report prevents a ready projection; allowlisted warnings,
  archived-topology omissions, and unresolved annotations yield `partial`.

## 7. Required Tests

The amended graph plans must include tests proving:

1. A two-parent/one-child unit emits two adult memberships, one descendant edge,
   and two raw parent-child records.
2. The frontend consumes canonical edge arrays and does not group raw links.
3. Ten insertion-order shuffles produce identical projection dictionaries and
   checksums.
4. Unresolved IDs are stable and distinct across father, mother, and every
   partner slot from one legacy row and across idempotent reruns;
   add/supersede/remove commands have the exact current-map lifecycle; names
   never create nodes or edges; public output omits raw text.
5. Duplicate adult pairs fail unless every current unit is explicitly confirmed,
   including divorced, widowed, separated, ended, and unknown-status units;
   superseded repository versions do not appear as duplicate snapshot entries.
6. Confirmation changes the semantic checksum, while graph/entity revision
   metadata does not.
7. The projected checksum equals the input snapshot checksum, is never hashed
   recursively, and passing a projection to `semantic_checksum` raises
   `TypeError`.
8. Repository source IDs do not enter domain objects or affect checksums.
9. Archived people cannot leave dangling public links, units, edges, references,
   or components; both raw parent links are omitted when a two-adult family is
   filtered because one adult is archived, while the affected unarchived child
   remains visible in a partial graph.
10. Repeated ancestors appear once as a primary person plus deterministic
    references.
11. Public issue and admin snapshot objects have exactly the key sets above;
    code/message mismatches and unknown, private, or provenance fields are
    rejected by backend and frontend schemas.

## 8. Sequencing

The graph-core, backend-persistence, and frontend plans are amended in the same
contract change wherever their DTOs, commands, repositories, tests, or layout
logic consume this design. No graph implementation begins from the superseded
plan text.
