from dataclasses import FrozenInstanceError, asdict, fields, replace
from random import Random

import pytest

from domain.checksum import semantic_checksum
from domain.dates import PartialDate
from domain.ids import FamilyUnitId, LinkId, PersonId
from domain.issues import GraphIssue, IssueSeverity, ValidationReport
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    GraphSnapshot,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
)
from domain.projection import (
    PUBLIC_ISSUE_MESSAGES,
    AdultMembershipEdge,
    DescendantEdge,
    GraphComponent,
    InvalidGraphProjection,
    ProjectedFamilyUnit,
    ProjectedLink,
    ProjectedPerson,
    PublicGraphIssue,
    RelationshipReference,
    TreeProjection,
    _components,
    project_graph,
)
from domain.validation import validate_snapshot
from tests.fixtures.graphs import (
    CHILD,
    FAMILY,
    PARENT,
    adoptive_cycle_snapshot,
    archived_two_parent_snapshot,
    archived_reference_candidates_snapshot,
    canonical_pedigree_collapse_snapshot,
    cousin_union_snapshot,
    deterministic_projection_snapshot,
    disconnected_components_snapshot,
    empty_snapshot,
    partner_only_snapshot,
    remarriage_snapshot,
    repeated_ancestor_snapshot,
    simple_parent_child_snapshot,
    shared_family_pedigree_collapse_snapshot,
    single_parent_family_snapshot,
    two_parent_family_snapshot,
)


def _ids(records: tuple[object, ...], attribute: str) -> tuple[object, ...]:
    return tuple(getattr(record, attribute) for record in records)


def _shuffled(mapping: object, seed: int) -> dict[object, object]:
    items = list(mapping.items())  # type: ignore[attr-defined]
    Random(seed).shuffle(items)
    return dict(items)


def _assert_all_public_ids_resolve(projection: TreeProjection) -> None:
    person_ids = set(_ids(projection.people, "person_id"))
    family_ids = set(_ids(projection.family_units, "family_unit_id"))
    link_ids = set(_ids(projection.parent_child_links, "link_id"))

    for family in projection.family_units:
        assert family.adult_a_id in person_ids
        assert family.adult_b_id is None or family.adult_b_id in person_ids
    for link in projection.parent_child_links:
        assert link.parent_id in person_ids
        assert link.child_id in person_ids
        assert link.family_unit_id is None or link.family_unit_id in family_ids
    for edge in projection.adult_memberships:
        assert edge.adult_id in person_ids
        assert edge.family_unit_id in family_ids
    for edge in projection.descendant_edges:
        assert edge.child_id in person_ids
        assert edge.family_unit_id in family_ids
    for reference in projection.references:
        assert reference.source_person_id in person_ids
        assert reference.target_person_id in person_ids
        assert (
            reference.family_unit_id is None
            or reference.family_unit_id in family_ids
        )
    for component in projection.components:
        assert set(component.root_person_ids) <= person_ids
        assert set(component.person_ids) <= person_ids
        assert set(component.family_unit_ids) <= family_ids
        assert set(component.link_ids) <= link_ids
    for issue in projection.issues:
        assert set(issue.person_ids) <= person_ids
        assert set(issue.family_unit_ids) <= family_ids
        assert set(issue.link_ids) <= link_ids


