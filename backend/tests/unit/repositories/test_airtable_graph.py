import copy
import hashlib
import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import pytest

from domain.checksum import semantic_checksum
from domain.ids import (
    FamilyUnitId,
    LinkId,
    OperationId,
    PersonId,
    UnresolvedRelationshipId,
)
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    GraphSnapshot,
    GraphState,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)
from repositories.protocols import (
    AuditOperation,
    AuditOperationState,
    CommitPermit,
    GraphCommit,
    GraphWriteSet,
    WriteContext,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_commit_sha256,
)


SCOPE = "graph:main"
COMMITTED_AT = datetime(2026, 8, 5, 12, 30, 45, 123456, tzinfo=UTC)
ENTITY_TABLES = (
    "PersonVersions",
    "FamilyUnits",
    "ParentChildLinks",
    "UnresolvedRelationships",
)
MISSING = object()


class AmbiguousCreateError(RuntimeError):
    pass


class FakeAirtableTable:
    def __init__(self, owner: "FakeAirtable", name: str) -> None:
        self.owner = owner
        self.name = name
        self.records: list[dict[str, object]] = []
        self.ambiguous_create = False
        self.fail_upsert = False

    def all(self) -> list[dict[str, object]]:
        self.owner.calls.append(("all", self.name))
        return copy.deepcopy(self.records)

    def batch_create(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        self.owner.calls.append(("batch_create", self.name))
        self.owner.write_order.append(self.name)
        created = [self._record(row) for row in rows]
        self.records.extend(created)
        if self.ambiguous_create:
            self.ambiguous_create = False
            raise AmbiguousCreateError(f"ambiguous {self.name} create")
        return copy.deepcopy(created)

    def batch_upsert(
        self,
        rows: list[dict[str, object]],
        *,
        key_fields: list[str],
    ) -> dict[str, object]:
        self.owner.calls.append(("batch_upsert", self.name))
        self.owner.write_order.append(self.name)
        if self.fail_upsert:
            raise RuntimeError("cache unavailable")
        for row in rows:
            match = next(
                (
                    record
                    for record in self.records
                    if all(
                        record["fields"].get(key) == row.get(key) for key in key_fields
                    )  # type: ignore[union-attr]
                ),
                None,
            )
            if match is None:
                self.records.append(self._record(row))
            else:
                match["fields"] = copy.deepcopy(row)
        return {"createdRecords": [], "updatedRecords": []}

    def seed(self, *rows: dict[str, object]) -> None:
        self.records.extend(self._record(row) for row in rows)

    def _record(self, fields: dict[str, object]) -> dict[str, object]:
        record_number = self.owner.next_record_number
        self.owner.next_record_number += 1
        return {
            "id": f"rec_{self.name}_{record_number}",
            "createdTime": "2026-08-05T12:30:45.000Z",
            "fields": copy.deepcopy(fields),
        }


class FakeAirtable:
    def __init__(self) -> None:
        self.tables = {
            name: FakeAirtableTable(self, name)
            for name in (*ENTITY_TABLES, "GraphCommits", "GraphState", "ChangeLog")
        }
        self.calls: list[tuple[str, str]] = []
        self.write_order: list[str] = []
        self.next_record_number = 1

    def table(self, name: str) -> FakeAirtableTable:
        return self.tables[name]


def _graph_repository(fake: FakeAirtable):
    module = importlib.import_module("repositories.airtable.graph")
    return module.AirtableGraphRepository(fake, scope=SCOPE)


def _audit_repository(fake: FakeAirtable, *, now: datetime = COMMITTED_AT):
    module = importlib.import_module("repositories.airtable.audit")
    ticks = iter(now + timedelta(microseconds=index) for index in range(100))
    return module.AirtableAuditRepository(fake, scope=SCOPE, clock=lambda: next(ticks))


def _context(operation_id: str, revision: int, fencing_token: int) -> WriteContext:
    return WriteContext(
        operation_id=OperationId(operation_id),
        revision=revision,
        fencing_token=fencing_token,
        actor_id="usr_test",
        request_id="req_test",
    )


def _snapshot(
    revision: int,
    operation_id: str,
    fencing_token: int,
    *,
    people: tuple[Person, ...] = (),
    family_units: tuple[FamilyUnit, ...] = (),
    links: tuple[ParentChildLink, ...] = (),
    unresolved: tuple[UnresolvedRelationship, ...] = (),
) -> GraphSnapshot:
    return GraphSnapshot(
        GraphState(revision, OperationId(operation_id), fencing_token, ""),
        {person.person_id: person for person in people},
        {family.family_unit_id: family for family in family_units},
        {link.link_id: link for link in links},
        {item.unresolved_id: item for item in unresolved},
    )


def _commit_and_permit(
    receipt,
    snapshot: GraphSnapshot,
    *,
    committed_at: datetime = COMMITTED_AT,
) -> tuple[GraphCommit, CommitPermit]:
    commit = GraphCommit(
        operation_id=receipt.operation_id,
        revision=receipt.revision,
        fencing_token=receipt.fencing_token,
        permit_id=f"cpr_{receipt.operation_id}",
        semantic_checksum=semantic_checksum(snapshot),
        committed_at=committed_at,
    )
    return commit, CommitPermit(
        scope=SCOPE,
        operation_id=commit.operation_id,
        revision=commit.revision,
        fencing_token=commit.fencing_token,
        permit_id=commit.permit_id,
        commit_sha256=graph_commit_sha256(commit),
    )


def _full_write_set() -> GraphWriteSet:
    parent = Person(PersonId("per_parent"), "Parent", archived=True, version_revision=4)
    child = Person(PersonId("per_child"), "Child")
    family = FamilyUnit(
        FamilyUnitId("fam_one"),
        FamilyUnitKind.UNION,
        parent.person_id,
        child.person_id,
        distinct_union_confirmed=True,
        created_revision=1,
    )
    link = ParentChildLink(
        LinkId("lnk_one"),
        parent.person_id,
        child.person_id,
        ParentRole.PARENT,
        RelationshipType.BIOLOGICAL,
        family.family_unit_id,
        created_revision=1,
    )
    unresolved = UnresolvedRelationship(
        UnresolvedRelationshipId("unr_one"),
        child.person_id,
        UnresolvedRelationshipKind.FATHER,
        "  Unknown   Father  ",
        created_revision=1,
    )
    return GraphWriteSet(
        person_upserts=(parent, child),
        person_tombstones=(PersonId("per_removed"),),
        family_unit_upserts=(family,),
        family_unit_tombstones=(FamilyUnitId("fam_removed"),),
        parent_child_link_upserts=(link,),
        parent_child_link_tombstones=(LinkId("lnk_removed"),),
        unresolved_upserts=(unresolved,),
        unresolved_tombstones=(UnresolvedRelationshipId("unr_removed"),),
    )


def test_stage_writes_all_entity_tables_append_only_with_exact_authorization() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    context = _context("op_stage", 7, 23)

    receipt = repository.stage(_full_write_set(), context)
    repository.verify_staged(receipt)

    assert fake.write_order == list(ENTITY_TABLES)
    for table_name in ENTITY_TABLES:
        fields = [record["fields"] for record in fake.tables[table_name].records]
        assert fields
        assert all(row["Revision"] == 7 for row in fields)  # type: ignore[index]
        assert all(row["OperationId"] == "op_stage" for row in fields)  # type: ignore[index]
        assert all(row["FencingToken"] == 23 for row in fields)  # type: ignore[index]
        assert all(row["GraphScope"] == SCOPE for row in fields)  # type: ignore[index]
        assert all(isinstance(row["IsTombstone"], bool) for row in fields)  # type: ignore[index]
    person_rows = [record["fields"] for record in fake.tables["PersonVersions"].records]
    archived = next(row for row in person_rows if row["PersonId"] == "per_parent")  # type: ignore[index]
    removed = next(row for row in person_rows if row["PersonId"] == "per_removed")  # type: ignore[index]
    assert archived["Archived"] is True  # type: ignore[index]
    assert archived["IsTombstone"] is False  # type: ignore[index]
    assert removed == {
        "PersonId": "per_removed",
        "GraphScope": SCOPE,
        "Revision": 7,
        "OperationId": "op_stage",
        "FencingToken": 23,
        "IsTombstone": True,
    }
    family_row = fake.tables["FamilyUnits"].records[0]["fields"]
    unresolved_row = fake.tables["UnresolvedRelationships"].records[0]["fields"]
    assert family_row["DistinctUnionConfirmed"] is True  # type: ignore[index]
    assert unresolved_row["Kind"] == "father"  # type: ignore[index]
    assert unresolved_row["SubjectPersonId"] == "per_child"  # type: ignore[index]
    assert unresolved_row["UnresolvedName"] == "Unknown Father"  # type: ignore[index]
    assert receipt.write_set_json == canonical_graph_write_set_json(_full_write_set())


def test_commit_is_written_only_after_receipt_verification_and_cache_is_best_effort() -> (
    None
):
    fake = FakeAirtable()
    fake.tables["GraphState"].fail_upsert = True
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 11)
    )
    snapshot = _snapshot(1, "op_one", 11, people=(person,))
    commit, permit = _commit_and_permit(receipt, snapshot)

    with pytest.raises(ValueError, match="verified"):
        repository.append_commit(commit, permit)
    assert fake.tables["GraphCommits"].records == []

    repository.verify_staged(receipt)
    state = repository.append_commit(commit, permit)

    assert state == GraphState(
        1, OperationId("op_one"), 11, semantic_checksum(snapshot)
    )
    assert fake.write_order == [
        "PersonVersions",
        "FamilyUnits",
        "ParentChildLinks",
        "UnresolvedRelationships",
        "GraphCommits",
        "GraphState",
    ]
    assert fake.tables["GraphCommits"].records[0]["fields"] == {
        "Revision": 1,
        "OperationId": "op_one",
        "GraphScope": SCOPE,
        "FencingToken": 11,
        "PermitId": "cpr_op_one",
        "SemanticChecksum": semantic_checksum(snapshot),
        "CommittedAt": COMMITTED_AT.isoformat(),
    }


