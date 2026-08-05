from domain.dates import PartialDate
from domain.ids import (
    FamilyUnitId,
    LinkId,
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
    UnionStatus,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)

PARENT = PersonId("per_parent")
CHILD = PersonId("per_child")
FAMILY = FamilyUnitId("fam_primary")


def _state(revision: int = 0) -> GraphState:
    return GraphState(revision, None, 0, "")


def _link(
    link_id: str,
    parent_id: PersonId,
    child_id: PersonId,
    family_id: FamilyUnitId | None,
) -> ParentChildLink:
    return ParentChildLink(
        LinkId(link_id),
        parent_id,
        child_id,
        ParentRole.PARENT,
        RelationshipType.BIOLOGICAL,
        family_id,
    )


def empty_snapshot(revision: int = 0) -> GraphSnapshot:
    return GraphSnapshot(_state(revision), {}, {}, {}, {})


def simple_parent_child_snapshot(
    include_link: bool = True,
) -> tuple[GraphSnapshot, PersonId, PersonId, LinkId]:
    link_id = LinkId("lnk_parent_child")
    links = (
        {
            _link(str(link_id), PARENT, CHILD, None).link_id: _link(
                str(link_id), PARENT, CHILD, None
            )
        }
        if include_link
        else {}
    )
    return (
        GraphSnapshot(
            _state(),
            {PARENT: Person(PARENT, "Parent"), CHILD: Person(CHILD, "Child")},
            {},
            links,
            {},
        ),
        PARENT,
        CHILD,
        link_id,
    )


def two_parent_family_snapshot() -> GraphSnapshot:
    adult_b = PersonId("per_parent_b")
    family = FamilyUnit(FAMILY, FamilyUnitKind.UNION, PARENT, adult_b)
    return GraphSnapshot(
        _state(),
        {
            PARENT: Person(PARENT, "Parent A"),
            adult_b: Person(adult_b, "Parent B"),
            CHILD: Person(CHILD, "Child", primary_family_unit_id=FAMILY),
        },
        {FAMILY: family},
        {
            LinkId("lnk_parent_a_child"): _link(
                "lnk_parent_a_child", PARENT, CHILD, FAMILY
            ),
            LinkId("lnk_parent_b_child"): _link(
                "lnk_parent_b_child", adult_b, CHILD, FAMILY
            ),
        },
        {},
    )


def remarriage_snapshot() -> GraphSnapshot:
    former = PersonId("per_former_partner")
    current = PersonId("per_current_partner")
    first_child = PersonId("per_first_child")
    second_child = PersonId("per_second_child")
    first_family = FamilyUnitId("fam_first_union")
    second_family = FamilyUnitId("fam_second_union")
    return GraphSnapshot(
        _state(),
        {
            PARENT: Person(PARENT, "Remarried Adult"),
            former: Person(former, "Former Partner"),
            current: Person(current, "Current Partner"),
            first_child: Person(
                first_child, "First Child", primary_family_unit_id=first_family
            ),
            second_child: Person(
                second_child, "Second Child", primary_family_unit_id=second_family
            ),
        },
        {
            first_family: FamilyUnit(
                first_family, FamilyUnitKind.UNION, PARENT, former
            ),
            second_family: FamilyUnit(
                second_family, FamilyUnitKind.UNION, PARENT, current
            ),
        },
        {
            LinkId("lnk_remarriage_first"): _link(
                "lnk_remarriage_first", PARENT, first_child, first_family
            ),
            LinkId("lnk_former_first"): _link(
                "lnk_former_first", former, first_child, first_family
            ),
            LinkId("lnk_remarriage_second"): _link(
                "lnk_remarriage_second", PARENT, second_child, second_family
            ),
            LinkId("lnk_current_second"): _link(
                "lnk_current_second", current, second_child, second_family
            ),
        },
        {},
    )


