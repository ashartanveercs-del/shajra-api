import copy
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
    return module.AirtableAuditRepository(fake, clock=lambda: next(ticks))


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
        assert all(isinstance(row["IsTombstone"], bool) for row in fields)  # type: ignore[index]
    person_rows = [record["fields"] for record in fake.tables["PersonVersions"].records]
    archived = next(row for row in person_rows if row["PersonId"] == "per_parent")  # type: ignore[index]
    removed = next(row for row in person_rows if row["PersonId"] == "per_removed")  # type: ignore[index]
    assert archived["Archived"] is True  # type: ignore[index]
    assert archived["IsTombstone"] is False  # type: ignore[index]
    assert removed == {
        "PersonId": "per_removed",
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
        "person_tombstones": ["per_z", "per_a"],
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
    assert row["GraphCommitJson"] == found.graph_commit_json  # type: ignore[index]
    assert row["CommitSha256"] == found.commit_sha256  # type: ignore[index]
    assert row["InverseWriteSetJson"] == canonical_graph_write_set_json(  # type: ignore[index]
        GraphWriteSet(
            person_upserts=(
                Person(PersonId("per_a"), "Ada", version_revision=1),
                Person(PersonId("per_z"), "Zed", version_revision=2),
            ),
            person_tombstones=(PersonId("per_a"), PersonId("per_z")),
            unresolved_tombstones=(
                UnresolvedRelationshipId("unr_a"),
                UnresolvedRelationshipId("unr_z"),
            ),
        )
    )


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