def test_public_projection_records_have_only_the_approved_fields_and_are_frozen():
    assert [field.name for field in fields(ProjectedPerson)] == [
        "person_id",
        "full_name",
        "gender",
        "birth",
        "death",
        "is_alive",
    ]
    assert [field.name for field in fields(ProjectedFamilyUnit)] == [
        "family_unit_id",
        "kind",
        "adult_a_id",
        "adult_b_id",
        "status",
        "start",
        "end",
    ]
    assert [field.name for field in fields(ProjectedLink)] == [
        "link_id",
        "parent_id",
        "child_id",
        "role",
        "relationship_type",
        "family_unit_id",
        "primary",
    ]
    assert [field.name for field in fields(AdultMembershipEdge)] == [
        "edge_id",
        "family_unit_id",
        "adult_id",
        "slot",
    ]
    assert [field.name for field in fields(DescendantEdge)] == [
        "edge_id",
        "family_unit_id",
        "child_id",
    ]
    assert [field.name for field in fields(RelationshipReference)] == [
        "reference_id",
        "source_person_id",
        "target_person_id",
        "family_unit_id",
        "relationship_type",
        "label",
    ]
    assert [field.name for field in fields(GraphComponent)] == [
        "component_id",
        "root_person_ids",
        "person_ids",
        "family_unit_ids",
        "link_ids",
    ]
    assert [field.name for field in fields(PublicGraphIssue)] == [
        "code",
        "severity",
        "message",
        "person_ids",
        "family_unit_ids",
        "link_ids",
    ]
    assert [field.name for field in fields(TreeProjection)] == [
        "schema_version",
        "revision",
        "semantic_checksum",
        "status",
        "people",
        "family_units",
        "parent_child_links",
        "adult_memberships",
        "descendant_edges",
        "references",
        "components",
        "issues",
        "unresolved_count",
    ]

    person = project_graph(two_parent_family_snapshot()).people[0]
    with pytest.raises(FrozenInstanceError):
        person.full_name = "Changed"  # type: ignore[misc]


def test_empty_snapshot_projects_an_empty_public_graph():
    projection = project_graph(empty_snapshot(revision=4))

    assert projection.schema_version == "2"
    assert projection.revision == 4
    assert projection.status == "empty"
    assert projection.people == ()
    assert projection.family_units == ()
    assert projection.parent_child_links == ()
    assert projection.adult_memberships == ()
    assert projection.descendant_edges == ()
    assert projection.references == ()
    assert projection.components == ()
    assert projection.issues == ()
    assert projection.unresolved_count == 0


def test_single_parent_family_projects_one_real_unit_and_one_descendant_edge():
    projection = project_graph(single_parent_family_snapshot())

    assert _ids(projection.adult_memberships, "edge_id") == (
        "adult:fam_single_parent:per_single_parent",
    )
    assert projection.adult_memberships[0].slot == "adult_a"
    assert _ids(projection.descendant_edges, "edge_id") == (
        "child:fam_single_parent:per_single_child",
    )
    assert projection.components == (
        GraphComponent(
            "cmp_6ecc8d1c911e3469a0cc7602fb1bc3004df89fc86fd34daa975d5efc7107a89d",
            (PersonId("per_single_parent"),),
            (PersonId("per_single_child"), PersonId("per_single_parent")),
            (FamilyUnitId("fam_single_parent"),),
            (LinkId("lnk_single_parent_child"),),
        ),
    )


def test_two_parent_family_keeps_raw_detail_and_emits_canonical_edges_once():
    projection = project_graph(two_parent_family_snapshot())

    assert _ids(projection.parent_child_links, "link_id") == (
        LinkId("lnk_parent_a_child"),
        LinkId("lnk_parent_b_child"),
    )
    assert all(link.primary for link in projection.parent_child_links)
    assert _ids(projection.adult_memberships, "edge_id") == (
        "adult:fam_primary:per_parent",
        "adult:fam_primary:per_parent_b",
    )
    assert _ids(projection.descendant_edges, "edge_id") == (
        "child:fam_primary:per_child",
    )
    assert _ids(projection.components, "component_id") == (
        "cmp_3e074eac001ec8225146d43a80489c3818db0053421ec62c4a5ca96b616276f9",
    )


def test_canonical_adult_slots_are_ordered_by_person_id():
    snapshot = two_parent_family_snapshot()
    family = snapshot.family_units[FAMILY]
    swapped = GraphSnapshot(
        snapshot.state,
        snapshot.people,
        {
            FAMILY: replace(
                family,
                adult_a_id=family.adult_b_id,
                adult_b_id=family.adult_a_id,
            )
        },
        snapshot.links,
        snapshot.unresolved,
    )

    projection = project_graph(swapped)

    assert projection.family_units[0].adult_a_id == PARENT
    assert projection.family_units[0].adult_b_id == PersonId("per_parent_b")
    assert [(edge.adult_id, edge.slot) for edge in projection.adult_memberships] == [
        (PARENT, "adult_a"),
        (PersonId("per_parent_b"), "adult_b"),
    ]


