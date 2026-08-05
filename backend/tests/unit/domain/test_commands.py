from dataclasses import replace

import pytest

from domain.commands import (
    AddFamilyUnit,
    AddParentChildLink,
    AddPersonVersion,
    AddUnresolvedRelationship,
    ArchivePerson,
    CommandConflict,
    RemoveUnresolvedRelationship,
    SetPrimaryFamilyUnit,
    SupersedeFamilyUnit,
    SupersedeParentChildLink,
    SupersedeUnresolvedRelationship,
    apply_commands,
)
from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    Gender,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnionStatus,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)
from tests.fixtures.graphs import (
    CHILD,
    FAMILY,
    PARENT,
    archived_two_parent_snapshot,
    cousin_union_snapshot,
    duplicate_historical_union_snapshot,
    empty_snapshot,
    remarriage_snapshot,
    repeated_ancestor_snapshot,
    simple_parent_child_snapshot,
    two_parent_family_snapshot,
)


def _family(family_id: FamilyUnitId = FAMILY) -> FamilyUnit:
    return FamilyUnit(family_id, FamilyUnitKind.UNION, PARENT)


def _link(link_id: LinkId) -> ParentChildLink:
    return ParentChildLink(
        link_id,
        PARENT,
        CHILD,
        ParentRole.PARENT,
        RelationshipType.BIOLOGICAL,
        None,
        created_revision=1,
    )


def _annotation(
    unresolved_id: UnresolvedRelationshipId = UnresolvedRelationshipId("unr_unknown"),
) -> UnresolvedRelationship:
    return UnresolvedRelationship(
        unresolved_id,
        CHILD,
        UnresolvedRelationshipKind.PARENT,
        "Unknown Parent",
        created_revision=1,
    )


def test_add_link_returns_a_new_snapshot_without_mutating_input():
    snapshot, parent, child, link_id = simple_parent_child_snapshot(include_link=False)
    link = ParentChildLink(
        link_id,
        parent,
        child,
        ParentRole.PARENT,
        RelationshipType.BIOLOGICAL,
        None,
        created_revision=1,
    )

    result = apply_commands(snapshot, [AddParentChildLink(link)])

    assert result is not snapshot
    assert link_id not in snapshot.links
    assert result.links[link_id] == link


def test_reducer_accepts_cross_reference_errors_for_later_semantic_validation():
    link = _link(LinkId("lnk_unresolved_references"))

    result = apply_commands(empty_snapshot(), [AddParentChildLink(link)])

    assert result.links[link.link_id] == link


def test_add_person_version_inserts_then_replaces_the_stable_person_id():
    snapshot = empty_snapshot()
    first = Person(PARENT, "First Name")
    replacement = replace(first, full_name="Current Name", version_revision=2)

    inserted = apply_commands(snapshot, [AddPersonVersion(first)])
    result = apply_commands(inserted, [AddPersonVersion(replacement)])

    assert PARENT not in snapshot.people
    assert inserted.people[PARENT] == first
    assert result.people[PARENT] == replacement
    assert len(result.people) == 1


def test_add_family_unit_rejects_duplicate_stable_id_without_mutating_input():
    snapshot = empty_snapshot()
    family = _family()
    inserted = apply_commands(snapshot, [AddFamilyUnit(family)])

    with pytest.raises(CommandConflict, match="duplicate family unit"):
        apply_commands(inserted, [AddFamilyUnit(family)])

    assert FAMILY not in snapshot.family_units
    assert inserted.family_units[FAMILY] == family


def test_add_unresolved_relationship_normalizes_name_and_preserves_input_map():
    snapshot = empty_snapshot()
    annotation = replace(_annotation(), unresolved_name="  Unknown   Parent  ")

    result = apply_commands(snapshot, [AddUnresolvedRelationship(annotation)])

    assert annotation.unresolved_id not in snapshot.unresolved
    assert (
        result.unresolved[annotation.unresolved_id].unresolved_name == "Unknown Parent"
    )


def test_unresolved_relationship_rejects_an_empty_normalized_name():
    with pytest.raises(ValueError, match="unresolved_name"):
        replace(_annotation(), unresolved_name=" \t\n ")


def test_set_primary_family_unit_replaces_person_without_mutating_input():
    snapshot, parent, child, _ = simple_parent_child_snapshot()

    result = apply_commands(snapshot, [SetPrimaryFamilyUnit(child, FAMILY)])

    assert snapshot.people[child].primary_family_unit_id is None
    assert result.people[child].primary_family_unit_id == FAMILY
    assert result.people[parent] == snapshot.people[parent]


def test_archive_person_sets_archived_without_mutating_input():
    snapshot, parent, _, _ = simple_parent_child_snapshot()

    result = apply_commands(snapshot, [ArchivePerson(parent)])

    assert snapshot.people[parent].archived is False
    assert result.people[parent].archived is True


