from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
import json

import pytest

from domain.dates import PartialDate
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
    Gender,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
    UnionStatus,
)
from repositories.memory import InMemoryGraphRepository, RepositoryCorruptionError
from repositories.protocols import (
    CommitPermit,
    GraphCommit,
    GraphWriteSet,
    WriteContext,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_write_set_from_json,
    graph_commit_sha256,
)


SCOPE = "test:shajra"
COMMITTED_AT = datetime(2026, 8, 5, 12, 30, 45, 123456, tzinfo=UTC)
VALID_CHECKSUM = "a" * 64


@pytest.fixture
def memory_repository() -> InMemoryGraphRepository:
    return InMemoryGraphRepository(scope=SCOPE)


def context_for(operation_id: str, revision: int, fencing_token: int) -> WriteContext:
    return WriteContext(
        operation_id=OperationId(operation_id),
        revision=revision,
        fencing_token=fencing_token,
        actor_id="usr_test",
        request_id="req_test",
    )


def commit_and_permit_for(
    receipt,
    *,
    semantic_checksum: str = VALID_CHECKSUM,
    committed_at: datetime = COMMITTED_AT,
) -> tuple[GraphCommit, CommitPermit]:
    commit = GraphCommit(
        operation_id=receipt.operation_id,
        revision=receipt.revision,
        fencing_token=receipt.fencing_token,
        permit_id=f"cpr_{receipt.operation_id}",
        semantic_checksum=semantic_checksum,
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


def test_staged_rows_are_invisible_until_commit(
    memory_repository: InMemoryGraphRepository,
) -> None:
    write_set = GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),))
    receipt = memory_repository.stage(write_set, context_for("op_one", 1, 11))

    assert memory_repository.load_committed().state.revision == 0

    commit, permit = commit_and_permit_for(receipt)
    memory_repository.append_commit(commit, permit)

    assert memory_repository.load_committed().state.revision == 1


def test_only_committing_operation_is_visible_when_two_stage_same_revision(
    memory_repository: InMemoryGraphRepository,
) -> None:
    person_id = PersonId("per_shared")
    receipt_a = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(person_id, "From A"),)),
        context_for("op_a", 1, 21),
    )
    receipt_b = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(person_id, "From B"),)),
        context_for("op_b", 1, 22),
    )

    commit_b, permit_b = commit_and_permit_for(receipt_b)
    memory_repository.append_commit(commit_b, permit_b)

    assert memory_repository.load_committed().people[person_id].full_name == "From B"
    assert receipt_a.operation_id != commit_b.operation_id


def test_rows_with_only_operation_or_fencing_mismatches_stay_invisible(
    memory_repository: InMemoryGraphRepository,
) -> None:
    operation_mismatch_id = PersonId("per_operation_mismatch")
    fencing_mismatch_id = PersonId("per_fencing_mismatch")
    committed_id = PersonId("per_committed")
    memory_repository.stage(
        GraphWriteSet(
            person_upserts=(Person(operation_mismatch_id, "Wrong operation"),)
        ),
        context_for("op_other", 1, 10),
    )
    memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(fencing_mismatch_id, "Wrong fence"),)),
        context_for("op_committed", 1, 9),
    )
    receipt = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(committed_id, "Visible"),)),
        context_for("op_committed", 1, 10),
    )

    commit, permit = commit_and_permit_for(receipt)
    memory_repository.append_commit(commit, permit)
    snapshot = memory_repository.load_committed()

    assert snapshot.people == {committed_id: Person(committed_id, "Visible")}