def test_remarriage_projects_both_unions_with_stable_canonical_edge_ids():
    projection = project_graph(remarriage_snapshot())

    assert _ids(projection.adult_memberships, "edge_id") == (
        "adult:fam_first_union:per_former_partner",
        "adult:fam_first_union:per_parent",
        "adult:fam_second_union:per_current_partner",
        "adult:fam_second_union:per_parent",
    )
    assert _ids(projection.descendant_edges, "edge_id") == (
        "child:fam_first_union:per_first_child",
        "child:fam_second_union:per_second_child",
    )
    assert len(projection.components) == 1
    assert projection.components[0].root_person_ids == (
        PersonId("per_current_partner"),
        PersonId("per_former_partner"),
        PARENT,
    )


def test_cousin_union_keeps_partner_unit_without_promoting_raw_ancestry_links():
    projection = project_graph(cousin_union_snapshot())

    assert _ids(projection.adult_memberships, "edge_id") == (
        "adult:fam_cousin_union:per_cousin_a",
        "adult:fam_cousin_union:per_cousin_b",
    )
    assert projection.descendant_edges == ()
    assert len(projection.components) == 4
    assert {reference.label for reference in projection.references} == {
        "non_primary"
    }


def test_familyless_repeated_ancestor_paths_remain_child_to_parent_non_primary_references():
    projection = project_graph(repeated_ancestor_snapshot())
    primary_ids = [person.person_id for person in projection.people]
    assert len(primary_ids) == len(set(primary_ids))
    assert projection.references
    assert projection.references[0].target_person_id in set(primary_ids)

    assert set(_ids(projection.references, "reference_id")) == {
        "ref_0175897ff47fc1771c6964abb73eb88f8b1284621dcff8e2d19d9116a28d9c08",
        "ref_01ecb8eaaaf46982c5e2a23d0e4a6e52c2993d494e9ed7ba898fb44db4ef9612",
        "ref_1ec9ddb88af799ec3c3bcddb82b6f8b3604263f75abf47bb5e81455e69beb3e7",
        "ref_541df4274b3cb385ec39efb05d120dfd4577c749ebcd7602254e850e9e6a234a",
    }
    assert {reference.label for reference in projection.references} == {"non_primary"}
    assert {
        (reference.source_person_id, reference.target_person_id)
        for reference in projection.references
    } == {
        (PersonId("per_descendant"), PersonId("per_path_a")),
        (PersonId("per_descendant"), PersonId("per_path_b")),
        (PersonId("per_path_a"), PersonId("per_ancestor")),
        (PersonId("per_path_b"), PersonId("per_ancestor")),
    }


def test_canonical_primary_pedigree_collapse_references_the_repeated_ancestor():
    projection = project_graph(canonical_pedigree_collapse_snapshot())

    assert projection.references == (
        RelationshipReference(
            "ref_f058b36ec0333aa35a4a1c5bbbc45ca853485c2885eb50a70ae5265f12e7130c",
            PersonId("per_canonical_path_b"),
            PersonId("per_canonical_ancestor"),
            FamilyUnitId("fam_canonical_path_b"),
            RelationshipType.BIOLOGICAL,
            "repeated_ancestor",
        ),
    )
    assert projection.components[0].root_person_ids == (
        PersonId("per_canonical_ancestor"),
    )


def test_siblings_in_one_primary_family_do_not_repeat_the_same_ancestry_path():
    snapshot = single_parent_family_snapshot()
    sibling = PersonId("per_single_sibling")
    sibling_link = LinkId("lnk_single_parent_sibling")
    with_sibling = GraphSnapshot(
        snapshot.state,
        {
            **snapshot.people,
            sibling: Person(
                sibling,
                "Single Sibling",
                primary_family_unit_id=FamilyUnitId("fam_single_parent"),
            ),
        },
        snapshot.family_units,
        {
            **snapshot.links,
            sibling_link: ParentChildLink(
                sibling_link,
                PersonId("per_single_parent"),
                sibling,
                ParentRole.PARENT,
                RelationshipType.BIOLOGICAL,
                FamilyUnitId("fam_single_parent"),
            ),
        },
        snapshot.unresolved,
    )

    assert project_graph(with_sibling).references == ()


