from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.issues import (
    ANCESTRY_CYCLE,
    ARCHIVED_RELATIONSHIP_OMITTED,
    DEATH_BEFORE_BIRTH,
    DUPLICATE_FAMILY_UNIT,
    DUPLICATE_LINK,
    DUPLICATE_UNRESOLVED_RELATIONSHIP,
    FAMILY_UNIT_PARENT_MISMATCH,
    IMPOSSIBLE_PARENT_AGE,
    MISSING_FAMILY_UNIT,
    MISSING_PERSON,
    PRIMARY_UNIT_MISMATCH,
    SELF_PARENT,
    SELF_PARTNER,
    SUSPICIOUS_PARENT_AGE,
    UNION_END_BEFORE_START,
    GraphIssue,
    IssueSeverity,
    ValidationReport,
)
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    GraphSnapshot,
    ParentChildLink,
    RelationshipType,
    UnresolvedRelationshipKind,
)

_ANCESTRY_TYPES = frozenset(
    {
        RelationshipType.BIOLOGICAL,
        RelationshipType.ADOPTIVE,
        RelationshipType.STEP,
        RelationshipType.UNKNOWN,
    }
)


def _issue(
    code: str,
    severity: IssueSeverity,
    message: str,
    *,
    person_ids: Iterable[PersonId] = (),
    family_unit_ids: Iterable[FamilyUnitId] = (),
    link_ids: Iterable[LinkId] = (),
) -> GraphIssue:
    return GraphIssue(
        code,
        severity,
        message,
        tuple(sorted(set(person_ids))),
        tuple(sorted(set(family_unit_ids))),
        tuple(sorted(set(link_ids))),
    )


def _family_shape_is_valid(family: FamilyUnit) -> bool:
    if family.kind is FamilyUnitKind.SINGLE_PARENT:
        return family.adult_b_id is None
    return family.adult_b_id is not None


def _family_adults(family: FamilyUnit) -> tuple[PersonId, ...]:
    if family.adult_b_id is None:
        return (family.adult_a_id,)
    return (family.adult_a_id, family.adult_b_id)


def _family_key(family: FamilyUnit) -> tuple[PersonId, PersonId | None]:
    if family.kind is FamilyUnitKind.SINGLE_PARENT or family.adult_b_id is None:
        return (family.adult_a_id, None)
    adult_a, adult_b = sorted((family.adult_a_id, family.adult_b_id))
    return (adult_a, adult_b)


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _cycle_issues(
    snapshot: GraphSnapshot, valid_links: Iterable[ParentChildLink]
) -> list[GraphIssue]:
    ancestry_links = sorted(
        (
            link
            for link in valid_links
            if link.relationship_type in _ANCESTRY_TYPES
            and link.parent_id != link.child_id
        ),
        key=lambda link: link.link_id,
    )
    adjacency: dict[PersonId, list[PersonId]] = defaultdict(list)
    reverse_adjacency: dict[PersonId, list[PersonId]] = defaultdict(list)
    for link in ancestry_links:
        adjacency[link.parent_id].append(link.child_id)
        reverse_adjacency[link.child_id].append(link.parent_id)
    for neighbors in (*adjacency.values(), *reverse_adjacency.values()):
        neighbors.sort()

    visited: set[PersonId] = set()
    finish_order: list[PersonId] = []
    for person_id in sorted(snapshot.people):
        if person_id in visited:
            continue
        visited.add(person_id)
        traversal_frames: list[tuple[PersonId, int]] = [(person_id, 0)]
        while traversal_frames:
            current_id, child_index = traversal_frames[-1]
            children = adjacency.get(current_id, [])
            if child_index == len(children):
                finish_order.append(current_id)
                traversal_frames.pop()
                continue
            child_id = children[child_index]
            traversal_frames[-1] = (current_id, child_index + 1)
            if child_id not in visited:
                visited.add(child_id)
                traversal_frames.append((child_id, 0))

    assigned: set[PersonId] = set()
    components: list[tuple[PersonId, ...]] = []
    for person_id in reversed(finish_order):
        if person_id in assigned:
            continue
        assigned.add(person_id)
        component_members: list[PersonId] = []
        reverse_traversal = [person_id]
        while reverse_traversal:
            current_id = reverse_traversal.pop()
            component_members.append(current_id)
            for parent_id in reversed(reverse_adjacency.get(current_id, [])):
                if parent_id not in assigned:
                    assigned.add(parent_id)
                    reverse_traversal.append(parent_id)
        if len(component_members) > 1:
            components.append(tuple(sorted(component_members)))

    issues: list[GraphIssue] = []
    for cyclic_component in sorted(components):
        members = set(cyclic_component)
        cycle_link_ids = (
            link.link_id
            for link in ancestry_links
            if link.parent_id in members and link.child_id in members
        )
        issues.append(
            _issue(
                ANCESTRY_CYCLE,
                IssueSeverity.ERROR,
                "An ancestry cycle connects the affected people.",
                person_ids=cyclic_component,
                link_ids=cycle_link_ids,
            )
        )
    return issues