def test_tombstones_remove_every_entity_kind(
    memory_repository: InMemoryGraphRepository,
) -> None:
    person_id = PersonId("per_parent")
    child_id = PersonId("per_child")
    family_id = FamilyUnitId("fam_parent")
    link_id = LinkId("lnk_parent_child")
    unresolved_id = UnresolvedRelationshipId("unr_parent")
    write_set = GraphWriteSet(
        person_upserts=(Person(person_id, "Parent"), Person(child_id, "Child")),
        family_unit_upserts=(
            FamilyUnit(family_id, FamilyUnitKind.SINGLE_PARENT, person_id),
        ),
        parent_child_link_upserts=(
            ParentChildLink(
                link_id,
                person_id,
                child_id,
                ParentRole.PARENT,
                RelationshipType.BIOLOGICAL,
                family_id,
            ),
        ),
        unresolved_upserts=(
            UnresolvedRelationship(
                unresolved_id,
                child_id,
                UnresolvedRelationshipKind.FATHER,
                "Unknown parent",
            ),
        ),
    )
    first_receipt = memory_repository.stage(write_set, context_for("op_add", 1, 1))
    first_commit, first_permit = commit_and_permit_for(first_receipt)
    memory_repository.append_commit(first_commit, first_permit)

    first_snapshot = memory_repository.load_committed()
    assert first_snapshot.people == {
        person_id: Person(person_id, "Parent"),
        child_id: Person(child_id, "Child"),
    }
    assert first_snapshot.family_units == {
        family_id: FamilyUnit(family_id, FamilyUnitKind.SINGLE_PARENT, person_id)
    }
    assert first_snapshot.links == {
        link_id: ParentChildLink(
            link_id,
            person_id,
            child_id,
            ParentRole.PARENT,
            RelationshipType.BIOLOGICAL,
            family_id,
        )
    }
    assert first_snapshot.unresolved == {
        unresolved_id: UnresolvedRelationship(
            unresolved_id,
            child_id,
            UnresolvedRelationshipKind.FATHER,
            "Unknown parent",
        )
    }

    tombstones = GraphWriteSet(
        person_tombstones=(person_id,),
        family_unit_tombstones=(family_id,),
        parent_child_link_tombstones=(link_id,),
        unresolved_tombstones=(unresolved_id,),
    )
    second_receipt = memory_repository.stage(tombstones, context_for("op_delete", 2, 2))
    second_commit, second_permit = commit_and_permit_for(second_receipt)
    memory_repository.append_commit(second_commit, second_permit)

    snapshot = memory_repository.load_committed()
    assert person_id not in snapshot.people
    assert child_id in snapshot.people
    assert family_id not in snapshot.family_units
    assert link_id not in snapshot.links
    assert unresolved_id not in snapshot.unresolved


@pytest.mark.parametrize(
    "mutate_permit",
    [
        lambda permit: replace(permit, scope="other:scope"),
        lambda permit: replace(permit, operation_id=OperationId("op_other")),
        lambda permit: replace(permit, revision=2),
        lambda permit: replace(permit, fencing_token=99),
        lambda permit: replace(permit, permit_id="cpr_other"),
        lambda permit: replace(permit, commit_sha256="0" * 64),
    ],
    ids=("scope", "operation", "revision", "fencing", "permit", "digest"),
)
def test_permit_mismatches_reject_before_adding_a_commit(
    memory_repository: InMemoryGraphRepository,
    mutate_permit,
) -> None:
    receipt = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),)),
        context_for("op_one", 1, 1),
    )
    commit, permit = commit_and_permit_for(receipt)

    with pytest.raises(ValueError):
        memory_repository.append_commit(commit, mutate_permit(permit))

    assert memory_repository.commit_count == 0


@pytest.mark.parametrize(
    "mutate_commit",
    [
        lambda commit: replace(commit, semantic_checksum="b" * 64),
        lambda commit: replace(
            commit, committed_at=commit.committed_at + timedelta(seconds=1)
        ),
    ],
    ids=("semantic_checksum", "committed_at"),
)
def test_permit_digest_binds_checksum_and_commit_time_before_append(
    memory_repository: InMemoryGraphRepository,
    mutate_commit,
) -> None:
    receipt = memory_repository.stage(GraphWriteSet(), context_for("op_one", 1, 1))
    commit, permit = commit_and_permit_for(receipt)

    with pytest.raises(ValueError):
        memory_repository.append_commit(mutate_commit(commit), permit)

    assert memory_repository.commit_count == 0