def test_one_descendant_path_repeats_ancestor_through_shared_family_siblings():
    projection = project_graph(shared_family_pedigree_collapse_snapshot())

    assert projection.references == (
        RelationshipReference(
            "ref_875d1bb48a6328792fc320012a69e17046731ea901d54990eedc96b96290c0ce",
            PersonId("per_shared_sibling_b"),
            PersonId("per_shared_ancestor"),
            FamilyUnitId("fam_shared_siblings"),
            RelationshipType.BIOLOGICAL,
            "repeated_ancestor",
        ),
    )


def test_partner_only_component_has_two_roots_and_no_fake_descendants():
    projection = project_graph(partner_only_snapshot())

    assert projection.descendant_edges == ()
    assert projection.components == (
        GraphComponent(
            "cmp_e532763cbb5dac7c36d0530f7f6560cef523ee74128289fd67b421250a541a62",
            (PersonId("per_partner_a"), PersonId("per_partner_b")),
            (PersonId("per_partner_a"), PersonId("per_partner_b")),
            (FamilyUnitId("fam_partner_only"),),
            (),
        ),
    )


def test_disconnected_family_units_project_as_two_stable_components():
    projection = project_graph(disconnected_components_snapshot())

    assert len(projection.components) == 2
    assert _ids(projection.components, "component_id") == (
        "cmp_cdec2c68375c3e5955f99c25c814429c1bf8e38b9a8ba1afd3948a608bdf3190",
        "cmp_dca898c345f40dcebe383c84c4501fcdc42017b9e711b8bf7c8c0a0ee9312069",
    )
    assert [component.person_ids for component in projection.components] == [
        (PersonId("per_disconnected_a"), PersonId("per_disconnected_b")),
        (PersonId("per_disconnected_c"), PersonId("per_disconnected_d")),
    ]
    assert all(len(component.root_person_ids) == 2 for component in projection.components)


def test_non_primary_raw_link_becomes_a_full_sha256_reference():
    snapshot, _, _, _ = simple_parent_child_snapshot()

    projection = project_graph(snapshot)

    assert projection.references == (
        RelationshipReference(
            "ref_823c82e35ecb3f7285560aeaef7a612cc10ecb215170c1b1ac5d917bbf3d06dc",
            CHILD,
            PARENT,
            None,
            RelationshipType.BIOLOGICAL,
            "non_primary",
        ),
    )


def test_family_link_outside_primary_placement_is_a_cross_family_reference():
    snapshot = two_parent_family_snapshot()
    without_primary = GraphSnapshot(
        snapshot.state,
        {**snapshot.people, CHILD: replace(snapshot.people[CHILD], primary_family_unit_id=None)},
        snapshot.family_units,
        snapshot.links,
        snapshot.unresolved,
    )

    projection = project_graph(without_primary)

    assert projection.descendant_edges == ()
    assert {reference.label for reference in projection.references} == {
        "cross_family"
    }
    assert {reference.source_person_id for reference in projection.references} == {
        CHILD
    }
    assert {reference.target_person_id for reference in projection.references} == {
        PARENT,
        PersonId("per_parent_b"),
    }
    assert all(not link.primary for link in projection.parent_child_links)