def test_append_reverifies_the_exact_receipt_before_publishing() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    receipt = repository.stage(GraphWriteSet(), _context("op_one", 1, 11))
    repository.verify_staged(receipt)
    fake.tables["PersonVersions"].seed(
        {
            "PersonId": "per_unexpected",
            "GraphScope": SCOPE,
            "Revision": 1,
            "OperationId": "op_one",
            "FencingToken": 11,
            "IsTombstone": True,
        }
    )
    commit, permit = _commit_and_permit(receipt, _snapshot(1, "op_one", 11))

    with pytest.raises(ValueError, match="receipt"):
        repository.append_commit(commit, permit)

    assert fake.tables["GraphCommits"].records == []


def test_graph_scope_filters_verification_commits_entities_and_cache() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "Main")
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_main", 1, 7)
    )
    other_entity = copy.deepcopy(fake.tables["PersonVersions"].records[0]["fields"])
    other_entity["GraphScope"] = "graph:other"  # type: ignore[index]
    other_entity["FullName"] = "Other"  # type: ignore[index]
    fake.tables["PersonVersions"].seed(other_entity)  # type: ignore[arg-type]

    repository.verify_staged(receipt)
    snapshot = _snapshot(1, "op_main", 7, people=(person,))
    commit, permit = _commit_and_permit(receipt, snapshot)
    repository.append_commit(commit, permit)
    main_commit_row = fake.tables["GraphCommits"].records[0]["fields"]
    fake.tables["GraphCommits"].seed(
        {
            **main_commit_row,  # type: ignore[dict-item]
            "GraphScope": "graph:other",
            "OperationId": "op_other",
            "PermitId": "cpr_other",
        }
    )

    assert repository.load_committed().people == {person.person_id: person}
    assert fake.tables["GraphState"].records[0]["fields"]["StateKey"] == SCOPE  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutate_commit", "mutate_permit"),
    (
        (lambda commit: commit, lambda permit: replace(permit, scope="graph:other")),
        (
            lambda commit: replace(commit, semantic_checksum="changed"),
            lambda permit: permit,
        ),
        (
            lambda commit: replace(
                commit, committed_at=commit.committed_at + timedelta(seconds=1)
            ),
            lambda permit: permit,
        ),
    ),
    ids=("scope", "semantic-checksum", "committed-at"),
)
def test_permit_scope_and_full_digest_are_rejected_before_airtable_access(
    mutate_commit, mutate_permit
) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    receipt = type(
        "Receipt",
        (),
        {"operation_id": OperationId("op_one"), "revision": 1, "fencing_token": 4},
    )()
    commit, permit = _commit_and_permit(receipt, _snapshot(1, "op_one", 4))

    with pytest.raises(ValueError):
        repository.append_commit(mutate_commit(commit), mutate_permit(permit))

    assert fake.calls == []


