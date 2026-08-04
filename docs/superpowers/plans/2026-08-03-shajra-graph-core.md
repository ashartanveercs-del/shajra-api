# Shajra Graph Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and exhaustively test the immutable family-graph domain, invariant validator, deterministic projection, and semantic checksum without any Airtable or network dependency.

**Architecture:** Model people, family units, and parent-child links as immutable Python dataclasses with stable application IDs. Commands produce proposed snapshots, validation returns structured issues without side effects, and projection turns a valid component-aware ancestry DAG into a stable public contract with repeated paths represented as references.

**Tech Stack:** Python 3.12, standard-library dataclasses, Pydantic 2 DTO boundaries, pytest 9.1.1, Ruff 0.16.1, mypy 2.3.0.

## Global Constraints

- Complete the platform recovery plan first.
- This plan performs no Airtable, Vercel, Cloudinary, Upstash, GitHub, or production mutations.
- Domain modules import no FastAPI, pyairtable, requests, Groq, Cloudinary, or Upstash code.
- Application IDs use prefixed UUID4 for new records and deterministic UUID5 for migration.
- All ancestry-bearing links are acyclic; guardian links are non-ancestry references.
- Every child has zero or one `PrimaryFamilyUnitId` across all relationship types.
- Ambiguous names are annotations only and never create links.
- Projection and checksums are stable regardless of input dictionary order.
- Every task follows red-green-refactor and ends with a focused local commit.

---

## File Structure

Create:

- `backend/domain/__init__.py`: public domain exports.
- `backend/domain/ids.py`: stable ID types and factories.
- `backend/domain/dates.py`: partial-date value object and comparisons.
- `backend/domain/models.py`: immutable graph entities and snapshot.
- `backend/domain/commands.py`: mutation command types and pure reducer.
- `backend/domain/issues.py`: stable issue codes and validation report.
- `backend/domain/validation.py`: whole-snapshot invariant validation.
- `backend/domain/projection.py`: deterministic component and reference projection.
- `backend/domain/checksum.py`: canonical semantic serialization and SHA-256.
- `backend/tests/fixtures/graphs.py`: reusable graph factories.
- `backend/tests/unit/domain/test_ids.py`
- `backend/tests/unit/domain/test_dates.py`
- `backend/tests/unit/domain/test_commands.py`
- `backend/tests/unit/domain/test_validation.py`
- `backend/tests/unit/domain/test_projection.py`
- `backend/tests/unit/domain/test_checksum.py`

## Interfaces

```python
PersonId = NewType("PersonId", str)
FamilyUnitId = NewType("FamilyUnitId", str)
LinkId = NewType("LinkId", str)
UnresolvedRelationshipId = NewType("UnresolvedRelationshipId", str)
OperationId = NewType("OperationId", str)
MigrationRunId = NewType("MigrationRunId", str)

def new_person_id() -> PersonId: ...
def migrated_person_id(source_table: str, source_record_id: str) -> PersonId: ...
```

```python
@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    state: GraphState
    people: Mapping[PersonId, Person]
    family_units: Mapping[FamilyUnitId, FamilyUnit]
    links: Mapping[LinkId, ParentChildLink]
    unresolved: Mapping[UnresolvedRelationshipId, UnresolvedRelationship]

def apply_commands(snapshot: GraphSnapshot, commands: Sequence[GraphCommand]) -> GraphSnapshot: ...
def validate_snapshot(snapshot: GraphSnapshot) -> ValidationReport: ...
def project_graph(snapshot: GraphSnapshot) -> TreeProjection: ...
def semantic_checksum(snapshot: GraphSnapshot) -> str: ...
```

### Task 1: Stable IDs and Partial Dates

**Files:**
- Create: `backend/domain/ids.py`
- Create: `backend/domain/dates.py`
- Create: `backend/tests/unit/domain/test_ids.py`
- Create: `backend/tests/unit/domain/test_dates.py`

**Interfaces:**
- Produces: prefixed ID factories and `PartialDate` consumed by all later tasks.

- [ ] **Step 1: Write failing ID tests**