def test_guardian_link_is_detail_only_even_when_family_matches_primary_placement():
    adult = PersonId("per_guardian")
    child = PersonId("per_guardian_child")
    family_id = FamilyUnitId("fam_guardian")
    link_id = LinkId("lnk_guardian")
    snapshot = GraphSnapshot(
        empty_snapshot().state,
        {
            adult: Person(adult, "Guardian"),
            child: Person(child, "Child", primary_family_unit_id=family_id),
        },
        {
            family_id: FamilyUnit(
                family_id,
                FamilyUnitKind.SINGLE_PARENT,
                adult,
            )
        },
        {
            link_id: ParentChildLink(
                link_id,
                adult,
                child,
                ParentRole.PARENT,
                RelationshipType.GUARDIAN,
                family_id,
            )
        },
        {},
    )

    projection = project_graph(snapshot)

    assert projection.parent_child_links[0].primary is True
    assert projection.descendant_edges == ()
    assert projection.references == (
        RelationshipReference(
            "ref_e196b02aa02b171d3029947f1e73dfa5e62f2d54359f2605195e0c43f75c9c0b",
            child,
            adult,
            family_id,
            RelationshipType.GUARDIAN,
            "non_primary",
        ),
    )
    assert any(
        component.root_person_ids == (child,)
        for component in projection.components
    )


def test_projection_is_deterministic_across_ten_four_map_input_shuffles():
    snapshot = deterministic_projection_snapshot()
    expected = project_graph(snapshot)
    expected_dict = asdict(expected)
    expected_component_ids = _ids(expected.components, "component_id")
    expected_reference_ids = _ids(expected.references, "reference_id")

    for seed in range(10):
        shuffled = GraphSnapshot(
            snapshot.state,
            _shuffled(snapshot.people, seed),
            _shuffled(snapshot.family_units, seed + 10),
            _shuffled(snapshot.links, seed + 20),
            _shuffled(snapshot.unresolved, seed + 30),
        )
        actual = project_graph(shuffled)

        assert asdict(actual) == expected_dict
        assert _ids(actual.components, "component_id") == expected_component_ids
        assert _ids(actual.references, "reference_id") == expected_reference_ids


def test_component_link_ids_are_bucketed_in_one_pass_after_roots_finalize():
    class CountingLinks(tuple):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    projection = project_graph(disconnected_components_snapshot())
    links = CountingLinks(projection.parent_child_links)

    components = _components(
        projection.people,
        projection.family_units,
        links,
        projection.adult_memberships,
        projection.descendant_edges,
    )

    assert components == projection.components
    assert links.iterations == 1


def test_projection_carries_the_snapshot_semantic_checksum_without_rehashing():
    snapshot = deterministic_projection_snapshot()
    projection = project_graph(snapshot)

    assert projection.semantic_checksum == semantic_checksum(snapshot)
    with pytest.raises(TypeError):
        semantic_checksum(projection)  # type: ignore[arg-type]


def test_blocking_validation_raises_with_the_complete_report():
    snapshot = adoptive_cycle_snapshot()
    report = validate_snapshot(snapshot)

    with pytest.raises(InvalidGraphProjection) as caught:
        project_graph(snapshot)

    assert caught.value.report == report


def test_allowlisted_warning_uses_only_fixed_public_message():
    parent = PersonId("per_young_parent")
    child = PersonId("per_young_child")
    link_id = LinkId("lnk_young_parent")
    snapshot = GraphSnapshot(
        empty_snapshot().state,
        {
            parent: Person(parent, "Parent", birth=PartialDate.parse("2000")),
            child: Person(child, "Child", birth=PartialDate.parse("2005")),
        },
        {},
        {
            link_id: ParentChildLink(
                link_id,
                parent,
                child,
                ParentRole.PARENT,
                RelationshipType.BIOLOGICAL,
                None,
            )
        },
        {},
    )
    internal_message = next(
        issue.message
        for issue in validate_snapshot(snapshot).issues
        if issue.code == "SUSPICIOUS_PARENT_AGE"
    )

    projection = project_graph(snapshot)
    issue = next(issue for issue in projection.issues if issue.code == "SUSPICIOUS_PARENT_AGE")

    assert projection.status == "partial"
    assert issue.message == "Some dates may need review."
    assert issue.message == PUBLIC_ISSUE_MESSAGES["SUSPICIOUS_PARENT_AGE"]
    assert issue.message != internal_message


