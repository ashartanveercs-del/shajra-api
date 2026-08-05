from collections.abc import Sequence
from dataclasses import dataclass, replace

from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.models import (
    FamilyUnit,
    GraphSnapshot,
    ParentChildLink,
    Person,
    UnresolvedRelationship,
)


class CommandConflict(Exception):
    """A command cannot be structurally applied to the current snapshot."""


@dataclass(frozen=True, slots=True)
class AddPersonVersion:
    person: Person


@dataclass(frozen=True, slots=True)
class AddFamilyUnit:
    family_unit: FamilyUnit


@dataclass(frozen=True, slots=True)
class AddParentChildLink:
    link: ParentChildLink


@dataclass(frozen=True, slots=True)
class AddUnresolvedRelationship:
    annotation: UnresolvedRelationship


@dataclass(frozen=True, slots=True)
class SetPrimaryFamilyUnit:
    person_id: PersonId
    family_unit_id: FamilyUnitId | None


@dataclass(frozen=True, slots=True)
class ArchivePerson:
    person_id: PersonId


@dataclass(frozen=True, slots=True)
class SupersedeFamilyUnit:
    family_unit_id: FamilyUnitId
    replacement: FamilyUnit


@dataclass(frozen=True, slots=True)
class SupersedeParentChildLink:
    link_id: LinkId
    replacement: ParentChildLink


@dataclass(frozen=True, slots=True)
class SupersedeUnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId
    replacement: UnresolvedRelationship


@dataclass(frozen=True, slots=True)
class RemoveUnresolvedRelationship:
    unresolved_id: UnresolvedRelationshipId


GraphCommand = (
    AddPersonVersion
    | AddFamilyUnit
    | AddParentChildLink
    | AddUnresolvedRelationship
    | SetPrimaryFamilyUnit
    | ArchivePerson
    | SupersedeFamilyUnit
    | SupersedeParentChildLink
    | SupersedeUnresolvedRelationship
    | RemoveUnresolvedRelationship
)


def apply_commands(
    snapshot: GraphSnapshot, commands: Sequence[GraphCommand]
) -> GraphSnapshot:
    people = dict(snapshot.people)
    family_units = dict(snapshot.family_units)
    links = dict(snapshot.links)
    unresolved = dict(snapshot.unresolved)

    for command in commands:
        if isinstance(command, AddPersonVersion):
            people[command.person.person_id] = command.person
        elif isinstance(command, AddFamilyUnit):
            if command.family_unit.family_unit_id in family_units:
                raise CommandConflict("duplicate family unit ID")
            family_units[command.family_unit.family_unit_id] = command.family_unit
        elif isinstance(command, AddParentChildLink):
            if command.link.link_id in links:
                raise CommandConflict("duplicate link ID")
            links[command.link.link_id] = command.link
        elif isinstance(command, AddUnresolvedRelationship):
            if command.annotation.unresolved_id in unresolved:
                raise CommandConflict("duplicate unresolved relationship ID")
            unresolved[command.annotation.unresolved_id] = command.annotation
        elif isinstance(command, SetPrimaryFamilyUnit):
            if command.person_id not in people:
                raise CommandConflict("missing person ID")
            people[command.person_id] = replace(
                people[command.person_id], primary_family_unit_id=command.family_unit_id
            )
        elif isinstance(command, ArchivePerson):
            if command.person_id not in people:
                raise CommandConflict("missing person ID")
            people[command.person_id] = replace(
                people[command.person_id], archived=True
            )
        elif isinstance(command, SupersedeFamilyUnit):
            if command.family_unit_id not in family_units:
                raise CommandConflict("missing family unit ID")
            if command.replacement.family_unit_id != command.family_unit_id:
                raise CommandConflict("replacement ID does not match family unit ID")
            family_units[command.family_unit_id] = command.replacement
        elif isinstance(command, SupersedeParentChildLink):
            if command.link_id not in links:
                raise CommandConflict("missing link ID")
            if command.replacement.link_id != command.link_id:
                raise CommandConflict("replacement ID does not match link ID")
            links[command.link_id] = command.replacement
        elif isinstance(command, SupersedeUnresolvedRelationship):
            if command.unresolved_id not in unresolved:
                raise CommandConflict("missing unresolved relationship ID")
            if command.replacement.unresolved_id != command.unresolved_id:
                raise CommandConflict(
                    "replacement ID does not match unresolved relationship ID"
                )
            unresolved[command.unresolved_id] = command.replacement
        elif isinstance(command, RemoveUnresolvedRelationship):
            if command.unresolved_id not in unresolved:
                raise CommandConflict("missing unresolved relationship ID")
            del unresolved[command.unresolved_id]
        else:
            raise TypeError(f"Unsupported graph command: {command!r}")

    return GraphSnapshot(snapshot.state, people, family_units, links, unresolved)