def test_identical_commit_duplicates_are_idempotent(
    memory_repository: InMemoryGraphRepository,
) -> None:
    receipt = memory_repository.stage(GraphWriteSet(), context_for("op_one", 1, 1))
    commit, permit = commit_and_permit_for(receipt)

    first_state = memory_repository.append_commit(commit, permit)
    second_state = memory_repository.append_commit(commit, permit)

    assert first_state == second_state
    assert memory_repository.commit_count == 1


def test_conflicting_logical_commit_duplicate_fails_closed(
    memory_repository: InMemoryGraphRepository,
) -> None:
    receipt = memory_repository.stage(GraphWriteSet(), context_for("op_one", 1, 1))
    commit, permit = commit_and_permit_for(receipt)
    memory_repository.append_commit(commit, permit)
    conflicting_commit = replace(commit, semantic_checksum="b" * 64)
    conflicting_permit = replace(
        permit, commit_sha256=graph_commit_sha256(conflicting_commit)
    )

    with pytest.raises(RepositoryCorruptionError, match="COMMIT_LOG_CORRUPTION"):
        memory_repository.append_commit(conflicting_commit, conflicting_permit)


def test_conflicting_entity_versions_fail_closed(
    memory_repository: InMemoryGraphRepository,
) -> None:
    context = context_for("op_one", 1, 1)
    memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),)), context
    )
    receipt = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "Other"),)), context
    )
    commit, permit = commit_and_permit_for(receipt)
    memory_repository.append_commit(commit, permit)

    with pytest.raises(RepositoryCorruptionError, match="ENTITY_VERSION_CORRUPTION"):
        memory_repository.load_committed()


def test_requested_revision_loads_the_matching_committed_snapshot(
    memory_repository: InMemoryGraphRepository,
) -> None:
    person_id = PersonId("per_one")
    first_receipt = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(person_id, "First"),)),
        context_for("op_one", 1, 1),
    )
    first_commit, first_permit = commit_and_permit_for(first_receipt)
    memory_repository.append_commit(first_commit, first_permit)
    second_receipt = memory_repository.stage(
        GraphWriteSet(person_upserts=(Person(person_id, "Second"),)),
        context_for("op_two", 2, 2),
    )
    second_commit, second_permit = commit_and_permit_for(second_receipt)
    memory_repository.append_commit(second_commit, second_permit)

    assert memory_repository.load_committed(1).people[person_id].full_name == "First"
    assert memory_repository.load_committed(2).people[person_id].full_name == "Second"


def test_explicit_memory_revision_must_name_an_exact_commit(
    memory_repository: InMemoryGraphRepository,
) -> None:
    assert memory_repository.load_committed(0).state.revision == 0
    with pytest.raises(ValueError, match="committed revision"):
        memory_repository.load_committed(1)
    with pytest.raises(ValueError, match="negative"):
        memory_repository.load_committed(-1)

    receipt = memory_repository.stage(GraphWriteSet(), context_for("op_one", 1, 1))
    commit, permit = commit_and_permit_for(receipt)
    memory_repository.append_commit(commit, permit)

    assert memory_repository.load_committed(1).state.revision == 1
    with pytest.raises(ValueError, match="committed revision"):
        memory_repository.load_committed(0)
    with pytest.raises(ValueError, match="committed revision"):
        memory_repository.load_committed(2)


@pytest.mark.parametrize(
    "revisions",
    ((0,), (-1,), (1, 3)),
    ids=("zero", "negative", "gap"),
)
def test_memory_commit_history_requires_positive_contiguous_revisions(
    memory_repository: InMemoryGraphRepository,
    revisions: tuple[int, ...],
) -> None:
    memory_repository._commits.extend(  # type: ignore[attr-defined]
        GraphCommit(
            operation_id=OperationId(f"op_{revision}"),
            revision=revision,
            fencing_token=revision,
            permit_id=f"cpr_{revision}",
            semantic_checksum=VALID_CHECKSUM,
            committed_at=COMMITTED_AT,
        )
        for revision in revisions
    )

    with pytest.raises(RepositoryCorruptionError, match="COMMIT_LOG_CORRUPTION"):
        memory_repository.load_committed()