def test_unknown_warning_falls_back_to_generic_allowlisted_issue(monkeypatch):
    private_text = "Provider row Secret Person requires manual inspection."

    def warning_report(_: GraphSnapshot) -> ValidationReport:
        return ValidationReport(
            (
                GraphIssue(
                    "PRIVATE_PROVIDER_WARNING",
                    IssueSeverity.WARNING,
                    private_text,
                ),
            )
        )

    monkeypatch.setattr("domain.projection.validate_snapshot", warning_report)

    projection = project_graph(empty_snapshot())

    assert projection.status == "partial"
    assert projection.issues == (
        PublicGraphIssue(
            "GRAPH_WARNING",
            IssueSeverity.WARNING,
            "Some family-tree details need review.",
        ),
    )
    assert private_text not in repr(projection)


def test_unresolved_annotations_only_affect_count_and_partial_status():
    snapshot = deterministic_projection_snapshot()

    projection = project_graph(snapshot)

    assert projection.status == "partial"
    assert projection.unresolved_count == 2
    assert "Unknown Father" not in repr(projection)
    assert "Unknown Partner" not in repr(projection)


def test_archived_family_filter_removes_incident_topology_without_dangling_ids():
    snapshot = archived_two_parent_snapshot()
    before = (
        dict(snapshot.people),
        dict(snapshot.family_units),
        dict(snapshot.links),
        dict(snapshot.unresolved),
    )

    projection = project_graph(snapshot)

    assert _ids(projection.people, "person_id") == (
        CHILD,
        PersonId("per_retained_adult"),
    )
    assert projection.family_units == ()
    assert projection.parent_child_links == ()
    assert projection.adult_memberships == ()
    assert projection.descendant_edges == ()
    assert projection.references == ()
    assert {component.root_person_ids for component in projection.components} == {
        (CHILD,),
        (PersonId("per_retained_adult"),),
    }
    assert set(_ids(projection.components, "component_id")) == {
        "cmp_4289f1dc83e667009bb0803ba6c5436e9a9542fee7eab88c336a99fb6c4b4c9c",
        "cmp_5cafbf969e7e7095f311e51af4c75993dafa643480aa8f1de90f6ed02ef73818",
    }
    assert projection.status == "partial"
    assert projection.issues == (
        PublicGraphIssue(
            "ARCHIVED_RELATIONSHIP_OMITTED",
            IssueSeverity.WARNING,
            "Some relationships are hidden because an archived person is involved.",
        ),
    )
    _assert_all_public_ids_resolve(projection)
    assert before == (
        dict(snapshot.people),
        dict(snapshot.family_units),
        dict(snapshot.links),
        dict(snapshot.unresolved),
    )


def test_archived_filter_removes_real_guardian_and_cross_family_references():
    snapshot = archived_reference_candidates_snapshot()
    unarchived_people = dict(snapshot.people)
    unarchived_people[PARENT] = replace(unarchived_people[PARENT], archived=False)
    unarchived = GraphSnapshot(
        snapshot.state,
        unarchived_people,
        snapshot.family_units,
        snapshot.links,
        snapshot.unresolved,
    )

    candidate_projection = project_graph(unarchived)
    assert {
        (
            reference.label,
            reference.source_person_id,
            reference.target_person_id,
            reference.family_unit_id,
        )
        for reference in candidate_projection.references
    } == {
        (
            "cross_family",
            PersonId("per_retained_relative"),
            PersonId("per_retained_adult"),
            FAMILY,
        ),
        (
            "non_primary",
            PersonId("per_retained_relative"),
            PARENT,
            None,
        ),
    }

    projection = project_graph(snapshot)

    assert projection.references == ()
    assert LinkId("lnk_archived_cross_family") not in _ids(
        projection.parent_child_links, "link_id"
    )
    assert LinkId("lnk_archived_guardian") not in _ids(
        projection.parent_child_links, "link_id"
    )
    _assert_all_public_ids_resolve(projection)


def test_projection_is_pure_for_a_non_archived_snapshot():
    snapshot = deterministic_projection_snapshot()
    before = (
        dict(snapshot.people),
        dict(snapshot.family_units),
        dict(snapshot.links),
        dict(snapshot.unresolved),
    )

    project_graph(snapshot)

    assert before == (
        dict(snapshot.people),
        dict(snapshot.family_units),
        dict(snapshot.links),
        dict(snapshot.unresolved),
    )