def adoptive_cycle_snapshot() -> GraphSnapshot:
    first = PersonId("per_adoptive_cycle_a")
    second = PersonId("per_adoptive_cycle_b")
    return GraphSnapshot(
        _state(),
        {
            first: Person(first, "Adoptive Cycle A"),
            second: Person(second, "Adoptive Cycle B"),
        },
        {},
        {
            LinkId("lnk_adoptive_a_b"): ParentChildLink(
                LinkId("lnk_adoptive_a_b"),
                first,
                second,
                ParentRole.PARENT,
                RelationshipType.ADOPTIVE,
                None,
            ),
            LinkId("lnk_adoptive_b_a"): ParentChildLink(
                LinkId("lnk_adoptive_b_a"),
                second,
                first,
                ParentRole.PARENT,
                RelationshipType.ADOPTIVE,
                None,
            ),
        },
        {},
    )


def guardian_cycle_snapshot() -> GraphSnapshot:
    first = PersonId("per_guardian_cycle_a")
    second = PersonId("per_guardian_cycle_b")
    return GraphSnapshot(
        _state(),
        {
            first: Person(first, "Guardian Cycle A"),
            second: Person(second, "Guardian Cycle B"),
        },
        {},
        {
            LinkId("lnk_guardian_a_b"): ParentChildLink(
                LinkId("lnk_guardian_a_b"),
                first,
                second,
                ParentRole.PARENT,
                RelationshipType.GUARDIAN,
                None,
            ),
            LinkId("lnk_guardian_b_a"): ParentChildLink(
                LinkId("lnk_guardian_b_a"),
                second,
                first,
                ParentRole.PARENT,
                RelationshipType.GUARDIAN,
                None,
            ),
        },
        {},
    )


def cousin_union_snapshot() -> GraphSnapshot:
    grandparent = PersonId("per_common_grandparent")
    sibling_a = PersonId("per_sibling_a")
    sibling_b = PersonId("per_sibling_b")
    cousin_a = PersonId("per_cousin_a")
    cousin_b = PersonId("per_cousin_b")
    union = FamilyUnitId("fam_cousin_union")
    return GraphSnapshot(
        _state(),
        {
            person_id: Person(person_id, str(person_id))
            for person_id in (grandparent, sibling_a, sibling_b, cousin_a, cousin_b)
        },
        {union: FamilyUnit(union, FamilyUnitKind.UNION, cousin_a, cousin_b)},
        {
            LinkId("lnk_grandparent_a"): _link(
                "lnk_grandparent_a", grandparent, sibling_a, None
            ),
            LinkId("lnk_grandparent_b"): _link(
                "lnk_grandparent_b", grandparent, sibling_b, None
            ),
            LinkId("lnk_sibling_a"): _link("lnk_sibling_a", sibling_a, cousin_a, None),
            LinkId("lnk_sibling_b"): _link("lnk_sibling_b", sibling_b, cousin_b, None),
        },
        {},
    )


def repeated_ancestor_snapshot() -> GraphSnapshot:
    ancestor = PersonId("per_ancestor")
    parent_a = PersonId("per_path_a")
    parent_b = PersonId("per_path_b")
    descendant = PersonId("per_descendant")
    return GraphSnapshot(
        _state(),
        {
            person_id: Person(person_id, str(person_id))
            for person_id in (ancestor, parent_a, parent_b, descendant)
        },
        {},
        {
            LinkId("lnk_ancestor_a"): _link("lnk_ancestor_a", ancestor, parent_a, None),
            LinkId("lnk_ancestor_b"): _link("lnk_ancestor_b", ancestor, parent_b, None),
            LinkId("lnk_path_a"): _link("lnk_path_a", parent_a, descendant, None),
            LinkId("lnk_path_b"): _link("lnk_path_b", parent_b, descendant, None),
        },
        {},
    )


def duplicate_historical_union_snapshot(
    confirmed: bool, status: UnionStatus, ended: bool
) -> GraphSnapshot:
    other_adult = PersonId("per_historical_partner")
    end = PartialDate.parse("2000") if ended else None
    later_family = FamilyUnitId("fam_historical_later")
    first_family = FamilyUnit(
        FAMILY,
        FamilyUnitKind.UNION,
        PARENT,
        other_adult,
        status=status,
        end=end,
        distinct_union_confirmed=confirmed,
    )
    second_family = FamilyUnit(
        later_family,
        FamilyUnitKind.UNION,
        PARENT,
        other_adult,
        status=status,
        end=end,
        distinct_union_confirmed=confirmed,
    )
    return GraphSnapshot(
        _state(),
        {
            PARENT: Person(PARENT, "Adult A"),
            other_adult: Person(other_adult, "Adult B"),
        },
        {FAMILY: first_family, later_family: second_family},
        {},
        {},
    )