def test_ambiguous_commit_create_is_resolved_by_canonical_readback() -> None:
    fake = FakeAirtable()
    fake.tables["GraphCommits"].ambiguous_create = True
    repository = _graph_repository(fake)
    receipt = repository.stage(GraphWriteSet(), _context("op_empty", 1, 8))
    repository.verify_staged(receipt)
    commit, permit = _commit_and_permit(receipt, _snapshot(1, "op_empty", 8))

    state = repository.append_commit(commit, permit)

    assert state.revision == 1
    assert len(fake.tables["GraphCommits"].records) == 1
    assert fake.write_order[-2:] == ["GraphCommits", "GraphState"]


@pytest.mark.parametrize("damage", ("missing", "conflicting"))
def test_identical_commit_retry_revalidates_the_materialized_snapshot(
    damage: str,
) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    first_receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 1)
    )
    repository.verify_staged(first_receipt)
    first_snapshot = _snapshot(1, "op_one", 1, people=(person,))
    first_commit, first_permit = _commit_and_permit(first_receipt, first_snapshot)
    repository.append_commit(first_commit, first_permit)

    second_receipt = repository.stage(GraphWriteSet(), _context("op_two", 2, 2))
    repository.verify_staged(second_receipt)
    second_snapshot = _snapshot(2, "op_two", 2, people=(person,))
    second_commit, second_permit = _commit_and_permit(second_receipt, second_snapshot)
    repository.append_commit(second_commit, second_permit)

    if damage == "missing":
        fake.tables["PersonVersions"].records.clear()
        error = "COMMIT_CHECKSUM_MISMATCH"
    else:
        conflicting = copy.deepcopy(fake.tables["PersonVersions"].records[0]["fields"])
        conflicting["FullName"] = "Conflicting"  # type: ignore[index]
        fake.tables["PersonVersions"].seed(conflicting)  # type: ignore[arg-type]
        error = "ENTITY_VERSION_CORRUPTION"

    with pytest.raises(RuntimeError, match=error):
        repository.append_commit(second_commit, second_permit)


def test_identical_commit_retry_repairs_the_graph_state_cache() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    receipt = repository.stage(GraphWriteSet(), _context("op_one", 1, 5))
    repository.verify_staged(receipt)
    snapshot = _snapshot(1, "op_one", 5)
    commit, permit = _commit_and_permit(receipt, snapshot)
    expected_state = repository.append_commit(commit, permit)
    fake.tables["GraphState"].records.clear()

    retried_state = repository.append_commit(commit, permit)

    assert retried_state == expected_state
    assert fake.tables["GraphState"].records[0]["fields"] == {
        "StateKey": SCOPE,
        "Revision": 1,
        "HeadOperationId": "op_one",
        "FencingToken": 5,
        "SemanticChecksum": semantic_checksum(snapshot),
        "UpdatedAt": COMMITTED_AT.isoformat(),
    }


def test_fresh_repository_recovers_identical_commit_and_repairs_cache() -> None:
    fake = FakeAirtable()
    first_repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    receipt = first_repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 5)
    )
    first_repository.verify_staged(receipt)
    snapshot = _snapshot(1, "op_one", 5, people=(person,))
    commit, permit = _commit_and_permit(receipt, snapshot)
    expected_state = first_repository.append_commit(commit, permit)
    fake.tables["GraphState"].records.clear()

    recovered_state = _graph_repository(fake).append_commit(commit, permit)

    assert recovered_state == expected_state
    assert len(fake.tables["GraphCommits"].records) == 1
    assert fake.tables["GraphState"].records[0]["fields"]["StateKey"] == SCOPE  # type: ignore[index]