def validate_snapshot(snapshot: GraphSnapshot) -> ValidationReport:
    issues: list[GraphIssue] = []
    invalid_families: set[FamilyUnitId] = set()
    invalid_links: set[LinkId] = set()
    invalid_unresolved: set[UnresolvedRelationshipId] = set()
    missing_primary_people: set[PersonId] = set()

    # Pass 1: required references and self-links.
    for family_id, family in sorted(snapshot.family_units.items()):
        for adult_id in _family_adults(family):
            if adult_id not in snapshot.people:
                invalid_families.add(family_id)
                issues.append(
                    _issue(
                        MISSING_PERSON,
                        IssueSeverity.ERROR,
                        "A family unit references a missing adult.",
                        person_ids=(adult_id,),
                        family_unit_ids=(family_id,),
                    )
                )
        if family.adult_b_id == family.adult_a_id:
            invalid_families.add(family_id)
            issues.append(
                _issue(
                    SELF_PARTNER,
                    IssueSeverity.ERROR,
                    "A family unit lists the same person as both adults.",
                    person_ids=(family.adult_a_id,),
                    family_unit_ids=(family_id,),
                )
            )
        if not _family_shape_is_valid(family):
            invalid_families.add(family_id)

    for link_id, link in sorted(snapshot.links.items()):
        if link.parent_id not in snapshot.people:
            invalid_links.add(link_id)
            issues.append(
                _issue(
                    MISSING_PERSON,
                    IssueSeverity.ERROR,
                    "A parent-child link references a missing parent.",
                    person_ids=(link.parent_id,),
                    link_ids=(link_id,),
                )
            )
        if link.child_id not in snapshot.people:
            invalid_links.add(link_id)
            issues.append(
                _issue(
                    MISSING_PERSON,
                    IssueSeverity.ERROR,
                    "A parent-child link references a missing child.",
                    person_ids=(link.child_id,),
                    link_ids=(link_id,),
                )
            )
        if link.family_unit_id is not None and link.family_unit_id not in snapshot.family_units:
            invalid_links.add(link_id)
            issues.append(
                _issue(
                    MISSING_FAMILY_UNIT,
                    IssueSeverity.ERROR,
                    "A parent-child link references a missing family unit.",
                    family_unit_ids=(link.family_unit_id,),
                    link_ids=(link_id,),
                )
            )
        if link.parent_id == link.child_id:
            invalid_links.add(link_id)
            issues.append(
                _issue(
                    SELF_PARENT,
                    IssueSeverity.ERROR,
                    "A person cannot be their own parent.",
                    person_ids=(link.parent_id,),
                    link_ids=(link_id,),
                )
            )

    for unresolved_id, annotation in sorted(snapshot.unresolved.items()):
        if annotation.subject_person_id not in snapshot.people:
            invalid_unresolved.add(unresolved_id)
            issues.append(
                _issue(
                    MISSING_PERSON,
                    IssueSeverity.ERROR,
                    "An unresolved relationship references a missing subject.",
                    person_ids=(annotation.subject_person_id,),
                )
            )

    for person_id, person in sorted(snapshot.people.items()):
        primary_family_id = person.primary_family_unit_id
        if (
            primary_family_id is not None
            and primary_family_id not in snapshot.family_units
        ):
            missing_primary_people.add(person_id)
            issues.append(
                _issue(
                    MISSING_FAMILY_UNIT,
                    IssueSeverity.ERROR,
                    "A person references a missing primary family unit.",
                    person_ids=(person_id,),
                    family_unit_ids=(primary_family_id,),
                )
            )

    # Pass 2: normalized duplicate keys.
    links_by_key: dict[
        tuple[PersonId, PersonId, RelationshipType], list[ParentChildLink]
    ] = defaultdict(list)
    for link_id, link in sorted(snapshot.links.items()):
        if link_id not in invalid_links:
            links_by_key[(link.parent_id, link.child_id, link.relationship_type)].append(
                link
            )
    for duplicate_links in links_by_key.values():
        if len(duplicate_links) > 1:
            issues.append(
                _issue(
                    DUPLICATE_LINK,
                    IssueSeverity.ERROR,
                    "Multiple links have the same parent, child, and relationship type.",
                    person_ids=(
                        duplicate_links[0].parent_id,
                        duplicate_links[0].child_id,
                    ),
                    link_ids=(link.link_id for link in duplicate_links),
                )
            )

    families_by_key: dict[
        tuple[PersonId, PersonId | None], list[FamilyUnit]
    ] = defaultdict(list)
    for family_id, family in sorted(snapshot.family_units.items()):
        if family_id not in invalid_families:
            families_by_key[_family_key(family)].append(family)
    for duplicate_families in families_by_key.values():
        if len(duplicate_families) > 1 and not all(
            family.distinct_union_confirmed for family in duplicate_families
        ):
            issues.append(
                _issue(
                    DUPLICATE_FAMILY_UNIT,
                    IssueSeverity.ERROR,
                    "Repeated adult pairs require confirmation as distinct unions.",
                    person_ids=_family_adults(duplicate_families[0]),
                    family_unit_ids=(
                        family.family_unit_id for family in duplicate_families
                    ),
                )
            )

    unresolved_by_key: dict[
        tuple[PersonId, UnresolvedRelationshipKind, str],
        list[UnresolvedRelationshipId],
    ] = defaultdict(list)
    for unresolved_id, annotation in sorted(snapshot.unresolved.items()):
        if unresolved_id not in invalid_unresolved:
            unresolved_key = (
                annotation.subject_person_id,
                annotation.kind,
                annotation.unresolved_name.casefold(),
            )
            unresolved_by_key[unresolved_key].append(unresolved_id)
    for duplicate_key, unresolved_ids in unresolved_by_key.items():
        if len(unresolved_ids) > 1:
            issues.append(
                _issue(
                    DUPLICATE_UNRESOLVED_RELATIONSHIP,
                    IssueSeverity.WARNING,
                    "Equivalent unresolved relationships are recorded more than once.",
                    person_ids=(duplicate_key[0],),
                )
            )

    # Pass 3: family-unit shape and linked-parent membership.
    for family_id, family in sorted(snapshot.family_units.items()):
        if not _family_shape_is_valid(family):
            issues.append(
                _issue(
                    FAMILY_UNIT_PARENT_MISMATCH,
                    IssueSeverity.ERROR,
                    "The family-unit kind does not match its adult membership.",
                    person_ids=_family_adults(family),
                    family_unit_ids=(family_id,),
                )
            )

    for link_id, link in sorted(snapshot.links.items()):
        linked_family_id = link.family_unit_id
        if (
            link_id in invalid_links
            or linked_family_id is None
            or linked_family_id in invalid_families
        ):
            continue
        family = snapshot.family_units[linked_family_id]
        if link.parent_id not in _family_adults(family):
            issues.append(
                _issue(
                    FAMILY_UNIT_PARENT_MISMATCH,
                    IssueSeverity.ERROR,
                    "A family-linked parent is not an adult in that family unit.",
                    person_ids=(link.parent_id,),
                    family_unit_ids=(linked_family_id,),
                    link_ids=(link_id,),
                )
            )

    # Pass 4: exactly-zero-or-one primary placement.
    for person_id, person in sorted(snapshot.people.items()):
        primary_family_id = person.primary_family_unit_id
        if (
            primary_family_id is None
            or person_id in missing_primary_people
            or primary_family_id in invalid_families
        ):
            continue
        family = snapshot.family_units[primary_family_id]
        expected_parents = set(_family_adults(family))
        primary_links = [
            link
            for link_id, link in sorted(snapshot.links.items())
            if link_id not in invalid_links
            and link.child_id == person_id
            and link.family_unit_id == primary_family_id
        ]
        actual_parents = {link.parent_id for link in primary_links}
        if actual_parents != expected_parents or len(primary_links) != len(
            expected_parents
        ):
            issues.append(
                _issue(
                    PRIMARY_UNIT_MISMATCH,
                    IssueSeverity.ERROR,
                    "Primary-family links do not exactly match the listed adults.",
                    person_ids=(person_id, *expected_parents, *actual_parents),
                    family_unit_ids=(primary_family_id,),
                    link_ids=(link.link_id for link in primary_links),
                )
            )

    # Pass 5: ancestry cycles.
    valid_links = [
        link
        for link_id, link in sorted(snapshot.links.items())
        if link_id not in invalid_links
    ]
    issues.extend(_cycle_issues(snapshot, valid_links))

    # Pass 6: person, family-unit, and biological parent-child chronology.
    for person_id, person in sorted(snapshot.people.items()):
        if (
            person.birth is not None
            and person.death is not None
            and person.death.latest < person.birth.earliest
        ):
            issues.append(
                _issue(
                    DEATH_BEFORE_BIRTH,
                    IssueSeverity.ERROR,
                    "A person's death interval is wholly before their birth interval.",
                    person_ids=(person_id,),
                )
            )

    for family_id, family in sorted(snapshot.family_units.items()):
        if (
            family_id not in invalid_families
            and family.start is not None
            and family.end is not None
            and family.end.latest < family.start.earliest
        ):
            issues.append(
                _issue(
                    UNION_END_BEFORE_START,
                    IssueSeverity.ERROR,
                    "A union's end interval is wholly before its start interval.",
                    family_unit_ids=(family_id,),
                )
            )

    for link in valid_links:
        if link.relationship_type is not RelationshipType.BIOLOGICAL:
            continue
        parent = snapshot.people[link.parent_id]
        child = snapshot.people[link.child_id]
        impossible = False
        if parent.birth is not None and child.birth is not None:
            impossible = parent.birth.earliest > child.birth.latest
        if parent.death is not None and child.birth is not None:
            earliest_conception = child.birth.earliest - timedelta(days=310)
            impossible = impossible or parent.death.latest < earliest_conception
        if impossible:
            issues.append(
                _issue(
                    IMPOSSIBLE_PARENT_AGE,
                    IssueSeverity.ERROR,
                    "Biological parent chronology is impossible for the child.",
                    person_ids=(link.parent_id, link.child_id),
                    link_ids=(link.link_id,),
                )
            )
        if parent.birth is not None and child.birth is not None:
            under_ten_possible = child.birth.earliest < _add_years(
                parent.birth.latest, 10
            )
            over_eighty_possible = child.birth.latest > _add_years(
                parent.birth.earliest, 80
            )
            if under_ten_possible or over_eighty_possible:
                issues.append(
                    _issue(
                        SUSPICIOUS_PARENT_AGE,
                        IssueSeverity.WARNING,
                        "A possible biological parent age is under 10 or over 80.",
                        person_ids=(link.parent_id, link.child_id),
                        link_ids=(link.link_id,),
                    )
                )

    archived_people: set[PersonId] = set()
    archived_families: set[FamilyUnitId] = set()
    archived_links: set[LinkId] = set()
    for person_id, person in sorted(snapshot.people.items()):
        if not person.archived:
            continue
        incident_families = {
            family_id
            for family_id, family in snapshot.family_units.items()
            if family_id not in invalid_families
            and person_id in _family_adults(family)
        }
        incident_links = {
            link_id
            for link_id, link in snapshot.links.items()
            if link_id not in invalid_links
            and person_id in (link.parent_id, link.child_id)
        }
        if incident_families or incident_links:
            archived_people.add(person_id)
            archived_families.update(incident_families)
            archived_links.update(incident_links)
    if archived_people:
        issues.append(
            _issue(
                ARCHIVED_RELATIONSHIP_OMITTED,
                IssueSeverity.WARNING,
                "Current relationship topology includes an archived person.",
                person_ids=archived_people,
                family_unit_ids=archived_families,
                link_ids=archived_links,
            )
        )

    issues.sort(
        key=lambda issue: (
            issue.severity.value,
            issue.code,
            issue.person_ids,
            issue.family_unit_ids,
            issue.link_ids,
        )
    )
    return ValidationReport(tuple(issues))