def archived_two_parent_snapshot() -> GraphSnapshot:
    adult_b = PersonId("per_retained_adult")
    return GraphSnapshot(
        _state(),
        {
            PARENT: Person(PARENT, "Archived Adult", archived=True),
            adult_b: Person(adult_b, "Retained Adult"),
            CHILD: Person(CHILD, "Retained Child", primary_family_unit_id=FAMILY),
        },
        {FAMILY: FamilyUnit(FAMILY, FamilyUnitKind.UNION, PARENT, adult_b)},
        {
            LinkId("lnk_archived_adult"): _link(
                "lnk_archived_adult", PARENT, CHILD, FAMILY
            ),
            LinkId("lnk_retained_adult"): _link(
                "lnk_retained_adult", adult_b, CHILD, FAMILY
            ),
        },
        {},
    )


def single_parent_family_snapshot() -> GraphSnapshot:
    adult = PersonId("per_single_parent")
    child = PersonId("per_single_child")
    family_id = FamilyUnitId("fam_single_parent")
    link = _link("lnk_single_parent_child", adult, child, family_id)
    return GraphSnapshot(
        _state(),
        {
            adult: Person(adult, "Single Parent"),
            child: Person(child, "Single Child", primary_family_unit_id=family_id),
        },
        {
            family_id: FamilyUnit(
                family_id,
                FamilyUnitKind.SINGLE_PARENT,
                adult,
            )
        },
        {link.link_id: link},
        {},
    )


def partner_only_snapshot() -> GraphSnapshot:
    adult_a = PersonId("per_partner_a")
    adult_b = PersonId("per_partner_b")
    family_id = FamilyUnitId("fam_partner_only")
    return GraphSnapshot(
        _state(),
        {
            adult_a: Person(adult_a, "Partner A"),
            adult_b: Person(adult_b, "Partner B"),
        },
        {
            family_id: FamilyUnit(
                family_id,
                FamilyUnitKind.UNION,
                adult_a,
                adult_b,
            )
        },
        {},
        {},
    )


def disconnected_components_snapshot() -> GraphSnapshot:
    adult_a = PersonId("per_disconnected_a")
    adult_b = PersonId("per_disconnected_b")
    adult_c = PersonId("per_disconnected_c")
    adult_d = PersonId("per_disconnected_d")
    family_a = FamilyUnitId("fam_disconnected_a")
    family_b = FamilyUnitId("fam_disconnected_b")
    return GraphSnapshot(
        _state(),
        {
            person_id: Person(person_id, str(person_id))
            for person_id in (adult_a, adult_b, adult_c, adult_d)
        },
        {
            family_a: FamilyUnit(
                family_a,
                FamilyUnitKind.UNION,
                adult_a,
                adult_b,
            ),
            family_b: FamilyUnit(
                family_b,
                FamilyUnitKind.UNION,
                adult_c,
                adult_d,
            ),
        },
        {},
        {},
    )


def deterministic_projection_snapshot() -> GraphSnapshot:
    primary = two_parent_family_snapshot()
    repeated = repeated_ancestor_snapshot()
    partners = partner_only_snapshot()
    unresolved_a = UnresolvedRelationship(
        UnresolvedRelationshipId("unr_projection_a"),
        CHILD,
        UnresolvedRelationshipKind.FATHER,
        "Unknown Father",
    )
    unresolved_b = UnresolvedRelationship(
        UnresolvedRelationshipId("unr_projection_b"),
        CHILD,
        UnresolvedRelationshipKind.PARTNER,
        "Unknown Partner",
    )
    return GraphSnapshot(
        _state(17),
        {**primary.people, **repeated.people, **partners.people},
        {**primary.family_units, **partners.family_units},
        {**primary.links, **repeated.links},
        {
            unresolved_a.unresolved_id: unresolved_a,
            unresolved_b.unresolved_id: unresolved_b,
        },
    )