def test_verification_ignores_malformed_semantics_outside_receipt_tuple() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),)),
        _context("op_one", 1, 5),
    )
    valid = fake.tables["PersonVersions"].records[0]["fields"]
    for overrides in (
        {"OperationId": "op_abandoned"},
        {"FencingToken": 999},
        {"Revision": 2},
    ):
        malformed = copy.deepcopy(valid)
        malformed.pop("FullName")  # type: ignore[union-attr]
        malformed.update(overrides)  # type: ignore[union-attr]
        fake.tables["PersonVersions"].seed(malformed)  # type: ignore[arg-type]

    repository.verify_staged(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("Revision", MISSING),
        ("Revision", True),
        ("Revision", "1"),
        ("Revision", 0),
        ("Revision", -1),
        ("FencingToken", MISSING),
        ("FencingToken", True),
        ("FencingToken", "5"),
        ("FencingToken", 0),
        ("FencingToken", -1),
        ("OperationId", MISSING),
        ("OperationId", True),
        ("OperationId", ""),
        ("OperationId", "bad"),
    ),
    ids=(
        "missing-revision",
        "bool-revision",
        "wrong-type-revision",
        "zero-revision",
        "negative-revision",
        "missing-fence",
        "bool-fence",
        "wrong-type-fence",
        "zero-fence",
        "negative-fence",
        "missing-operation",
        "wrong-type-operation",
        "empty-operation",
        "bad-operation-id",
    ),
)
def test_verify_staged_fails_closed_on_malformed_authorization_metadata(
    field: str, value: object
) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),)),
        _context("op_one", 1, 5),
    )
    malformed = copy.deepcopy(fake.tables["PersonVersions"].records[0]["fields"])
    if value is MISSING:
        malformed.pop(field)  # type: ignore[union-attr]
    else:
        malformed[field] = value  # type: ignore[index]
    fake.tables["PersonVersions"].seed(malformed)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="AIRTABLE_ROW_CORRUPTION"):
        repository.verify_staged(receipt)


def test_committed_load_ignores_malformed_semantics_outside_commit_tuple() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 5)
    )
    repository.verify_staged(receipt)
    commit, permit = _commit_and_permit(
        receipt, _snapshot(1, "op_one", 5, people=(person,))
    )
    repository.append_commit(commit, permit)
    valid = fake.tables["PersonVersions"].records[0]["fields"]
    for overrides in (
        {"OperationId": "op_abandoned"},
        {"FencingToken": 999},
        {"Revision": 2},
    ):
        malformed = copy.deepcopy(valid)
        malformed.pop("FullName")  # type: ignore[union-attr]
        malformed.update(overrides)  # type: ignore[union-attr]
        fake.tables["PersonVersions"].seed(malformed)  # type: ignore[arg-type]

    assert repository.load_committed().people == {person.person_id: person}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("Revision", MISSING),
        ("Revision", True),
        ("Revision", "1"),
        ("Revision", 0),
        ("Revision", -1),
        ("FencingToken", MISSING),
        ("FencingToken", True),
        ("FencingToken", "5"),
        ("FencingToken", 0),
        ("FencingToken", -1),
        ("OperationId", MISSING),
        ("OperationId", True),
        ("OperationId", ""),
        ("OperationId", "bad"),
    ),
    ids=(
        "missing-revision",
        "bool-revision",
        "wrong-type-revision",
        "zero-revision",
        "negative-revision",
        "missing-fence",
        "bool-fence",
        "wrong-type-fence",
        "zero-fence",
        "negative-fence",
        "missing-operation",
        "wrong-type-operation",
        "empty-operation",
        "bad-operation-id",
    ),
)
def test_committed_load_fails_closed_on_malformed_authorization_metadata(
    field: str, value: object
) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 5)
    )
    repository.verify_staged(receipt)
    commit, permit = _commit_and_permit(
        receipt, _snapshot(1, "op_one", 5, people=(person,))
    )
    repository.append_commit(commit, permit)
    malformed = copy.deepcopy(fake.tables["PersonVersions"].records[0]["fields"])
    if value is MISSING:
        malformed.pop(field)  # type: ignore[union-attr]
    else:
        malformed[field] = value  # type: ignore[index]
    fake.tables["PersonVersions"].seed(malformed)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="AIRTABLE_ROW_CORRUPTION"):
        repository.load_committed()


@pytest.mark.parametrize("phase", ("verify", "load"))
def test_authorized_rows_with_malformed_semantics_fail_closed(phase: str) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    receipt = repository.stage(
        GraphWriteSet(person_upserts=(person,)), _context("op_one", 1, 5)
    )
    if phase == "load":
        repository.verify_staged(receipt)
        commit, permit = _commit_and_permit(
            receipt, _snapshot(1, "op_one", 5, people=(person,))
        )
        repository.append_commit(commit, permit)
    malformed = copy.deepcopy(fake.tables["PersonVersions"].records[0]["fields"])
    malformed.pop("FullName")  # type: ignore[union-attr]
    fake.tables["PersonVersions"].seed(malformed)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        if phase == "verify":
            repository.verify_staged(receipt)
        else:
            repository.load_committed()


def test_uncommitted_rows_are_invisible_and_exact_commit_tuple_authorizes_rows() -> (
    None
):
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    shared_id = PersonId("per_shared")
    repository.stage(
        GraphWriteSet(person_upserts=(Person(shared_id, "From A"),)),
        _context("op_a", 1, 20),
    )
    receipt_b = repository.stage(
        GraphWriteSet(person_upserts=(Person(shared_id, "From B"),)),
        _context("op_b", 1, 21),
    )
    repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_wrong_fence"), "Wrong"),)),
        _context("op_b", 1, 20),
    )

    assert repository.load_committed().people == {}

    repository.verify_staged(receipt_b)
    commit, permit = _commit_and_permit(
        receipt_b, _snapshot(1, "op_b", 21, people=(Person(shared_id, "From B"),))
    )
    repository.append_commit(commit, permit)

    assert repository.load_committed().people == {
        shared_id: Person(shared_id, "From B")
    }


