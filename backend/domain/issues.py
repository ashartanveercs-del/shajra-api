from dataclasses import dataclass
from enum import StrEnum

from domain.ids import FamilyUnitId, LinkId, PersonId

SELF_PARENT = "SELF_PARENT"
SELF_PARTNER = "SELF_PARTNER"
MISSING_PERSON = "MISSING_PERSON"
MISSING_FAMILY_UNIT = "MISSING_FAMILY_UNIT"
DUPLICATE_LINK = "DUPLICATE_LINK"
DUPLICATE_FAMILY_UNIT = "DUPLICATE_FAMILY_UNIT"
DUPLICATE_UNRESOLVED_RELATIONSHIP = "DUPLICATE_UNRESOLVED_RELATIONSHIP"
ANCESTRY_CYCLE = "ANCESTRY_CYCLE"
PRIMARY_UNIT_MISMATCH = "PRIMARY_UNIT_MISMATCH"
FAMILY_UNIT_PARENT_MISMATCH = "FAMILY_UNIT_PARENT_MISMATCH"
DEATH_BEFORE_BIRTH = "DEATH_BEFORE_BIRTH"
UNION_END_BEFORE_START = "UNION_END_BEFORE_START"
IMPOSSIBLE_PARENT_AGE = "IMPOSSIBLE_PARENT_AGE"
SUSPICIOUS_PARENT_AGE = "SUSPICIOUS_PARENT_AGE"
ARCHIVED_RELATIONSHIP_OMITTED = "ARCHIVED_RELATIONSHIP_OMITTED"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class GraphIssue:
    code: str
    severity: IssueSeverity
    message: str
    person_ids: tuple[PersonId, ...] = ()
    family_unit_ids: tuple[FamilyUnitId, ...] = ()
    link_ids: tuple[LinkId, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[GraphIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)