Create `backend/tests/unit/domain/test_ids.py`:

```python
from domain.ids import (
    migrated_person_id,
    new_family_unit_id,
    new_person_id,
    new_unresolved_relationship_id,
)


def test_new_ids_have_type_prefixes_and_are_unique():
    first = new_person_id()
    second = new_person_id()
    assert str(first).startswith("per_")
    assert first != second
    assert str(new_family_unit_id()).startswith("fam_")
    assert str(new_unresolved_relationship_id()).startswith("unr_")


def test_migrated_ids_are_deterministic_and_table_scoped():
    first = migrated_person_id("ApprovedMembers", "rec123")
    assert first == migrated_person_id("ApprovedMembers", "rec123")
    assert first != migrated_person_id("PendingSubmissions", "rec123")
```

- [ ] **Step 2: Run ID tests and confirm failure**

Run: `python -m pytest tests/unit/domain/test_ids.py -q`

Expected: FAIL because `domain.ids` does not exist.

- [ ] **Step 3: Implement ID factories**

Write `backend/domain/ids.py`:

```python
from typing import NewType
from uuid import NAMESPACE_URL, uuid4, uuid5

PersonId = NewType("PersonId", str)
FamilyUnitId = NewType("FamilyUnitId", str)
LinkId = NewType("LinkId", str)
UnresolvedRelationshipId = NewType("UnresolvedRelationshipId", str)
OperationId = NewType("OperationId", str)
MigrationRunId = NewType("MigrationRunId", str)


def _new(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _migrated(prefix: str, source_table: str, source_record_id: str) -> str:
    value = uuid5(NAMESPACE_URL, f"shajra:{source_table}:{source_record_id}")
    return f"{prefix}_{value.hex}"


def new_person_id() -> PersonId:
    return PersonId(_new("per"))


def new_family_unit_id() -> FamilyUnitId:
    return FamilyUnitId(_new("fam"))


def new_link_id() -> LinkId:
    return LinkId(_new("lnk"))


def new_unresolved_relationship_id() -> UnresolvedRelationshipId:
    return UnresolvedRelationshipId(_new("unr"))


def new_operation_id() -> OperationId:
    return OperationId(_new("op"))


def migrated_person_id(source_table: str, source_record_id: str) -> PersonId:
    return PersonId(_migrated("per", source_table, source_record_id))
```

Add the remaining deterministic factories with these exact prefixes:

```python
def migrated_family_unit_id(source_table: str, source_record_id: str) -> FamilyUnitId:
    return FamilyUnitId(_migrated("fam", source_table, source_record_id))


def migrated_link_id(source_table: str, source_record_id: str) -> LinkId:
    return LinkId(_migrated("lnk", source_table, source_record_id))


def migrated_unresolved_relationship_id(
    source_table: str, source_record_id: str
) -> UnresolvedRelationshipId:
    return UnresolvedRelationshipId(_migrated("unr", source_table, source_record_id))


def migrated_operation_id(source_table: str, source_record_id: str) -> OperationId:
    return OperationId(_migrated("op", source_table, source_record_id))


def migrated_run_id(source_table: str, source_record_id: str) -> MigrationRunId:
    return MigrationRunId(_migrated("mig", source_table, source_record_id))
```

- [ ] **Step 4: Write failing partial-date tests**

Create `backend/tests/unit/domain/test_dates.py`:

```python
import pytest

from domain.dates import DatePrecision, PartialDate


@pytest.mark.parametrize(
    ("raw", "precision"),
    [("1960", DatePrecision.YEAR), ("1960-04", DatePrecision.MONTH), ("1960-04-23", DatePrecision.DAY)],
)
def test_parse_supported_partial_dates(raw, precision):
    assert PartialDate.parse(raw).precision is precision


def test_rejects_invalid_calendar_date():
    with pytest.raises(ValueError):
        PartialDate.parse("2024-02-31")


def test_precision_aware_ordering_uses_possible_ranges():
    year = PartialDate.parse("1960")
    next_year = PartialDate.parse("1961")
    assert year.latest < next_year.earliest
```

