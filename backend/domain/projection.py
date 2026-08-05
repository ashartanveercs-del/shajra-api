import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

from domain.checksum import semantic_checksum
from domain.dates import PartialDate
from domain.ids import FamilyUnitId, LinkId, PersonId
from domain.issues import (
    ARCHIVED_RELATIONSHIP_OMITTED,
    DUPLICATE_UNRESOLVED_RELATIONSHIP,
    SUSPICIOUS_PARENT_AGE,
    GraphIssue,
    IssueSeverity,
    ValidationReport,
)
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    Gender,
    GraphSnapshot,
    ParentChildLink,
    ParentRole,
    RelationshipType,
    UnionStatus,
)
from domain.validation import validate_snapshot


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


ReferenceLabel = Literal["repeated_ancestor", "cross_family", "non_primary"]


@dataclass(frozen=True, slots=True)
class RelationshipReference:
    reference_id: str
    source_person_id: PersonId
    target_person_id: PersonId
    family_unit_id: FamilyUnitId | None
    relationship_type: RelationshipType
    label: ReferenceLabel


@dataclass(frozen=True, slots=True)
class GraphComponent:
    component_id: str
    root_person_ids: tuple[PersonId, ...]
    person_ids: tuple[PersonId, ...]
    family_unit_ids: tuple[FamilyUnitId, ...]
    link_ids: tuple[LinkId, ...]


PublicIssueCode = Literal[
    "DUPLICATE_UNRESOLVED_RELATIONSHIP",
    "SUSPICIOUS_PARENT_AGE",
    "ARCHIVED_RELATIONSHIP_OMITTED",
    "GRAPH_WARNING",
]

PUBLIC_ISSUE_MESSAGES: Mapping[PublicIssueCode, str] = MappingProxyType(
    {
        "DUPLICATE_UNRESOLVED_RELATIONSHIP": "Some relationships need review.",
        "SUSPICIOUS_PARENT_AGE": "Some dates may need review.",
        "ARCHIVED_RELATIONSHIP_OMITTED": (
            "Some relationships are hidden because an archived person is involved."
        ),
        "GRAPH_WARNING": "Some family-tree details need review.",
    }
)


@dataclass(frozen=True, slots=True)
class PublicGraphIssue:
    code: PublicIssueCode
    severity: IssueSeverity
    message: str
    person_ids: tuple[PersonId, ...] = ()
    family_unit_ids: tuple[FamilyUnitId, ...] = ()
    link_ids: tuple[LinkId, ...] = ()


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
    issues: tuple[PublicGraphIssue, ...]
    unresolved_count: int


