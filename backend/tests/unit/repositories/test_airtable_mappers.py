import importlib
import sys

import pyairtable
import pytest
from pyairtable.formulas import match

from domain.checksum import semantic_checksum
from domain.ids import (
    FamilyUnitId,
    LinkId,
    OperationId,
    PersonId,
    UnresolvedRelationshipId,
    migrated_person_id,
    migrated_unresolved_relationship_id,
)
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    Gender,
    GraphState,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)
from repositories.airtable.legacy import LegacySnapshotRepository
from repositories.airtable.client import AirtableClient
from repositories.airtable.mappers import (
    LiveEntityVersion,
    RepositoryTombstone,
    family_unit_from_row,
    family_unit_to_row,
    legacy_snapshot_from_records,
    map_linked_record_id,
    parent_child_link_from_row,
    parent_child_link_to_row,
    person_from_row,
    person_to_row,
    snapshot_from_rows,
    unresolved_from_row,
    unresolved_to_row,
)
from repositories.protocols import WriteContext


def _context() -> WriteContext:
    return WriteContext(
        operation_id=OperationId("op_write"),
        revision=7,
        fencing_token=23,
        actor_id="actor",
        request_id="request",
    )


def _person(person_id: str, name: str) -> Person:
    return Person(PersonId(person_id), name, Gender.MALE, is_alive=None)


def test_linked_record_id_maps_one_value_through_the_explicit_id_map() -> None:
    resolved = map_linked_record_id(
        ["rec_parent"],
        {"rec_parent": PersonId("per_parent")},
        "FatherRecordId",
    )

    assert resolved == PersonId("per_parent")


def test_linked_record_id_maps_an_empty_list_to_none() -> None:
    assert map_linked_record_id([], {}, "FatherRecordId") is None


def test_linked_record_id_rejects_multiple_values_for_a_single_relation() -> None:
    with pytest.raises(ValueError, match="FatherRecordId must contain at most one record"):
        map_linked_record_id(["rec_one", "rec_two"], {}, "FatherRecordId")


def test_legacy_mapper_creates_deterministic_unresolved_slots_without_splitting() -> None:
    records = [
        {
            "id": "rec_child",
            "fields": {
                "FullName": "Child",
                "Gender": "Female",
                "FatherName": "Father Name",
                "MotherName": "Mother Name",
                "SpouseName": "Spouse One, Spouse Two",
            },
        }
    ]

    first = legacy_snapshot_from_records(records)
    second = legacy_snapshot_from_records(records)

    assert [annotation.unresolved_id for annotation in first.unresolved] == [
        migrated_unresolved_relationship_id("ApprovedMembers", "rec_child", "FatherName#0"),
        migrated_unresolved_relationship_id("ApprovedMembers", "rec_child", "MotherName#0"),
        migrated_unresolved_relationship_id("ApprovedMembers", "rec_child", "SpouseName#0"),
    ]
    assert [annotation.unresolved_name for annotation in first.unresolved] == [
        "Father Name",
        "Mother Name",
        "Spouse One, Spouse Two",
    ]
    assert first.unresolved == second.unresolved


def test_legacy_mapper_emits_only_exact_record_id_parent_links() -> None:
    records = [
        {"id": "rec_parent", "fields": {"FullName": "Parent", "Gender": "Male"}},
        {
            "id": "rec_child",
            "fields": {
                "FullName": "Child",
                "FatherRecordId": ["rec_parent"],
                "FatherName": "A different displayed name",
            },
        },
    ]

    snapshot = legacy_snapshot_from_records(records)

    assert [(link.parent_id, link.child_id, link.role) for link in snapshot.parent_child_links] == [
        (
            migrated_person_id("ApprovedMembers", "rec_parent"),
            migrated_person_id("ApprovedMembers", "rec_child"),
            ParentRole.FATHER,
        )
    ]
    assert snapshot.unresolved == ()