- [ ] **Step 5: Implement normalized partial dates**

Write `backend/domain/dates.py` with:

```python
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import calendar
import re


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class PartialDate:
    value: str
    precision: DatePrecision
    earliest: date
    latest: date

    @classmethod
    def parse(cls, raw: str) -> "PartialDate":
        if re.fullmatch(r"\d{4}", raw):
            year = int(raw)
            return cls(raw, DatePrecision.YEAR, date(year, 1, 1), date(year, 12, 31))
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            year, month = map(int, raw.split("-"))
            last = calendar.monthrange(year, month)[1]
            return cls(raw, DatePrecision.MONTH, date(year, month, 1), date(year, month, last))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed = date.fromisoformat(raw)
            return cls(raw, DatePrecision.DAY, parsed, parsed)
        raise ValueError("Date must be YYYY, YYYY-MM, or YYYY-MM-DD")
```

Legacy `DD-MM-YYYY` parsing belongs in the migration adapter, not this domain type.

- [ ] **Step 6: Run and commit**

```powershell
python -m pytest tests/unit/domain/test_ids.py tests/unit/domain/test_dates.py -q
ruff check domain/ids.py domain/dates.py tests/unit/domain
mypy domain/ids.py domain/dates.py
git add backend/domain backend/tests/unit/domain
git commit -m "feat: add stable Shajra IDs and partial dates"
```

### Task 2: Immutable Graph Models and Commands

**Files:**
- Create: `backend/domain/models.py`
- Create: `backend/domain/commands.py`
- Create: `backend/domain/__init__.py`
- Create: `backend/tests/unit/domain/test_commands.py`
- Create: `backend/tests/fixtures/graphs.py`

**Interfaces:**
- Consumes: ID and date types from Task 1.
- Produces: immutable entities, `GraphSnapshot`, commands, and `apply_commands`.

- [ ] **Step 1: Write a failing command-reducer test**

Create `backend/tests/unit/domain/test_commands.py`:

```python
from domain.commands import AddParentChildLink, apply_commands
from domain.models import ParentChildLink, ParentRole, RelationshipType
from tests.fixtures.graphs import simple_parent_child_snapshot


def test_add_link_returns_a_new_snapshot_without_mutating_input():
    snapshot, parent, child, link_id = simple_parent_child_snapshot(include_link=False)
    link = ParentChildLink(
        link_id=link_id,
        parent_id=parent,
        child_id=child,
        role=ParentRole.PARENT,
        relationship_type=RelationshipType.BIOLOGICAL,
        family_unit_id=None,
        created_revision=1,
    )
    result = apply_commands(snapshot, [AddParentChildLink(link)])
    assert link_id not in snapshot.links
    assert result.links[link_id] == link
```

In the same file, add red-green tests for unresolved add, same-ID supersede, and
remove. Each command must return a new snapshot, leave the input map unchanged,
and raise `CommandConflict` for duplicate, missing, or replacement-ID mismatch.
Add a family-unit supersede test proving the replacement occupies the same stable
map key and the old version does not coexist in the current snapshot.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python -m pytest tests/unit/domain/test_commands.py -q`

Expected: FAIL because models and commands do not exist.

- [ ] **Step 3: Implement immutable entity enums and dataclasses**

In `backend/domain/models.py`, define `Gender`, `FamilyUnitKind`, `UnionStatus`,
`ParentRole`, `RelationshipType`, and `UnresolvedRelationshipKind` as `StrEnum`.
The unresolved kinds are exactly `father`, `mother`, `parent`, and `partner`.
Define:

```python
@dataclass(frozen=True, slots=True)
class Person:
    person_id: PersonId
    full_name: str
    gender: Gender = Gender.UNKNOWN
    birth: PartialDate | None = None
    death: PartialDate | None = None
    is_alive: bool | None = None
    primary_family_unit_id: FamilyUnitId | None = None
    archived: bool = False
    version_revision: int = 0