def test_reads_select_requested_versions_and_apply_all_tombstone_kinds() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    write_set = _full_write_set()
    first_receipt = repository.stage(write_set, _context("op_add", 1, 1))
    repository.verify_staged(first_receipt)
    first_snapshot = _snapshot(
        1,
        "op_add",
        1,
        people=write_set.person_upserts,
        family_units=write_set.family_unit_upserts,
        links=write_set.parent_child_link_upserts,
        unresolved=write_set.unresolved_upserts,
    )
    first_commit, first_permit = _commit_and_permit(first_receipt, first_snapshot)
    repository.append_commit(first_commit, first_permit)

    second_write = GraphWriteSet(
        person_tombstones=(PersonId("per_parent"),),
        family_unit_tombstones=(FamilyUnitId("fam_one"),),
        parent_child_link_tombstones=(LinkId("lnk_one"),),
        unresolved_tombstones=(UnresolvedRelationshipId("unr_one"),),
    )
    second_receipt = repository.stage(second_write, _context("op_remove", 2, 2))
    repository.verify_staged(second_receipt)
    second_snapshot = _snapshot(
        2,
        "op_remove",
        2,
        people=(Person(PersonId("per_child"), "Child"),),
    )
    second_commit, second_permit = _commit_and_permit(second_receipt, second_snapshot)
    repository.append_commit(second_commit, second_permit)

    assert repository.load_committed(1) == replace(
        first_snapshot,
        state=replace(
            first_snapshot.state, semantic_checksum=semantic_checksum(first_snapshot)
        ),
    )
    assert repository.load_committed(2) == replace(
        second_snapshot,
        state=replace(
            second_snapshot.state, semantic_checksum=semantic_checksum(second_snapshot)
        ),
    )


def test_identical_physical_retries_dedupe_but_conflicts_fail_closed() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    person = Person(PersonId("per_one"), "One")
    context = _context("op_one", 1, 1)
    receipt = repository.stage(GraphWriteSet(person_upserts=(person,)), context)
    repository.stage(GraphWriteSet(person_upserts=(person,)), context)
    repository.verify_staged(receipt)
    snapshot = _snapshot(1, "op_one", 1, people=(person,))
    commit, permit = _commit_and_permit(receipt, snapshot)
    repository.append_commit(commit, permit)
    fake.tables["GraphCommits"].seed(fake.tables["GraphCommits"].records[0]["fields"])  # type: ignore[arg-type]

    assert repository.load_committed().people == {person.person_id: person}

    conflicting_person = copy.deepcopy(
        fake.tables["PersonVersions"].records[0]["fields"]
    )
    conflicting_person["FullName"] = "Other"  # type: ignore[index]
    fake.tables["PersonVersions"].seed(conflicting_person)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="ENTITY_VERSION_CORRUPTION"):
        repository.load_committed()


def test_conflicting_commit_rows_and_semantic_checksum_mismatch_fail_closed() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    commit_row = {
        "GraphScope": SCOPE,
        "Revision": 1,
        "OperationId": "op_one",
        "FencingToken": 1,
        "PermitId": "cpr_one",
        "SemanticChecksum": semantic_checksum(_snapshot(1, "op_one", 1)),
        "CommittedAt": COMMITTED_AT.isoformat(),
    }
    fake.tables["GraphCommits"].seed(
        commit_row, {**commit_row, "PermitId": "cpr_other"}
    )

    with pytest.raises(RuntimeError, match="COMMIT_LOG_CORRUPTION"):
        repository.load_committed()

    fake = FakeAirtable()
    repository = _graph_repository(fake)
    fake.tables["GraphCommits"].seed({**commit_row, "SemanticChecksum": "0" * 64})
    with pytest.raises(RuntimeError, match="COMMIT_CHECKSUM_MISMATCH"):
        repository.load_committed()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("OperationId", "bad"),
        ("Revision", 0),
        ("Revision", -1),
        ("FencingToken", 0),
        ("FencingToken", -1),
        ("PermitId", "bad"),
        ("SemanticChecksum", "bad"),
        ("CommittedAt", "2026-08-05T12:30:45"),
    ),
)
def test_malformed_commit_rows_fail_with_stable_corruption(
    field: str, value: object
) -> None:
    fake = FakeAirtable()
    fake.tables["GraphCommits"].seed(
        {
            "GraphScope": SCOPE,
            "Revision": 1,
            "OperationId": "op_one",
            "FencingToken": 1,
            "PermitId": "cpr_one",
            "SemanticChecksum": "a" * 64,
            "CommittedAt": COMMITTED_AT.isoformat(),
            field: value,
        }
    )

    with pytest.raises(RuntimeError, match="COMMIT_LOG_CORRUPTION"):
        _graph_repository(fake).load_committed()


@pytest.mark.parametrize(
    "revisions",
    ((0,), (-1,), (1, 3)),
    ids=("zero", "negative", "gap"),
)
def test_commit_history_requires_positive_contiguous_revisions(revisions) -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)
    for revision in revisions:
        fake.tables["GraphCommits"].seed(
            {
                "GraphScope": SCOPE,
                "Revision": revision,
                "OperationId": f"op_{revision}",
                "FencingToken": revision,
                "PermitId": f"cpr_{revision}",
                "SemanticChecksum": semantic_checksum(
                    _snapshot(revision, f"op_{revision}", revision)
                ),
                "CommittedAt": COMMITTED_AT.isoformat(),
            }
        )

    with pytest.raises(RuntimeError, match="COMMIT_LOG_CORRUPTION"):
        repository.load_committed()


