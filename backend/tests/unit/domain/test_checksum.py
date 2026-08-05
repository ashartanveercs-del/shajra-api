from dataclasses import replace

import pytest

from domain.checksum import semantic_checksum
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
    GraphSnapshot,
    GraphState,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnionStatus,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)
from tests.fixtures.graphs import duplicate_historical_union_snapshot


def _snapshot() -> GraphSnapshot:
    parent_a_id = PersonId("per_parent_a")
    parent_b_id = PersonId("per_parent_b")
    child_id = PersonId("per_child")
    family_id = FamilyUnitId("fam_parent_union")
    link_id = LinkId("lnk_parent_child")
    unresolved_id = UnresolvedRelationshipId("unr_unknown_parent")

    return GraphSnapshot(
        GraphState(5, OperationId("op_current"), 8, "stored-checksum"),
        {
            parent_a_id: Person(
                parent_a_id,
                "Alex Parent",
                Gender.UNKNOWN,
                PartialDate.parse("1970-04"),
                None,
                True,
                None,
                False,
                version_revision=6,
            ),
            parent_b_id: Person(
                parent_b_id,
                "Blair Parent",
                Gender.FEMALE,
                PartialDate.parse("1972-03-04"),
                None,
                True,
                None,
                False,
                version_revision=7,
            ),
            child_id: Person(
                child_id,
                "Casey Child",
                Gender.MALE,
                PartialDate.parse("2000"),
                None,
                True,
                family_id,
                False,
                version_revision=8,
            ),
        },
        {
            family_id: FamilyUnit(
                family_id,
                FamilyUnitKind.UNION,
                parent_a_id,
                parent_b_id,
                UnionStatus.MARRIED,
                PartialDate.parse("1995-06"),
                None,
                False,
                created_revision=9,
            )
        },
        {
            link_id: ParentChildLink(
                link_id,
                parent_a_id,
                child_id,
                ParentRole.PARENT,
                RelationshipType.BIOLOGICAL,
                family_id,
                created_revision=10,
            )
        },
        {
            unresolved_id: UnresolvedRelationship(
                unresolved_id,
                child_id,
                UnresolvedRelationshipKind.PARENT,
                "  Unknown   Parent  ",
                created_revision=11,
            )
        },
    )


def test_checksum_is_independent_of_mapping_insertion_order():
    snapshot = _snapshot()
    reordered = GraphSnapshot(
        snapshot.state,
        dict(reversed(tuple(snapshot.people.items()))),
        dict(reversed(tuple(snapshot.family_units.items()))),
        dict(reversed(tuple(snapshot.links.items()))),
        dict(reversed(tuple(snapshot.unresolved.items()))),
    )

    assert semantic_checksum(reordered) == semantic_checksum(snapshot)


def test_checksum_changes_when_parent_child_relationship_changes():
    snapshot = _snapshot()
    link_id, link = next(iter(snapshot.links.items()))
    changed = GraphSnapshot(
        snapshot.state,
        snapshot.people,
        snapshot.family_units,
        {link_id: replace(link, relationship_type=RelationshipType.ADOPTIVE)},
        snapshot.unresolved,
    )

    assert semantic_checksum(changed) != semantic_checksum(snapshot)


def test_checksum_excludes_graph_and_entity_revision_metadata():
    snapshot = _snapshot()
    person_id, person = next(iter(snapshot.people.items()))
    family_id, family = next(iter(snapshot.family_units.items()))
    link_id, link = next(iter(snapshot.links.items()))
    unresolved_id, annotation = next(iter(snapshot.unresolved.items()))
    metadata_changed = GraphSnapshot(
        GraphState(99, OperationId("op_replayed"), 101, "different-stored-checksum"),
        {person_id: replace(person, version_revision=102), **{
            key: value for key, value in snapshot.people.items() if key != person_id
        }},
        {family_id: replace(family, created_revision=103)},
        {link_id: replace(link, created_revision=104)},
        {unresolved_id: replace(annotation, created_revision=105)},
    )

    assert semantic_checksum(metadata_changed) == semantic_checksum(snapshot)


def test_checksum_changes_when_distinct_historical_union_is_confirmed():
    unconfirmed = duplicate_historical_union_snapshot(
        confirmed=False,
        status=UnionStatus.DIVORCED,
        ended=True,
    )
    confirmed = duplicate_historical_union_snapshot(
        confirmed=True,
        status=UnionStatus.DIVORCED,
        ended=True,
    )

    assert semantic_checksum(confirmed) != semantic_checksum(unconfirmed)


def test_checksum_rejects_non_snapshot_input():
    with pytest.raises(TypeError):
        semantic_checksum(object())