def test_commits_must_be_sequential(
    memory_repository: InMemoryGraphRepository,
) -> None:
    later_receipt = memory_repository.stage(
        GraphWriteSet(), context_for("op_two", 2, 2)
    )
    later_commit, later_permit = commit_and_permit_for(later_receipt)

    with pytest.raises(ValueError, match="sequential"):
        memory_repository.append_commit(later_commit, later_permit)


def test_commit_hash_uses_canonical_utc_timestamp_and_normalized_checksum() -> None:
    commit = GraphCommit(
        operation_id=OperationId("op_one"),
        revision=1,
        fencing_token=1,
        permit_id="cpr_one",
        semantic_checksum="A" * 64,
        committed_at=datetime(
            2026, 8, 5, 17, 30, 45, 123456, tzinfo=timezone(timedelta(hours=5))
        ),
    )

    assert canonical_graph_commit_json(commit) == (
        '{"committed_at":"2026-08-05T12:30:45.123456Z","fencing_token":1,'
        '"operation_id":"op_one","permit_id":"cpr_one","revision":1,'
        f'"semantic_checksum":"{VALID_CHECKSUM}"}}'
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        graph_commit_sha256(replace(commit, committed_at=datetime(2026, 8, 5)))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda commit: replace(commit, operation_id=OperationId("bad")),
        lambda commit: replace(commit, revision=0),
        lambda commit: replace(commit, revision=-1),
        lambda commit: replace(commit, fencing_token=0),
        lambda commit: replace(commit, fencing_token=-1),
        lambda commit: replace(commit, permit_id="bad"),
        lambda commit: replace(commit, semantic_checksum="not-a-checksum"),
        lambda commit: replace(commit, committed_at=datetime(2026, 8, 5)),
    ),
    ids=(
        "operation-id",
        "zero-revision",
        "negative-revision",
        "zero-fence",
        "negative-fence",
        "permit-id",
        "checksum",
        "naive-timestamp",
    ),
)
def test_graph_commit_canonicalization_rejects_invalid_commits(mutate) -> None:
    commit = GraphCommit(
        OperationId("op_one"),
        1,
        1,
        "cpr_one",
        VALID_CHECKSUM,
        COMMITTED_AT,
    )

    with pytest.raises(ValueError):
        canonical_graph_commit_json(mutate(commit))


def test_write_set_json_is_ascii_and_sorted_by_logical_id() -> None:
    write_set = GraphWriteSet(
        person_upserts=(
            Person(PersonId("per_z"), "Zed", version_revision=4),
            Person(PersonId("per_a"), "Ana", version_revision=2),
        ),
        person_tombstones=(PersonId("per_x"), PersonId("per_b")),
    )

    assert canonical_graph_write_set_json(write_set) == (
        '{"person_upserts":[{"archived":false,"birth":null,"death":null,'
        '"full_name":"Ana","gender":"unknown","is_alive":null,'
        '"person_id":"per_a","primary_family_unit_id":null,"version_revision":2},'
        '{"archived":false,"birth":null,"death":null,"full_name":"Zed",'
        '"gender":"unknown","is_alive":null,"person_id":"per_z",'
        '"primary_family_unit_id":null,"version_revision":4}],'
        '"person_tombstones":["per_b","per_x"],"family_unit_upserts":[],'
        '"family_unit_tombstones":[],"parent_child_link_upserts":[],'
        '"parent_child_link_tombstones":[],"unresolved_upserts":[],'
        '"unresolved_tombstones":[]}'
    )