def test_explicit_revision_must_name_an_exact_commit() -> None:
    fake = FakeAirtable()
    repository = _graph_repository(fake)

    assert repository.load_committed(0).state == GraphState(0, None, 0, "")
    with pytest.raises(ValueError, match="committed revision"):
        repository.load_committed(1)
    with pytest.raises(ValueError, match="negative"):
        repository.load_committed(-1)

    receipt = repository.stage(GraphWriteSet(), _context("op_one", 1, 1))
    repository.verify_staged(receipt)
    commit, permit = _commit_and_permit(receipt, _snapshot(1, "op_one", 1))
    repository.append_commit(commit, permit)

    assert repository.load_committed(1).state.revision == 1
    with pytest.raises(ValueError, match="committed revision"):
        repository.load_committed(0)
    with pytest.raises(ValueError, match="committed revision"):
        repository.load_committed(2)


def _audit_operation(
    *,
    operation_id: str = "op_audit",
    idempotency_key: str = "idem_one",
) -> AuditOperation:
    commit = GraphCommit(
        OperationId(operation_id),
        3,
        17,
        f"cpr_{operation_id}",
        "a" * 64,
        COMMITTED_AT,
    )
    inverse = {
        "unresolved_tombstones": ["unr_z", "unr_a"],
        "unresolved_upserts": [],
        "parent_child_link_tombstones": [],
        "parent_child_link_upserts": [],
        "family_unit_tombstones": [],
        "family_unit_upserts": [],
        "person_tombstones": ["per_removed_z", "per_removed_a"],
        "person_upserts": [
            {
                "version_revision": 2,
                "person_id": "per_z",
                "primary_family_unit_id": None,
                "is_alive": None,
                "gender": "unknown",
                "full_name": "Zed",
                "death": None,
                "birth": None,
                "archived": False,
            },
            {
                "person_id": "per_a",
                "full_name": "Ada",
                "gender": "unknown",
                "birth": None,
                "death": None,
                "is_alive": None,
                "primary_family_unit_id": None,
                "archived": False,
                "version_revision": 1,
            },
        ],
    }
    return AuditOperation(
        operation_id=OperationId(operation_id),
        idempotency_key=idempotency_key,
        state=AuditOperationState.PENDING,
        actor_id="usr_admin",
        request_id="req_audit",
        source_reference="submission_42",
        commands_json=json.dumps([{"kind": "rename", "person_id": "per_a"}]),
        before_snapshot_json=json.dumps(
            {
                "people": [
                    {
                        "person_id": "per_a",
                        "full_name": "Ada",
                        "contact_email": "private@example.test",
                        "airtable_record_id": "rec_private",
                    }
                ],
                "api_key": "secret",
            }
        ),
        after_snapshot_json=json.dumps(
            {
                "people": [
                    {
                        "person_id": "per_a",
                        "full_name": "Ada Updated",
                        "mobile": "+10000000000",
                    }
                ]
            }
        ),
        inverse_write_set_json=json.dumps(inverse),
        commit_scope=SCOPE,
        graph_commit_json=canonical_graph_commit_json(commit),
        commit_sha256=graph_commit_sha256(commit),
    )


def test_audit_pending_is_append_only_canonical_redacted_and_record_id_free() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)

    repository.create_pending(_audit_operation())
    found = repository.find_by_idempotency_key("idem_one")

    assert found is not None
    assert found.state is AuditOperationState.PENDING
    assert json.loads(found.before_snapshot_json) == {
        "people": [{"full_name": "Ada", "person_id": "per_a"}]
    }
    assert json.loads(found.after_snapshot_json) == {
        "people": [{"full_name": "Ada Updated", "person_id": "per_a"}]
    }
    assert "private@example.test" not in str(fake.tables["ChangeLog"].records)
    assert "+10000000000" not in str(fake.tables["ChangeLog"].records)
    assert "rec_private" not in str(fake.tables["ChangeLog"].records)
    assert "rec_ChangeLog" not in repr(found)
    row = fake.tables["ChangeLog"].records[0]["fields"]
    assert row["CommitScope"] == SCOPE  # type: ignore[index]
    assert row["CommandsJson"] == found.commands_json  # type: ignore[index]
    assert row["BeforeSnapshotJson"] == found.before_snapshot_json  # type: ignore[index]
    assert row["AfterSnapshotJson"] == found.after_snapshot_json  # type: ignore[index]
    assert row["GraphCommitJson"] == found.graph_commit_json  # type: ignore[index]
    assert row["CommitSha256"] == found.commit_sha256  # type: ignore[index]
    assert row["InverseWriteSetJson"] == canonical_graph_write_set_json(  # type: ignore[index]
        GraphWriteSet(
            person_upserts=(
                Person(PersonId("per_a"), "Ada", version_revision=1),
                Person(PersonId("per_z"), "Zed", version_revision=2),
            ),
            person_tombstones=(
                PersonId("per_removed_a"),
                PersonId("per_removed_z"),
            ),
            unresolved_tombstones=(
                UnresolvedRelationshipId("unr_a"),
                UnresolvedRelationshipId("unr_z"),
            ),
        )
    )