def test_public_legacy_snapshot_omits_contact_and_source_metadata() -> None:
    snapshot = legacy_snapshot_from_records(
        [
            {
                "id": "rec_person",
                "fields": {
                    "FullName": "Private Person",
                    "Email": "private@example.test",
                    "PhoneNumber": "+10000000000",
                },
            }
        ]
    )

    public_values = snapshot.public_values()

    assert "Email" not in str(public_values)
    assert "PhoneNumber" not in str(public_values)
    assert "rec_person" not in str(public_values)
    assert "SourceRecordId" not in str(public_values)
    assert snapshot.people[0].source_record_id == "rec_person"


def test_source_metadata_does_not_change_the_domain_snapshot_or_checksum() -> None:
    state = GraphState(1, OperationId("op_one"), 3, "checksum")
    original = person_to_row(_person("per_one", "Ada"), _context())
    with_provenance = {**original, "SourceRecordId": "rec_one", "MigrationRunId": "mig_one"}
    other_provenance = {**original, "SourceRecordId": "rec_two", "MigrationRunId": "mig_two"}

    first = snapshot_from_rows(state, person_rows=[with_provenance])
    second = snapshot_from_rows(state, person_rows=[other_provenance])

    assert first == second
    assert semantic_checksum(first) == semantic_checksum(second)


@pytest.mark.parametrize(
    ("to_row", "from_row", "entity"),
    (
        (person_to_row, person_from_row, Person(PersonId("per_one"), "Ada")),
        (
            family_unit_to_row,
            family_unit_from_row,
            FamilyUnit(FamilyUnitId("fam_one"), FamilyUnitKind.SINGLE_PARENT, PersonId("per_one")),
        ),
        (
            parent_child_link_to_row,
            parent_child_link_from_row,
            ParentChildLink(
                LinkId("lnk_one"),
                PersonId("per_parent"),
                PersonId("per_child"),
                ParentRole.FATHER,
                RelationshipType.BIOLOGICAL,
                None,
            ),
        ),
        (
            unresolved_to_row,
            unresolved_from_row,
            UnresolvedRelationship(
                UnresolvedRelationshipId("unr_one"),
                PersonId("per_one"),
                UnresolvedRelationshipKind.FATHER,
                "Unknown Father",
            ),
        ),
    ),
)
def test_entity_mappers_persist_authorization_tuple_and_tombstones(
    to_row, from_row, entity
) -> None:
    row = to_row(entity, _context())
    tombstone = from_row({**row, "IsTombstone": True})

    assert row["Revision"] == 7
    assert row["OperationId"] == "op_write"
    assert row["FencingToken"] == 23
    assert row["IsTombstone"] is False
    assert isinstance(tombstone, RepositoryTombstone)
    assert tombstone.revision == 7
    assert tombstone.operation_id == OperationId("op_write")
    assert tombstone.fencing_token == 23


@pytest.mark.parametrize(
    ("to_row", "from_row", "entity"),
    (
        (person_to_row, person_from_row, Person(PersonId("per_live"), "Ada")),
        (
            family_unit_to_row,
            family_unit_from_row,
            FamilyUnit(FamilyUnitId("fam_live"), FamilyUnitKind.SINGLE_PARENT, PersonId("per_live")),
        ),
        (
            parent_child_link_to_row,
            parent_child_link_from_row,
            ParentChildLink(
                LinkId("lnk_live"),
                PersonId("per_parent"),
                PersonId("per_child"),
                ParentRole.FATHER,
                RelationshipType.BIOLOGICAL,
                None,
            ),
        ),
        (
            unresolved_to_row,
            unresolved_from_row,
            UnresolvedRelationship(
                UnresolvedRelationshipId("unr_live"),
                PersonId("per_live"),
                UnresolvedRelationshipKind.FATHER,
                "Unknown Father",
            ),
        ),
    ),
)
def test_live_entity_versions_preserve_full_authorization_metadata(
    to_row, from_row, entity
) -> None:
    row = to_row(entity, _context())
    live_version = from_row(row)
    tombstone = from_row({**row, "IsTombstone": True})

    assert isinstance(live_version, LiveEntityVersion)
    assert live_version.entity == entity
    assert live_version.revision == 7
    assert live_version.operation_id == OperationId("op_write")
    assert live_version.fencing_token == 23
    assert live_version.is_tombstone is False
    assert isinstance(tombstone, RepositoryTombstone)
    assert tombstone.is_tombstone is True
    assert not isinstance(tombstone, LiveEntityVersion)


