"""Safe formula construction for the limited legacy lookup surface."""

from pyairtable.formulas import EQ, FIND, LOWER, Field, match, to_formula

SEARCHABLE_FIELDS = frozenset({"PersonId", "FullName", "Status", "MemberRecordId"})
CASE_INSENSITIVE_EXACT_FIELDS = frozenset({"Email"})


def _validated_field(field: str) -> Field:
    if field not in SEARCHABLE_FIELDS:
        raise ValueError(f"Unsupported Airtable field: {field}")
    return Field(field)


def exact_match(field: str, value: str) -> str:
    _validated_field(field)
    return str(match({field: value}))


def case_insensitive_substring(field: str, value: str) -> str:
    return str(FIND(LOWER(to_formula(value)), LOWER(_validated_field(field))))


def case_insensitive_exact(field: str, value: str) -> str:
    if field not in CASE_INSENSITIVE_EXACT_FIELDS:
        raise ValueError(f"Unsupported Airtable field: {field}")
    return str(EQ(LOWER(Field(field)), LOWER(to_formula(value))))