def test_audit_recursively_removes_private_keys_and_values() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = replace(
        _audit_operation(),
        commands_json=json.dumps(
            [
                {
                    "kind": "rename",
                    "person_id": "per_a",
                    "metadata": {
                        "primary_email_address": "first@example.test",
                        "api_token_hash": "hash-secret",
                    },
                }
            ]
        ),
        before_snapshot_json=json.dumps(
            {
                "person_id": "per_a",
                "full_name": "Ada",
                "nested": {
                    "auth": {"kind": "password", "value": "hunter2"},
                    "items": [
                        "safe",
                        "second@example.test",
                        "+1 (202) 555-0199",
                        "rec12345678901234",
                        {"kind": "password", "value": "generic-secret"},
                    ],
                },
            }
        ),
        after_snapshot_json=json.dumps(
            {
                "person_id": "per_a",
                "full_name": "Ada Updated",
                "payload": "third@example.test",
                "details": {"value": "+442071838750"},
            }
        ),
    )

    repository.create_pending(operation)
    found = repository.find_by_idempotency_key(operation.idempotency_key)

    assert found is not None
    assert json.loads(found.commands_json) == [
        {"kind": "rename", "metadata": {}, "person_id": "per_a"}
    ]
    assert json.loads(found.before_snapshot_json) == {
        "full_name": "Ada",
        "nested": {"items": ["safe"]},
        "person_id": "per_a",
    }
    assert json.loads(found.after_snapshot_json) == {
        "details": {},
        "full_name": "Ada Updated",
        "person_id": "per_a",
    }
    serialized = " ".join(
        (
            found.commands_json,
            found.before_snapshot_json,
            found.after_snapshot_json,
            str(fake.tables["ChangeLog"].records),
        )
    )
    for secret in (
        "first@example.test",
        "hash-secret",
        "hunter2",
        "second@example.test",
        "+1 (202) 555-0199",
        "rec12345678901234",
        "generic-secret",
        "third@example.test",
        "+442071838750",
    ):
        assert secret not in serialized


@pytest.mark.parametrize(
    "phone_text",
    (
        "Call +1 (202) 555-0199 after 5pm",
        "Reach me at 202.555.0199 ext. 42",
        "Phone: +44-20-7183-8750 x123",
    ),
    ids=("mixed-text", "dot-extension", "hyphen-extension"),
)
def test_audit_removes_embedded_phone_pii_but_preserves_partial_dates(
    phone_text: str,
) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = replace(
        _audit_operation(),
        after_snapshot_json=json.dumps(
            {
                "birth": "1980-01-02",
                "note": phone_text,
                "ordinary_number": "Room 42",
            }
        ),
    )

    repository.create_pending(operation)
    found = repository.find_by_idempotency_key(operation.idempotency_key)

    assert found is not None
    assert json.loads(found.after_snapshot_json) == {
        "birth": "1980-01-02",
        "ordinary_number": "Room 42",
    }
    row = fake.tables["ChangeLog"].records[0]["fields"]
    assert phone_text not in row["AfterSnapshotJson"]  # type: ignore[operator,index]
    assert phone_text not in str(fake.tables["ChangeLog"].records)


@pytest.mark.parametrize(
    "safe_text",
    (
        "Born 1980-01-02",
        "Year 1980",
        "Reference 12345678",
    ),
    ids=("embedded-date", "embedded-year", "unlabeled-eight-digits"),
)
def test_audit_preserves_embedded_partial_dates_and_short_unlabeled_numbers(
    safe_text: str,
) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = replace(
        _audit_operation(),
        after_snapshot_json=json.dumps({"note": safe_text}),
    )

    repository.create_pending(operation)
    found = repository.find_by_idempotency_key(operation.idempotency_key)

    assert found is not None
    assert json.loads(found.after_snapshot_json) == {"note": safe_text}
    row = fake.tables["ChangeLog"].records[0]["fields"]
    assert json.loads(row["AfterSnapshotJson"])["note"] == safe_text  # type: ignore[arg-type,index]


@pytest.mark.parametrize(
    "private_text",
    (
        "Call 202, 555, 0199",
        "Call 202\u2013555\u20130199",
        "Phone: 12345678",
        "Phone number: 12345678",
        "Tel. 12345678",
        "Mobile no. 12345678",
        "Telephone No: 12345678",
        "WhatsApp number: 12345678",
        "Call +1980-01-02",
        "Born 1980-01-02; call 202, 555, 0199",
    ),
    ids=(
        "comma",
        "en-dash",
        "labeled-eight-digits",
        "phone-number-label",
        "tel-period-label",
        "mobile-number-abbreviation",
        "telephone-number-label",
        "whatsapp-number-label",
        "plus-date-shaped-phone",
        "date-and-phone",
    ),
)
def test_audit_removes_general_and_labeled_phone_candidates(
    private_text: str,
) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = replace(
        _audit_operation(),
        after_snapshot_json=json.dumps({"note": private_text, "safe": "kept"}),
    )

    repository.create_pending(operation)
    found = repository.find_by_idempotency_key(operation.idempotency_key)

    assert found is not None
    assert json.loads(found.after_snapshot_json) == {"safe": "kept"}
    row = fake.tables["ChangeLog"].records[0]["fields"]
    assert private_text not in row["AfterSnapshotJson"]  # type: ignore[operator,index]
    assert private_text not in str(fake.tables["ChangeLog"].records)


def test_audit_scope_rejects_cross_scope_create_and_filters_reads() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    wrong_scope = replace(_audit_operation(), commit_scope="graph:other")

    with pytest.raises(ValueError, match="scope"):
        repository.create_pending(wrong_scope)
    assert fake.calls == []

    module = importlib.import_module("repositories.airtable.audit")
    other_repository = module.AirtableAuditRepository(
        fake, scope="graph:other", clock=lambda: COMMITTED_AT
    )
    other_repository.create_pending(wrong_scope)

    assert repository.find_by_idempotency_key(wrong_scope.idempotency_key) is None


def test_audit_rejects_private_or_untyped_inverse_write_sets() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    inverse = json.loads(operation.inverse_write_set_json)
    inverse["person_upserts"][0]["contact_email"] = "private@example.test"

    with pytest.raises(ValueError):
        repository.create_pending(
            replace(operation, inverse_write_set_json=json.dumps(inverse))
        )

    assert fake.tables["ChangeLog"].records == []


