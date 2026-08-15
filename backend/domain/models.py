from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from domain.dates import PartialDate
from domain.ids import (
    FamilyUnitId,
    LinkId,
    OperationId,
    PersonId,
    UnresolvedRelationshipId,
)


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class FamilyUnitKind(StrEnum):
    SINGLE_PARENT = "single_parent"
    UNION = "union"


class UnionStatus(StrEnum):
    UNKNOWN = "unknown"
    MARRIED = "married"
    SEPARATED = "separated"
    DIVORCED = "divorced"
    WIDOWED = "widowed"


class ParentRole(StrEnum):
    FATHER = "father"
    MOTHER = "mother"
    PARENT = "parent"


class RelationshipType(StrEnum):
    BIOLOGICAL = "biological"
    ADOPTIVE = "adoptive"
    STEP = "step"
    GUARDIAN = "guardian"
    UNKNOWN = "unknown"


class UnresolvedRelationshipKind(StrEnum):
    FATHER = "father"
    MOTHER = "mother"
    PARENT = "parent"
    PARTNER = "partner"


@dataclass(frozen=True, slots=True)
class Person:
    person_id: PersonId
    full_name: str
    gender: Gender = Gender.UNKNOWN
    birth: PartialDate | None = None
    death: PartialDate | None = None
    is_alive: bool | None = None
    primary_family_unit_id: FamilyUnitId | None = None
    archived: bool = False
    version_revision: int = 0


@dataclass(frozen=True, slots=True)
class FamilyUnit:
    family_unit_id: FamilyUnitId
    kind: FamilyUnitKind
    adult_a_id: PersonId
    adult_b_id: PersonId | None = None
    status: UnionStatus = UnionStatus.UNKNOWN
    start: PartialDate | None = None
    end: PartialDate | None = None
    distinct_union_confirmed: bool = False
    created_revision: int = 0


@dataclass(frozen=True, slots=True)
class ParentChildLink:
    link_id: LinkId
    parent_id: PersonId
    child_id: PersonId
    role: ParentRole
    relationship_type: RelationshipType
    family_unit_id: FamilyUnitId | None
    created_revision: int = 0


@dataclass(frozen=True, slots=True)
class UnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
    subject_person_id: PersonId
    kind: UnresolvedRelationshipKind
    unresolved_name: str
    created_revision: int = 0

    def __post_init__(self) -> None:
        normalized_name = " ".join(self.unresolved_name.split())
        if not normalized_name:
            raise ValueError("unresolved_name must not be empty")
        object.__setattr__(self, "unresolved_name", normalized_name)


@dataclass(frozen=True, slots=True)
class GraphState:
    revision: int
    head_operation_id: OperationId | None
    fencing_token: int
    semantic_checksum: str


def _require_matching_key(collection: str, key: str, entity_id: str) -> None:
    if key != entity_id:
        raise ValueError(f"{collection} map key does not match embedded stable ID")


def _validate_mapping_keys(
    people: Mapping[PersonId, Person],
    family_units: Mapping[FamilyUnitId, FamilyUnit],
    links: Mapping[LinkId, ParentChildLink],
    unresolved: Mapping[UnresolvedRelationshipId, UnresolvedRelationship],
) -> None:
    for person_id, person in people.items():
        _require_matching_key("people", person_id, person.person_id)
    for family_unit_id, family_unit in family_units.items():
        _require_matching_key(
            "family_units", family_unit_id, family_unit.family_unit_id
        )
    for link_id, link in links.items():
        _require_matching_key("links", link_id, link.link_id)
    for unresolved_id, annotation in unresolved.items():
        _require_matching_key("unresolved", unresolved_id, annotation.unresolved_id)


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    state: GraphState
    people: Mapping[PersonId, Person]
    family_units: Mapping[FamilyUnitId, FamilyUnit]
    links: Mapping[LinkId, ParentChildLink]
    unresolved: Mapping[UnresolvedRelationshipId, UnresolvedRelationship]

    def __post_init__(self) -> None:
        _validate_mapping_keys(
            self.people,
            self.family_units,
            self.links,
            self.unresolved,
        )
        object.__setattr__(self, "people", MappingProxyType(dict(self.people)))
        object.__setattr__(
            self, "family_units", MappingProxyType(dict(self.family_units))
        )
        object.__setattr__(self, "links", MappingProxyType(dict(self.links)))
        object.__setattr__(self, "unresolved", MappingProxyType(dict(self.unresolved)))