class FakeRateLimitError(Exception):
    def __init__(self, retry_after: str | None = None) -> None:
        self.response = type(
            "Response",
            (),
            {"status_code": 429, "headers": {"Retry-After": retry_after} if retry_after else {}},
        )()


class FakeApprovedMembersTable:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def all(self) -> list[dict[str, object]]:
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]


def test_legacy_repository_retries_only_rate_limits_with_bounded_backoff() -> None:
    table = FakeApprovedMembersTable(
        [FakeRateLimitError("2"), FakeRateLimitError(), [{"id": "rec_one", "fields": {"FullName": "Ada"}}]]
    )
    delays: list[float] = []
    repository = LegacySnapshotRepository(table, sleep=delays.append)

    snapshot = repository.load()

    assert table.calls == 3
    assert delays == [2.0, 2.0]
    assert snapshot.people[0].source_record_id == "rec_one"


def test_legacy_repository_does_not_retry_non_rate_limit_errors() -> None:
    class FakeServerError(Exception):
        response = type("Response", (), {"status_code": 500, "headers": {}})()

    table = FakeApprovedMembersTable([FakeServerError()])
    repository = LegacySnapshotRepository(table, sleep=lambda _: None)

    with pytest.raises(FakeServerError):
        repository.load()

    assert table.calls == 1


class FakeFacadeTable:
    def __init__(self) -> None:
        self.formulas: list[str] = []
        self.creates: list[dict[str, object]] = []

    def all(self, *, formula: str | None = None) -> list[dict[str, object]]:
        if formula is not None:
            self.formulas.append(formula)
        return []

    def create(self, fields: dict[str, object]) -> dict[str, object]:
        self.creates.append(fields)
        return {"id": "rec_new", "fields": fields}


class FakeFacadeApi:
    def __init__(self) -> None:
        self.tables: dict[str, FakeFacadeTable] = {}
        self.marker = "delegated"

    def table(self, _base_id: str, name: str) -> FakeFacadeTable:
        return self.tables.setdefault(name, FakeFacadeTable())


class FakeFacadeClient:
    def __init__(self, api: FakeFacadeApi) -> None:
        self._api = api

    def table(self, name: str) -> FakeFacadeTable:
        return self._api.table("app-test", name)


def test_legacy_facade_is_lazy_uses_safe_formulas_and_gates_mutations(monkeypatch) -> None:
    api = FakeFacadeApi()
    api_calls: list[str] = []

    def api_factory(token: str) -> FakeFacadeApi:
        api_calls.append(token)
        return api

    monkeypatch.setattr(pyairtable, "Api", api_factory)
    monkeypatch.delitem(sys.modules, "airtable_client", raising=False)
    facade = importlib.import_module("airtable_client")

    assert api_calls == []
    monkeypatch.setattr(facade, "_client", FakeFacadeClient(api))

    value = "Robert') & DELETE() & ('"
    facade.get_members_by_filter("FullName", value)

    assert api.tables["ApprovedMembers"].formulas == [str(match({"FullName": value}))]

    monkeypatch.setenv("SHAJRA_LEGACY_MUTATIONS_ENABLED", "false")
    with pytest.raises(RuntimeError, match="Legacy Airtable mutations are disabled"):
        facade.create_member({"FullName": "Blocked"})
    assert api.tables["ApprovedMembers"].creates == []


def test_legacy_facade_exports_a_lazy_api_proxy(monkeypatch) -> None:
    api = FakeFacadeApi()
    api_calls: list[str] = []

    def api_factory(token: str) -> FakeFacadeApi:
        api_calls.append(token)
        return api

    facade = importlib.import_module("airtable_client")
    monkeypatch.setattr(
        facade,
        "_client",
        AirtableClient("test-token", "app-test", api_factory=api_factory),
    )

    legacy_api = facade.api

    assert api_calls == []
    assert legacy_api.table("app-test", "ApprovedMembers") is api.tables["ApprovedMembers"]
    assert api_calls == ["test-token"]
    assert legacy_api.marker == "delegated"