class InvalidGraphProjection(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__("graph snapshot has blocking validation issues")
        self.report = report


_ANCESTRY_TYPES = frozenset(
    {
        RelationshipType.BIOLOGICAL,
        RelationshipType.ADOPTIVE,
        RelationshipType.STEP,
        RelationshipType.UNKNOWN,
    }
)
_PUBLIC_CODES = frozenset(
    {
        DUPLICATE_UNRESOLVED_RELATIONSHIP,
        SUSPICIOUS_PARENT_AGE,
        ARCHIVED_RELATIONSHIP_OMITTED,
    }
)


def project_graph(snapshot: GraphSnapshot) -> TreeProjection:
    report = validate_snapshot(snapshot)
    if report.has_errors:
        raise InvalidGraphProjection(report)

    visible_people = {
        person_id: person
        for person_id, person in sorted(snapshot.people.items())
        if not person.archived
    }
    retained_families = {
        family_id: family
        for family_id, family in sorted(snapshot.family_units.items())
        if family.adult_a_id in visible_people
        and (
            family.adult_b_id is None or family.adult_b_id in visible_people
        )
    }
    retained_links = {
        link_id: link
        for link_id, link in sorted(snapshot.links.items())
        if link.parent_id in visible_people
        and link.child_id in visible_people
        and (
            link.family_unit_id is None
            or link.family_unit_id in retained_families
        )
    }
    topology_omitted = (
        len(retained_families) != len(snapshot.family_units)
        or len(retained_links) != len(snapshot.links)
    )

    people = tuple(
        ProjectedPerson(
            person.person_id,
            person.full_name,
            person.gender,
            person.birth,
            person.death,
            person.is_alive,
        )
        for person in visible_people.values()
    )
    family_units = tuple(
        _project_family(family) for family in retained_families.values()
    )
    parent_child_links = tuple(
        _project_link(link, snapshot)
        for link in retained_links.values()
    )
    adult_memberships = _adult_memberships(family_units)
    descendant_edges = _descendant_edges(parent_child_links)
    references = _relationship_references(parent_child_links)
    components = _components(
        people,
        family_units,
        parent_child_links,
        adult_memberships,
        descendant_edges,
    )

    issues = _public_issues(
        report,
        topology_omitted,
        {person.person_id for person in people},
        {family.family_unit_id for family in family_units},
        {link.link_id for link in parent_child_links},
    )
    unresolved_count = len(snapshot.unresolved)
    if issues or unresolved_count or topology_omitted:
        status: Literal["ready", "empty", "partial", "unavailable"] = "partial"
    elif not people:
        status = "empty"
    else:
        status = "ready"

    return TreeProjection(
        "2",
        snapshot.state.revision,
        semantic_checksum(snapshot),
        status,
        people,
        family_units,
        parent_child_links,
        adult_memberships,
        descendant_edges,
        references,
        components,
        issues,
        unresolved_count,
    )


def _project_family(family: FamilyUnit) -> ProjectedFamilyUnit:
    adults = [family.adult_a_id]
    if family.adult_b_id is not None:
        adults.append(family.adult_b_id)
    adults.sort()
    return ProjectedFamilyUnit(
        family.family_unit_id,
        family.kind,
        adults[0],
        adults[1] if len(adults) == 2 else None,
        family.status,
        family.start,
        family.end,
    )


def _project_link(link: ParentChildLink, snapshot: GraphSnapshot) -> ProjectedLink:
    primary_family_id = snapshot.people[link.child_id].primary_family_unit_id
    primary = link.family_unit_id is not None and link.family_unit_id == primary_family_id
    return ProjectedLink(
        link.link_id,
        link.parent_id,
        link.child_id,
        link.role,
        link.relationship_type,
        link.family_unit_id,
        primary,
    )


def _adult_memberships(
    families: tuple[ProjectedFamilyUnit, ...],
) -> tuple[AdultMembershipEdge, ...]:
    edges: list[AdultMembershipEdge] = []
    for family in families:
        edges.append(
            AdultMembershipEdge(
                f"adult:{family.family_unit_id}:{family.adult_a_id}",
                family.family_unit_id,
                family.adult_a_id,
                "adult_a",
            )
        )
        if family.adult_b_id is not None:
            edges.append(
                AdultMembershipEdge(
                    f"adult:{family.family_unit_id}:{family.adult_b_id}",
                    family.family_unit_id,
                    family.adult_b_id,
                    "adult_b",
                )
            )
    return tuple(sorted(edges, key=lambda edge: edge.edge_id))


def _descendant_edges(
    links: tuple[ProjectedLink, ...],
) -> tuple[DescendantEdge, ...]:
    placements = {
        (link.family_unit_id, link.child_id)
        for link in links
        if link.primary
        and link.family_unit_id is not None
        and link.relationship_type in _ANCESTRY_TYPES
    }
    return tuple(
        DescendantEdge(
            f"child:{family_id}:{child_id}",
            family_id,
            child_id,
        )
        for family_id, child_id in sorted(placements)
    )


def _relationship_references(
    links: tuple[ProjectedLink, ...],
) -> tuple[RelationshipReference, ...]:
    candidates = tuple(
        link
        for link in links
        if not link.primary and link.relationship_type in _ANCESTRY_TYPES
    )
    adjacency: dict[PersonId, list[ProjectedLink]] = defaultdict(list)
    incoming: set[PersonId] = set()
    for link in candidates:
        adjacency[link.parent_id].append(link)
        incoming.add(link.child_id)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda link: link.link_id)

    labels: dict[LinkId, ReferenceLabel] = {}
    expanded: set[PersonId] = set()
    starts = sorted(
        {link.parent_id for link in candidates} | {link.child_id for link in candidates},
        key=lambda person_id: (person_id in incoming, person_id),
    )
    for start_id in starts:
        if start_id in expanded:
            continue
        expanded.add(start_id)
        frames: list[tuple[PersonId, int]] = [(start_id, 0)]
        while frames:
            person_id, link_index = frames[-1]
            outgoing = adjacency.get(person_id, [])
            if link_index == len(outgoing):
                frames.pop()
                continue
            link = outgoing[link_index]
            frames[-1] = (person_id, link_index + 1)
            if link.family_unit_id is not None:
                labels[link.link_id] = "cross_family"
            elif link.child_id in expanded:
                labels[link.link_id] = "repeated_ancestor"
            else:
                labels[link.link_id] = "non_primary"
            if link.child_id not in expanded:
                expanded.add(link.child_id)
                frames.append((link.child_id, 0))

    references = [
        RelationshipReference(
            _reference_id(link, labels[link.link_id]),
            link.parent_id,
            link.child_id,
            link.family_unit_id,
            link.relationship_type,
            labels[link.link_id],
        )
        for link in candidates
    ]
    return tuple(sorted(references, key=lambda reference: reference.reference_id))