def test_graph_write_set_json_parser_round_trips_all_typed_entity_kinds() -> None:
    write_set = GraphWriteSet(
        person_upserts=(
            Person(
                PersonId("per_one"),
                "Ada",
                Gender.FEMALE,
                PartialDate.parse("1980-04"),
                None,
                True,
                FamilyUnitId("fam_one"),
                False,
                7,
            ),
        ),
        person_tombstones=(PersonId("per_removed"),),
        family_unit_upserts=(
            FamilyUnit(
                FamilyUnitId("fam_one"),
                FamilyUnitKind.UNION,
                PersonId("per_one"),
                PersonId("per_two"),
                UnionStatus.MARRIED,
                PartialDate.parse("2001"),
                None,
                True,
                4,
            ),
        ),
        family_unit_tombstones=(FamilyUnitId("fam_removed"),),
        parent_child_link_upserts=(
            ParentChildLink(
                LinkId("lnk_one"),
                PersonId("per_one"),
                PersonId("per_child"),
                ParentRole.MOTHER,
                RelationshipType.BIOLOGICAL,
                FamilyUnitId("fam_one"),
                5,
            ),
        ),
        parent_child_link_tombstones=(LinkId("lnk_removed"),),
        unresolved_upserts=(
            UnresolvedRelationship(
                UnresolvedRelationshipId("unr_one"),
                PersonId("per_child"),
                UnresolvedRelationshipKind.FATHER,
                "Unknown Father",
                6,
            ),
        ),
        unresolved_tombstones=(UnresolvedRelationshipId("unr_removed"),),
    )

    parsed = graph_write_set_from_json(canonical_graph_write_set_json(write_set))

    assert parsed == write_set


@pytest.mark.parametrize("nested", (False, True), ids=("top-level", "nested"))
def test_graph_write_set_json_parser_rejects_duplicate_object_keys(
    nested: bool,
) -> None:
    encoded = canonical_graph_write_set_json(
        GraphWriteSet(person_upserts=(Person(PersonId("per_one"), "One"),))
    )
    if nested:
        encoded = encoded.replace(
            '"full_name":"One"',
            '"full_name":"One","full_name":"Other"',
            1,
        )
    else:
        encoded = encoded.replace(
            '"person_upserts":',
            '"person_upserts":[],"person_upserts":',
            1,
        )

    with pytest.raises(ValueError, match="duplicate"):
        graph_write_set_from_json(encoded)


def _valid_graph_write_set_json_value() -> dict[str, object]:
    return {
        "person_upserts": [
            {
                "archived": False,
                "birth": {"precision": "day", "value": "1980-04-03"},
                "death": None,
                "full_name": "Ada",
                "gender": "female",
                "is_alive": True,
                "person_id": "per_one",
                "primary_family_unit_id": None,
                "version_revision": 1,
            }
        ],
        "person_tombstones": [],
        "family_unit_upserts": [],
        "family_unit_tombstones": [],
        "parent_child_link_upserts": [],
        "parent_child_link_tombstones": [],
        "unresolved_upserts": [],
        "unresolved_tombstones": [],
    }


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.pop("unresolved_tombstones"),
        lambda value: value.__setitem__("extra", []),
        lambda value: value["person_upserts"][0].__setitem__(  # type: ignore[index,union-attr]
            "contact_email", "private@example.test"
        ),
        lambda value: value["person_upserts"][0].__setitem__(  # type: ignore[index,union-attr]
            "gender", "invalid"
        ),
        lambda value: value["person_upserts"][0].__setitem__(  # type: ignore[index,union-attr]
            "is_alive", 1
        ),
        lambda value: value["person_upserts"][0].__setitem__(  # type: ignore[index,union-attr]
            "person_id", "rec_airtable"
        ),
        lambda value: value["person_upserts"][0]["birth"].__setitem__(  # type: ignore[index,union-attr]
            "precision", "year"
        ),
        lambda value: value["person_upserts"].append(  # type: ignore[union-attr]
            value["person_upserts"][0]  # type: ignore[index]
        ),
        lambda value: value["person_tombstones"].extend(  # type: ignore[union-attr]
            ["per_one", "per_one"]
        ),
    ),
    ids=(
        "missing-top-level-field",
        "extra-top-level-field",
        "contact-field",
        "invalid-enum",
        "bool-as-int",
        "airtable-record-id",
        "date-precision-mismatch",
        "duplicate-upsert",
        "duplicate-upsert-and-tombstones",
    ),
)
def test_graph_write_set_json_parser_rejects_noncanonical_or_unsafe_values(
    mutate,
) -> None:
    value = _valid_graph_write_set_json_value()
    mutate(value)

    with pytest.raises(ValueError):
        graph_write_set_from_json(json.dumps(value))