def test_supersede_family_unit_replaces_under_same_key_without_old_version():
    snapshot = apply_commands(empty_snapshot(), [AddFamilyUnit(_family())])
    replacement = replace(_family(), status=UnionStatus.MARRIED, created_revision=2)

    result = apply_commands(snapshot, [SupersedeFamilyUnit(FAMILY, replacement)])

    assert result is not snapshot
    assert snapshot.family_units[FAMILY].status is UnionStatus.UNKNOWN
    assert result.family_units[FAMILY] == replacement
    assert len(result.family_units) == 1


def test_supersede_parent_child_link_replaces_under_same_key_without_old_version():
    snapshot, _, _, link_id = simple_parent_child_snapshot()
    replacement = replace(
        snapshot.links[link_id], relationship_type=RelationshipType.ADOPTIVE
    )

    result = apply_commands(snapshot, [SupersedeParentChildLink(link_id, replacement)])

    assert snapshot.links[link_id].relationship_type is RelationshipType.BIOLOGICAL
    assert result.links[link_id] == replacement
    assert len(result.links) == 1


def test_supersede_unresolved_relationship_replaces_under_same_key_without_old_version():
    annotation = _annotation()
    snapshot = apply_commands(empty_snapshot(), [AddUnresolvedRelationship(annotation)])
    replacement = replace(annotation, unresolved_name="Known Later")

    result = apply_commands(
        snapshot,
        [SupersedeUnresolvedRelationship(annotation.unresolved_id, replacement)],
    )

    assert snapshot.unresolved[annotation.unresolved_id] == annotation
    assert result.unresolved[annotation.unresolved_id] == replacement
    assert len(result.unresolved) == 1


def test_remove_unresolved_relationship_deletes_only_from_new_snapshot():
    annotation = _annotation()
    snapshot = apply_commands(empty_snapshot(), [AddUnresolvedRelationship(annotation)])

    result = apply_commands(
        snapshot, [RemoveUnresolvedRelationship(annotation.unresolved_id)]
    )

    assert annotation.unresolved_id in snapshot.unresolved
    assert annotation.unresolved_id not in result.unresolved


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (AddParentChildLink(_link(LinkId("lnk_parent_child"))), "duplicate link"),
        (AddUnresolvedRelationship(_annotation()), "duplicate unresolved relationship"),
    ],
)
def test_add_commands_reject_duplicate_stable_ids(command, message):
    snapshot, _, _, link_id = simple_parent_child_snapshot()
    if isinstance(command, AddUnresolvedRelationship):
        snapshot = apply_commands(snapshot, [command])
    else:
        assert command.link.link_id == link_id

    with pytest.raises(CommandConflict, match=message):
        apply_commands(snapshot, [command])


@pytest.mark.parametrize(
    "command",
    [
        SetPrimaryFamilyUnit(PersonId("per_missing"), None),
        ArchivePerson(PersonId("per_missing")),
        SupersedeFamilyUnit(FAMILY, _family()),
        SupersedeParentChildLink(LinkId("lnk_missing"), _link(LinkId("lnk_missing"))),
        SupersedeUnresolvedRelationship(
            UnresolvedRelationshipId("unr_missing"),
            _annotation(UnresolvedRelationshipId("unr_missing")),
        ),
        RemoveUnresolvedRelationship(UnresolvedRelationshipId("unr_missing")),
    ],
)
def test_commands_reject_missing_targets(command):
    with pytest.raises(CommandConflict, match="missing"):
        apply_commands(empty_snapshot(), [command])


@pytest.mark.parametrize(
    "command",
    [
        SupersedeFamilyUnit(FAMILY, _family(FamilyUnitId("fam_other"))),
        SupersedeParentChildLink(
            LinkId("lnk_parent_child"), _link(LinkId("lnk_other"))
        ),
        SupersedeUnresolvedRelationship(
            UnresolvedRelationshipId("unr_unknown"),
            _annotation(UnresolvedRelationshipId("unr_other")),
        ),
    ],
)
def test_supersede_rejects_replacement_id_mismatch(command):
    if isinstance(command, SupersedeFamilyUnit):
        snapshot = apply_commands(empty_snapshot(), [AddFamilyUnit(_family())])
    elif isinstance(command, SupersedeParentChildLink):
        snapshot, _, _, _ = simple_parent_child_snapshot()
    else:
        snapshot = apply_commands(
            empty_snapshot(), [AddUnresolvedRelationship(_annotation())]
        )

    with pytest.raises(CommandConflict, match="replacement ID"):
        apply_commands(snapshot, [command])


def test_snapshot_nested_maps_cannot_be_mutated_through_frozen_dataclass():
    snapshot = empty_snapshot()

    with pytest.raises(TypeError):
        snapshot.people[PARENT] = Person(PARENT, "Mutated")