@dataclass(frozen=True, slots=True)
class FamilyUnit:
    family_unit_id: FamilyUnitId
    kind: FamilyUnitKind
    adult_a_id: PersonId
    adult_b_id: PersonId | None = None
    status: UnionStatus = UnionStatus.UNKNOWN
    start: PartialDate | None = None
    end: PartialDate | None = None
    distinct_union_confirmed: bool = False
    created_revision: int = 0


@dataclass(frozen=True, slots=True)
class ParentChildLink:
    link_id: LinkId
    parent_id: PersonId
    child_id: PersonId
    role: ParentRole
    relationship_type: RelationshipType
    family_unit_id: FamilyUnitId | None
    created_revision: int = 0


@dataclass(frozen=True, slots=True)
class UnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
    subject_person_id: PersonId
    kind: UnresolvedRelationshipKind
    unresolved_name: str
    created_revision: int = 0


@dataclass(frozen=True, slots=True)
class GraphState:
    revision: int
    head_operation_id: OperationId | None
    fencing_token: int
    semantic_checksum: str


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    state: GraphState
    people: Mapping[PersonId, Person]
    family_units: Mapping[FamilyUnitId, FamilyUnit]
    links: Mapping[LinkId, ParentChildLink]
    unresolved: Mapping[UnresolvedRelationshipId, UnresolvedRelationship]
```

Use `MappingProxyType(dict(...))` for all four maps in
`GraphSnapshot.__post_init__` so nested maps cannot be mutated through a frozen
dataclass. Normalize `UnresolvedRelationship.unresolved_name` by trimming outer
whitespace and collapsing internal whitespace; reject an empty result.

- [ ] **Step 4: Implement explicit graph commands and reducer**

In `backend/domain/commands.py`, define commands for adding/updating person
versions, creating/superseding family units and links, setting primary placement,
and archiving people. Use a closed type alias:

```python
GraphCommand = (
    AddPersonVersion
    | AddFamilyUnit
    | AddParentChildLink
    | AddUnresolvedRelationship
    | SetPrimaryFamilyUnit
    | ArchivePerson
    | SupersedeFamilyUnit
    | SupersedeParentChildLink
    | SupersedeUnresolvedRelationship
    | RemoveUnresolvedRelationship
)
```

`AddUnresolvedRelationship` contains `annotation`. Supersede contains
`unresolved_id` plus `replacement` and requires matching stable IDs. Remove
contains `unresolved_id`. Add rejects a duplicate ID; supersede/remove reject a
missing target. Supersede replaces and remove deletes the current map value.

`SupersedeFamilyUnit` and `SupersedeParentChildLink` replace the current value
under the same stable logical ID; prior versions belong to repository history and
do not coexist in `GraphSnapshot`.

`apply_commands` copies the four maps, applies commands in order, and returns a
new snapshot. It raises `CommandConflict` for duplicate IDs, missing targets, or
replacement-ID mismatches; it does not run semantic validation.

- [ ] **Step 5: Build reusable fixtures**

In `backend/tests/fixtures/graphs.py`, provide factories with stable literal IDs:

```python
PARENT = PersonId("per_parent")
CHILD = PersonId("per_child")
FAMILY = FamilyUnitId("fam_primary")
```

Implement these exact fixture contracts without calling production ID factories:

- `empty_snapshot(revision=0)` returns empty maps and a `GraphState` at that revision.
- `simple_parent_child_snapshot(include_link=True)` returns the snapshot plus
  `PARENT`, `CHILD`, and `LinkId("lnk_parent_child")`; omitting the link changes
  no other fixture data.
- `two_parent_family_snapshot()` contains two adults, one union, one child, two
  biological links, and that union as the child's primary family unit.
- `remarriage_snapshot()` contains one adult in two distinct canonical unions and
  one child per union.
- `cousin_union_snapshot()` is acyclic but contains a repeated ancestor path.
- `repeated_ancestor_snapshot()` contains one person reachable by two valid paths
  so projection must emit one person plus a reference.

Every fixture initializes the unresolved map explicitly. Add
`duplicate_historical_union_snapshot(confirmed, status, ended)` and an archived
topology fixture for the validation/projection tasks.

- [ ] **Step 6: Run and commit**

```powershell
python -m pytest tests/unit/domain/test_commands.py -q
ruff check domain tests/fixtures tests/unit/domain/test_commands.py
mypy domain
git add backend/domain backend/tests
git commit -m "feat: model immutable Shajra graph commands"
```

### Task 3: Structured Invariant Validation

**Files:**
- Create: `backend/domain/issues.py`
- Create: `backend/domain/validation.py`
- Create: `backend/tests/unit/domain/test_validation.py`

**Interfaces:**
- Consumes: `GraphSnapshot`.
- Produces: `validate_snapshot(snapshot) -> ValidationReport`.

- [ ] **Step 1: Write the invariant matrix as failing tests**

Parametrize tests for these stable codes:

```python
SELF_PARENT = "SELF_PARENT"
SELF_PARTNER = "SELF_PARTNER"
MISSING_PERSON = "MISSING_PERSON"
MISSING_FAMILY_UNIT = "MISSING_FAMILY_UNIT"
DUPLICATE_LINK = "DUPLICATE_LINK"
DUPLICATE_FAMILY_UNIT = "DUPLICATE_FAMILY_UNIT"
DUPLICATE_UNRESOLVED_RELATIONSHIP = "DUPLICATE_UNRESOLVED_RELATIONSHIP"
ANCESTRY_CYCLE = "ANCESTRY_CYCLE"
PRIMARY_UNIT_MISMATCH = "PRIMARY_UNIT_MISMATCH"
FAMILY_UNIT_PARENT_MISMATCH = "FAMILY_UNIT_PARENT_MISMATCH"
DEATH_BEFORE_BIRTH = "DEATH_BEFORE_BIRTH"
UNION_END_BEFORE_START = "UNION_END_BEFORE_START"
IMPOSSIBLE_PARENT_AGE = "IMPOSSIBLE_PARENT_AGE"
SUSPICIOUS_PARENT_AGE = "SUSPICIOUS_PARENT_AGE"
ARCHIVED_RELATIONSHIP_OMITTED = "ARCHIVED_RELATIONSHIP_OMITTED"
```

Include this cycle test:

```python
def test_non_primary_adoptive_cycle_is_blocking():
    report = validate_snapshot(adoptive_cycle_snapshot())
    assert report.has_errors
    assert "ANCESTRY_CYCLE" in {issue.code for issue in report.issues}
