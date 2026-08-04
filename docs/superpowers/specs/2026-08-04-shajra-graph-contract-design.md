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

- `parent_child_links` contains sanitized underlying relationship records. It is
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

Component and reference IDs are deterministic strings derived from sorted stable
application IDs. All nested IDs are sorted. Archived people are excluded from
the public projection but remain available to the admin snapshot.

No projected type contains contact data, source record IDs, audit metadata,
provider details, or Airtable IDs.

## 3. Unresolved Relationship Annotations

Unresolved names are first-class annotations, never synthetic people or graph
edges.

Task 1 adds `UnresolvedRelationshipId = NewType("UnresolvedRelationshipId",
str)` with the `unr_` prefix and new/migrated UUID factories matching the other
stable ID rules.

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

The command union includes `AddUnresolvedRelationship` and
`SupersedeUnresolvedRelationship`. Public projection exposes only
`unresolved_count` and sanitized issue codes. Raw unresolved names remain in the
admin snapshot and migration review surfaces.

Any unresolved annotation makes an otherwise valid public projection `partial`;
it never creates a connector or changes ancestry roots.

## 4. Explicit Historical Unions

`FamilyUnit` adds this semantic field:

```python
distinct_union_confirmed: bool = False
```

The normalized family key is the canonical adult pair after sorting adult IDs;
a single-parent unit uses `(adult_a_id, None)`. If more than one active family
unit has the same normalized key, every unit in that duplicate group must have
`distinct_union_confirmed=True`. Otherwise validation emits the blocking
`DUPLICATE_FAMILY_UNIT` issue.

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

`semantic_checksum` includes stable IDs and every family semantic, including
unresolved annotations and `distinct_union_confirmed`. It excludes:

- graph revision
- head operation ID
- fencing token
- any previously stored checksum
- repository source IDs and migration IDs
- audit timestamps and delivery metadata

The graph-core provenance test is replaced with a pure-domain test proving that
identical graph maps with different `GraphState` revision, operation, fencing,
and stored-checksum values produce the same checksum. Repository mapper tests in
the persistence plan separately prove that different source record IDs map to
the same domain checksum.

`TreeProjection` receives the final lowercase SHA-256 checksum after canonical
serialization. The projection's `semantic_checksum` field is omitted when a
projection itself is serialized for hashing, preventing self-reference.

## 6. Determinism and Validation

- Canonical adult ordering is by `PersonId`.
- People, units, links, unresolved annotations, rendered edges, references, and
  components are sorted by stable semantic IDs.
- Input mapping order cannot change projection dictionaries or checksums.
- Raw parent-child records remain unique by
  `(parent_id, child_id, relationship_type)`.
- Guardian links never participate in ancestry or canonical descendant edges.
- Biological, adoptive, step, and unknown primary links remain cycle-checked.
- A blocking validation report prevents a ready projection; warnings and
  unresolved annotations yield `partial`.

## 7. Required Tests

The amended graph plans must include tests proving:

1. A two-parent/one-child unit emits two adult memberships, one descendant edge,
   and two raw parent-child records.
2. The frontend consumes canonical edge arrays and does not group raw links.
3. Ten insertion-order shuffles produce identical projection dictionaries and
   checksums.
4. Unresolved IDs are stable, commands are immutable, names never create nodes or
   edges, and public output omits raw unresolved text.
5. Duplicate adult pairs fail unless every unit is explicitly confirmed distinct.
6. Confirmation changes the semantic checksum.
7. Repository source IDs do not enter domain objects or affect checksums.
8. Repeated ancestors appear once as a primary person plus deterministic
   references.

## 8. Sequencing

The graph-core plan is amended first, followed by backend persistence and frontend
plans wherever their DTOs or tests consume this contract. No graph implementation
begins from the contradictory plan text.