def test_model_enums_match_the_resolved_wire_contract():
    assert [member.value for member in Gender] == ["male", "female", "unknown"]
    assert [member.value for member in FamilyUnitKind] == ["single_parent", "union"]
    assert [member.value for member in UnionStatus] == [
        "unknown",
        "married",
        "separated",
        "divorced",
        "widowed",
    ]
    assert [member.value for member in ParentRole] == ["father", "mother", "parent"]
    assert [member.value for member in RelationshipType] == [
        "biological",
        "adoptive",
        "step",
        "guardian",
        "unknown",
    ]
    assert [member.value for member in UnresolvedRelationshipKind] == [
        "father",
        "mother",
        "parent",
        "partner",
    ]


def test_simple_parent_child_fixture_can_omit_only_its_link():
    included, parent, child, link_id = simple_parent_child_snapshot()
    omitted, omitted_parent, omitted_child, omitted_link_id = (
        simple_parent_child_snapshot(False)
    )

    assert (parent, child, link_id) == (omitted_parent, omitted_child, omitted_link_id)
    assert included.people == omitted.people
    assert included.family_units == omitted.family_units
    assert link_id in included.links
    assert omitted.links == {}
    assert included.unresolved == omitted.unresolved == {}


def test_two_parent_family_fixture_has_two_adults_union_child_and_two_biological_links():
    snapshot = two_parent_family_snapshot()
    child = next(
        person for person in snapshot.people.values() if person.full_name == "Child"
    )

    assert len(snapshot.people) == 3
    assert len(snapshot.family_units) == 1
    assert len(snapshot.links) == 2
    assert {link.relationship_type for link in snapshot.links.values()} == {
        RelationshipType.BIOLOGICAL
    }
    assert child.primary_family_unit_id in snapshot.family_units
    assert snapshot.unresolved == {}


def test_remarriage_fixture_has_one_adult_two_distinct_unions_and_one_child_per_union():
    snapshot = remarriage_snapshot()
    unions = list(snapshot.family_units.values())
    shared_adult = next(
        person_id
        for person_id in snapshot.people
        if sum(person_id in (union.adult_a_id, union.adult_b_id) for union in unions)
        == 2
    )

    assert len(unions) == 2
    assert len({union.family_unit_id for union in unions}) == 2
    assert sum(link.parent_id == shared_adult for link in snapshot.links.values()) == 2
    assert len({link.child_id for link in snapshot.links.values()}) == 2
    assert snapshot.unresolved == {}


def test_cousin_union_fixture_is_acyclic_and_has_a_repeated_ancestor_path():
    snapshot = cousin_union_snapshot()
    parents_by_child = {}
    for link in snapshot.links.values():
        parents_by_child.setdefault(link.child_id, set()).add(link.parent_id)

    def ancestors(person_id, seen=frozenset()):
        assert person_id not in seen
        result = set()
        for parent_id in parents_by_child.get(person_id, set()):
            result.add(parent_id)
            result.update(ancestors(parent_id, seen | {person_id}))
        return result

    union = next(
        unit for unit in snapshot.family_units.values() if unit.adult_b_id is not None
    )
    left = ancestors(union.adult_a_id)
    right = ancestors(union.adult_b_id)
    assert left & right
    assert snapshot.unresolved == {}


def test_repeated_ancestor_fixture_has_a_person_reachable_by_two_valid_paths():
    snapshot = repeated_ancestor_snapshot()
    child = next(
        person_id
        for person_id in snapshot.people
        if not any(link.parent_id == person_id for link in snapshot.links.values())
    )
    parent_links = [link for link in snapshot.links.values() if link.child_id == child]
    grandparents = [
        link.parent_id
        for parent_link in parent_links
        for link in snapshot.links.values()
        if link.child_id == parent_link.parent_id
    ]

    assert len(parent_links) == 2
    assert len(grandparents) == 2
    assert len(set(grandparents)) == 1
    assert snapshot.unresolved == {}


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("status", [UnionStatus.UNKNOWN, UnionStatus.DIVORCED])
@pytest.mark.parametrize("ended", [False, True])
def test_duplicate_historical_union_fixture_exposes_requested_union_state(
    confirmed, status, ended
):
    snapshot = duplicate_historical_union_snapshot(confirmed, status, ended)
    unions = list(snapshot.family_units.values())

    assert len(unions) == 2
    assert len({(union.adult_a_id, union.adult_b_id) for union in unions}) == 1
    assert {union.distinct_union_confirmed for union in unions} == {confirmed}
    assert {union.status for union in unions} == {status}
    assert {(union.end is not None) for union in unions} == {ended}
    assert snapshot.unresolved == {}


def test_archived_two_parent_fixture_keeps_archived_adult_family_and_both_parent_links():
    snapshot = archived_two_parent_snapshot()
    archived = [person for person in snapshot.people.values() if person.archived]

    assert len(archived) == 1
    assert len(snapshot.people) == 3
    assert len(snapshot.family_units) == 1
    assert len(snapshot.links) == 2
    assert archived[0].person_id in {link.parent_id for link in snapshot.links.values()}
    assert snapshot.unresolved == {}