```

Include a guardian-cycle test that asserts no ancestry-cycle issue.

- [ ] **Step 2: Run validation tests and confirm failure**

Run: `python -m pytest tests/unit/domain/test_validation.py -q`

Expected: FAIL because validator types do not exist.

- [ ] **Step 3: Implement structured issues**

In `backend/domain/issues.py`:

```python
class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class GraphIssue:
    code: str
    severity: IssueSeverity
    message: str
    person_ids: tuple[PersonId, ...] = ()
    family_unit_ids: tuple[FamilyUnitId, ...] = ()
    link_ids: tuple[LinkId, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[GraphIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)
```

- [ ] **Step 4: Implement the validator in deterministic passes**

`validate_snapshot` must run these passes and sort the final issues by severity,
code, and affected IDs:

1. Reference existence and self-links.
2. Duplicate normalized keys. Raw links use
   `(parent_id, child_id, relationship_type)`. Unresolved annotations use
   `(subject_person_id, kind, casefolded_whitespace_normalized_name)` and produce
   a warning. Family units group every current snapshot entry by canonical adult
   pair regardless of status/dates; a group larger than one is blocking unless
   every unit has `distinct_union_confirmed=True`.
3. Family-unit shape and parent membership.
4. Exactly-zero-or-one primary placement.
5. DFS cycle detection over biological, adoptive, step, and unknown links.
6. Person, parent-child, and family-unit chronology.

Add tests proving divorced, widowed, separated, ended, and unknown-status units
cannot bypass duplicate-pair confirmation. Add a reducer-plus-validator test
proving a superseded repository version is represented by one replacement under
the same stable ID and therefore is not a second snapshot unit. Add unresolved
subject-existence, normalized-duplicate warning, and add/supersede/remove
lifecycle tests.

Use interval comparisons for partial dates. Block only impossible ordering, such
as a parent's latest possible birth occurring after the child's earliest possible
birth, or a biological parent's latest possible death occurring before the
child's earliest possible conception window. A possible parent age under 10 or
over 80 is always a warning, never a blocking issue.

- [ ] **Step 5: Run the validator gate and commit**

```powershell
python -m pytest tests/unit/domain/test_validation.py -q
ruff check domain/issues.py domain/validation.py tests/unit/domain/test_validation.py
mypy domain
git add backend/domain backend/tests/unit/domain backend/tests/fixtures/graphs.py
git commit -m "feat: validate Shajra graph invariants"
```

### Task 4: Deterministic Projection and References

**Files:**
- Create: `backend/domain/projection.py`
- Create: `backend/tests/unit/domain/test_projection.py`

**Interfaces:**
- Consumes: a snapshot with no blocking validation issues.
- Produces: `project_graph(snapshot) -> TreeProjection`.

- [ ] **Step 1: Write projection contract tests**

Cover single parent, two parents, remarriage, cousin union, repeated ancestor,
partner-only component, multiple roots, and disconnected components. The repeated
ancestor assertion must be:

```python
projection = project_graph(repeated_ancestor_snapshot())
primary_ids = [person.person_id for person in projection.people]
assert len(primary_ids) == len(set(primary_ids))
assert projection.references
assert projection.references[0].target_person_id in set(primary_ids)
```

For the two-parent fixture, assert two raw links, two adult memberships, and
exactly one descendant edge. Assert canonical edge IDs use the exact `adult:` and
`child:` formats. Also shuffle all four input-map insertion orders ten times and
assert identical projection dictionaries, component IDs, and reference IDs.

- [ ] **Step 2: Run projection tests and confirm failure**

Run: `python -m pytest tests/unit/domain/test_projection.py -q`

Expected: FAIL because projection does not exist.

- [ ] **Step 3: Implement public projection dataclasses**

Define these exact public projection records:

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


@dataclass(frozen=True, slots=True)
class TreeProjection:
    schema_version: Literal["2"]
    revision: int
    semantic_checksum: str
    status: Literal["ready", "empty", "partial", "unavailable"]
    people: tuple[ProjectedPerson, ...]
    family_units: tuple[ProjectedFamilyUnit, ...]
    parent_child_links: tuple[ProjectedLink, ...]
    adult_memberships: tuple[AdultMembershipEdge, ...]
    descendant_edges: tuple[DescendantEdge, ...]
    references: tuple[RelationshipReference, ...]
    components: tuple[GraphComponent, ...]
    issues: tuple[GraphIssue, ...]
    unresolved_count: int
```

Set raw-link `primary` only when the child's `primary_family_unit_id` equals the
link's non-null `family_unit_id`. It is relationship detail, not a rendering
instruction. `adult_memberships` and `descendant_edges` are the only authoritative
rendering topology. No projected type contains email, phone, unresolved text,
source record ID, audit data, provider text, or Airtable ID.

- [ ] **Step 4: Implement component-aware DAG traversal**

Build indexes from ID-sorted tuples and validate before projection. Then apply the
public archived-topology filter from the graph-contract design: omit archived
people, incident links, units with archived adults, their canonical edges, and
references with hidden endpoints; recompute roots/components afterward. Retained
children from omitted units remain visible roots. Emit the allowlisted warning
`ARCHIVED_RELATIONSHIP_OMITTED` and status `partial` when incident topology is
omitted.

For each retained family unit, emit one adult membership per present adult with
IDs `adult:{family_unit_id}:{person_id}`. Group retained primary raw links by
`(family_unit_id, child_id)` and emit exactly one descendant edge per group with
ID `child:{family_unit_id}:{child_id}`. Conflicting unit/child membership is a
blocking validation error, not a silently chosen edge. Raw links remain separate
detail records and are never rendered one-for-one.

Calculate weakly connected components over retained people, family units, and
canonical edges. Within each component, roots are retained people with no
retained primary ancestry placement. Expand a person once; later paths emit
`RelationshipReference`. A single-parent family unit is a real unit, not a fake
spouse.

Use full SHA-256 IDs exactly as specified in the graph-contract design:
`cmp_` hashes sorted component person IDs; `ref_` hashes canonical JSON containing
source, target, optional unit, relationship type, and label. Sort every output
collection and every nested ID tuple.

Raise `InvalidGraphProjection(report)` when blocking issues exist. For allowlisted
warnings, archived-topology omissions, or unresolved annotations, return status
`partial`. Public issues contain only code, severity, affected public application
IDs, and fixed allowlisted copy. Copy `semantic_checksum(snapshot)` into the
projection; do not hash a projection.

Add archived fixtures/tests proving there are no dangling public IDs, hidden
incident topology is omitted, an affected retained child remains reachable as a
root, the status is partial, and the admin-domain snapshot remains unchanged.

- [ ] **Step 5: Run and commit**

```powershell
python -m pytest tests/unit/domain/test_projection.py -q
ruff check domain/projection.py tests/unit/domain/test_projection.py
mypy domain
git add backend/domain/projection.py backend/tests/unit/domain/test_projection.py backend/tests/fixtures/graphs.py
git commit -m "feat: project deterministic Shajra ancestry DAG"
```

### Task 5: Canonical Semantic Checksum

**Files:**
- Create: `backend/domain/checksum.py`
- Create: `backend/tests/unit/domain/test_checksum.py`
- Modify: `backend/domain/projection.py`

**Interfaces:**
- Consumes: `GraphSnapshot` only.
- Produces: lowercase SHA-256 `semantic_checksum`.

- [ ] **Step 1: Write failing checksum tests**

```python
def test_checksum_ignores_mapping_insertion_order():
    assert semantic_checksum(snapshot_order_a()) == semantic_checksum(snapshot_order_b())


def test_checksum_changes_for_a_real_relationship_change():
    assert semantic_checksum(before_snapshot()) != semantic_checksum(after_snapshot())


def test_checksum_ignores_state_and_entity_revision_metadata():
    assert semantic_checksum(snapshot_revision_a()) == semantic_checksum(snapshot_revision_b())


def test_projection_carries_the_snapshot_checksum():
    snapshot = two_parent_family_snapshot()[0]
    assert project_graph(snapshot).semantic_checksum == semantic_checksum(snapshot)


def test_confirmed_historical_union_changes_checksum():
    assert semantic_checksum(unconfirmed_union()) != semantic_checksum(confirmed_union())
```

- [ ] **Step 2: Implement canonical serialization**

Serialize only semantic fields into ID-sorted dictionaries, use JSON separators
`(",", ":")`, UTF-8, and `sort_keys=True`:

```python
def sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Exclude graph revision, operation ID, fencing token, stored checksum, every entity
`created_revision`/`version_revision`, source record IDs, migration IDs, and audit
timestamps. Include stable IDs and all current family semantics, including
normalized unresolved annotations and `distinct_union_confirmed`.

`semantic_checksum` must reject non-`GraphSnapshot` inputs. `project_graph`
computes the snapshot checksum once and copies it into the projection, avoiding a
second public-projection checksum meaning and any recursive checksum field.

- [ ] **Step 3: Run all graph-core gates**

```powershell
python -m pytest tests/unit/domain -q --cov=domain --cov-report=term-missing
ruff check domain tests/unit/domain tests/fixtures
mypy domain
```

Expected: zero failures and 100 percent branch coverage for IDs, dates, validation,
projection, and checksum modules.

- [ ] **Step 4: Commit graph checksum support**

```powershell
git add backend/domain backend/tests/unit/domain backend/tests/fixtures
git commit -m "feat: add canonical Shajra graph checksums"
```

## Completion Gate

This plan is complete only when the pure domain suite passes without environment
variables or network access, projection output is deterministic across ten input
orders, every specified invariant has a stable issue-code test, and the worktree
is clean. Continue with
`2026-08-03-shajra-backend-persistence-and-migration.md`.