def _reference_id(link: ProjectedLink, label: ReferenceLabel) -> str:
    value = [
        link.parent_id,
        link.child_id,
        link.family_unit_id or "",
        link.relationship_type,
        label,
    ]
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ref_" + hashlib.sha256(encoded).hexdigest()


def _components(
    people: tuple[ProjectedPerson, ...],
    families: tuple[ProjectedFamilyUnit, ...],
    links: tuple[ProjectedLink, ...],
    memberships: tuple[AdultMembershipEdge, ...],
    descendants: tuple[DescendantEdge, ...],
) -> tuple[GraphComponent, ...]:
    parents = {person.person_id: person.person_id for person in people}

    def find(person_id: PersonId) -> PersonId:
        while parents[person_id] != person_id:
            parents[person_id] = parents[parents[person_id]]
            person_id = parents[person_id]
        return person_id

    def union(left: PersonId, right: PersonId) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        parents[high] = low

    family_adults: dict[FamilyUnitId, tuple[PersonId, ...]] = defaultdict(tuple)
    for membership in memberships:
        family_adults[membership.family_unit_id] += (membership.adult_id,)
    for adult_ids in family_adults.values():
        for adult_id in adult_ids[1:]:
            union(adult_ids[0], adult_id)
    for descendant in descendants:
        union(family_adults[descendant.family_unit_id][0], descendant.child_id)

    people_by_root: dict[PersonId, list[PersonId]] = defaultdict(list)
    for person_id in sorted(parents):
        people_by_root[find(person_id)].append(person_id)
    family_root = {
        family.family_unit_id: find(family.adult_a_id) for family in families
    }
    placed_children = {edge.child_id for edge in descendants}

    components: list[GraphComponent] = []
    for root_id, component_people in people_by_root.items():
        person_ids = tuple(sorted(component_people))
        person_id_set = set(person_ids)
        family_ids = tuple(
            sorted(
                family_id
                for family_id, component_root in family_root.items()
                if component_root == root_id
            )
        )
        family_id_set = set(family_ids)
        component_link_ids = tuple(
            sorted(
                link.link_id
                for link in links
                if link.parent_id in person_id_set
                and link.child_id in person_id_set
                and (
                    link.family_unit_id is None
                    or link.family_unit_id in family_id_set
                )
            )
        )
        roots = tuple(
            person_id for person_id in person_ids if person_id not in placed_children
        )
        component_hash = hashlib.sha256(
            "|".join(person_ids).encode("utf-8")
        ).hexdigest()
        components.append(
            GraphComponent(
                "cmp_" + component_hash,
                roots,
                person_ids,
                family_ids,
                component_link_ids,
            )
        )
    return tuple(sorted(components, key=lambda component: component.component_id))


def _public_issues(
    report: ValidationReport,
    topology_omitted: bool,
    person_ids: set[PersonId],
    family_ids: set[FamilyUnitId],
    link_ids: set[LinkId],
) -> tuple[PublicGraphIssue, ...]:
    internal_issues = list(report.issues)
    if topology_omitted and not any(
        issue.code == ARCHIVED_RELATIONSHIP_OMITTED for issue in internal_issues
    ):
        internal_issues.append(
            GraphIssue(
                ARCHIVED_RELATIONSHIP_OMITTED,
                IssueSeverity.WARNING,
                "",
            )
        )

    public_issues: list[PublicGraphIssue] = []
    for issue in internal_issues:
        code = cast(
            PublicIssueCode,
            issue.code if issue.code in _PUBLIC_CODES else "GRAPH_WARNING",
        )
        public_issues.append(
            PublicGraphIssue(
                code,
                issue.severity,
                PUBLIC_ISSUE_MESSAGES[code],
                tuple(sorted(value for value in issue.person_ids if value in person_ids)),
                tuple(
                    sorted(value for value in issue.family_unit_ids if value in family_ids)
                ),
                tuple(sorted(value for value in issue.link_ids if value in link_ids)),
            )
        )
    public_issues.sort(
        key=lambda issue: (
            issue.severity.value,
            issue.code,
            issue.person_ids,
            issue.family_unit_ids,
            issue.link_ids,
        )
    )
    return tuple(public_issues)