def test_audit_transitions_repeat_recovery_metadata_and_resolve_latest_state() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)

    repository.transition(operation.operation_id, AuditOperationState.COMMITTING)
    repository.transition(operation.operation_id, AuditOperationState.COMMITTING)
    repository.transition(operation.operation_id, AuditOperationState.COMMITTED)

    found = repository.find_by_idempotency_key(operation.idempotency_key)
    assert found is not None
    assert found.state is AuditOperationState.COMMITTED
    assert len(fake.tables["ChangeLog"].records) == 3
    for record in fake.tables["ChangeLog"].records:
        fields = record["fields"]
        assert fields["CommitScope"] == operation.commit_scope  # type: ignore[index]
        assert fields["GraphCommitJson"] == operation.graph_commit_json  # type: ignore[index]
        assert fields["CommitSha256"] == operation.commit_sha256  # type: ignore[index]
        assert fields["InverseWriteSetJson"]  # type: ignore[index]


def test_audit_pending_retry_is_idempotent_and_conflicting_key_fails_closed() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    repository.create_pending(operation)

    assert len(fake.tables["ChangeLog"].records) == 1

    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_CONFLICT"):
        repository.create_pending(
            _audit_operation(
                operation_id="op_other", idempotency_key=operation.idempotency_key
            )
        )
    assert len(fake.tables["ChangeLog"].records) == 1


def test_blank_source_reference_round_trips_as_idempotent_none() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    blank = replace(_audit_operation(), source_reference="")

    repository.create_pending(blank)
    repository.create_pending(replace(blank, source_reference=None))
    found = repository.find_by_idempotency_key(blank.idempotency_key)

    assert found is not None
    assert found.source_reference is None
    assert len(fake.tables["ChangeLog"].records) == 1
    assert fake.tables["ChangeLog"].records[0]["fields"]["SourceReference"] is None  # type: ignore[index]


def test_later_repetition_of_the_same_audit_state_is_corruption() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    repeated = copy.deepcopy(fake.tables["ChangeLog"].records[0]["fields"])
    repeated["UpdatedAt"] = (COMMITTED_AT + timedelta(microseconds=1)).isoformat()  # type: ignore[index]
    fake.tables["ChangeLog"].seed(repeated)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="AUDIT_LOG_CORRUPTION"):
        repository.find_by_idempotency_key(operation.idempotency_key)


def test_exact_duplicate_audit_transition_is_a_physical_retry() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    duplicate = copy.deepcopy(fake.tables["ChangeLog"].records[0]["fields"])
    fake.tables["ChangeLog"].seed(duplicate)  # type: ignore[arg-type]

    found = repository.find_by_idempotency_key(operation.idempotency_key)

    assert found is not None
    assert found.state is AuditOperationState.PENDING


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("operation_id", "bad"),
        ("revision", 0),
        ("revision", -1),
        ("fencing_token", 0),
        ("fencing_token", -1),
        ("permit_id", "bad"),
        ("semantic_checksum", "bad"),
        ("committed_at", "2026-08-05T12:30:45"),
    ),
)
def test_audit_rejects_invalid_graph_commit_json(field: str, value: object) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    commit_value = json.loads(operation.graph_commit_json)
    commit_value[field] = value
    malformed_json = json.dumps(commit_value)

    with pytest.raises(ValueError):
        repository.create_pending(
            replace(
                operation,
                graph_commit_json=malformed_json,
                commit_sha256=hashlib.sha256(
                    malformed_json.encode("ascii")
                ).hexdigest(),
            )
        )

    assert fake.tables["ChangeLog"].records == []


@pytest.mark.parametrize("document", ("commit", "commands", "snapshot"))
def test_audit_rejects_duplicate_json_object_keys(document: str) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    if document == "commit":
        duplicate = operation.graph_commit_json.replace(
            '"operation_id":"op_audit"',
            '"operation_id":"op_audit","operation_id":"op_other"',
            1,
        )
        operation = replace(operation, graph_commit_json=duplicate)
    elif document == "commands":
        operation = replace(
            operation, commands_json='[{"kind":"rename","kind":"delete"}]'
        )
    else:
        operation = replace(
            operation,
            before_snapshot_json='{"person":{"full_name":"Ada","full_name":"Other"}}',
        )

    with pytest.raises(ValueError, match="duplicate"):
        repository.create_pending(operation)

    assert fake.tables["ChangeLog"].records == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ExpectedRevision", 99),
        ("ResultRevision", 99),
        ("FencingToken", 99),
    ),
)
def test_audit_rows_validate_denormalized_commit_metadata(
    field: str, value: int
) -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    fake.tables["ChangeLog"].records[0]["fields"][field] = value  # type: ignore[index]

    with pytest.raises(RuntimeError, match="AUDIT_LOG_CORRUPTION"):
        repository.find_by_idempotency_key(operation.idempotency_key)


def test_audit_rows_reject_updated_at_before_created_at() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    fake.tables["ChangeLog"].records[0]["fields"]["UpdatedAt"] = (  # type: ignore[index]
        COMMITTED_AT - timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(RuntimeError, match="AUDIT_LOG_CORRUPTION"):
        repository.find_by_idempotency_key(operation.idempotency_key)


def test_audit_transitions_require_one_immutable_created_at() -> None:
    fake = FakeAirtable()
    repository = _audit_repository(fake)
    operation = _audit_operation()
    repository.create_pending(operation)
    repository.transition(operation.operation_id, AuditOperationState.COMMITTING)
    fake.tables["ChangeLog"].records[1]["fields"]["CreatedAt"] = (  # type: ignore[index]
        COMMITTED_AT + timedelta(microseconds=1)
    ).isoformat()

    with pytest.raises(RuntimeError, match="AUDIT_LOG_CORRUPTION"):
        repository.find_by_idempotency_key(operation.idempotency_key)
