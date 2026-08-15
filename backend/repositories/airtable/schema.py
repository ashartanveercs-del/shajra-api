"""Canonical Airtable schema used by the normalized repositories."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

from pyairtable.models.schema import BaseSchema

AirtableFieldType = Literal["singleLineText", "multilineText", "number", "checkbox"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    airtable_type: AirtableFieldType
    required_options: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def create_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "type": self.airtable_type}
        if self.required_options:
            payload["options"] = dict(self.required_options)
        return payload


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    primary_field: str
    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        names = tuple(field.name for field in self.fields)
        if not names or names[0] != self.primary_field or len(names) != len(set(names)):
            raise ValueError(
                "Primary field must be first and field names must be unique"
            )

    def create_fields(self) -> list[dict[str, object]]:
        return [field.create_payload() for field in self.fields]


SchemaIssueCode = Literal[
    "MISSING_TABLE",
    "PRIMARY_FIELD_MISMATCH",
    "MISSING_FIELD",
    "UNEXPECTED_FIELD",
    "FIELD_TYPE_MISMATCH",
    "FIELD_OPTION_MISMATCH",
]


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    code: SchemaIssueCode
    table: str
    field: str | None
    expected: object | None
    actual: object | None


def text(name: str) -> FieldSpec:
    return FieldSpec(name, "singleLineText")


def long_text(name: str) -> FieldSpec:
    return FieldSpec(name, "multilineText")


def integer(name: str) -> FieldSpec:
    return FieldSpec(name, "number", MappingProxyType({"precision": 0}))


def checkbox(name: str) -> FieldSpec:
    return FieldSpec(
        name,
        "checkbox",
        MappingProxyType({"icon": "check", "color": "greenBright"}),
    )


NORMALIZED_SCHEMA = MappingProxyType(
    {
        "PersonVersions": TableSpec(
            "PersonVersions",
            "PersonId",
            (
                text("PersonId"),
                text("GraphScope"),
                text("FullName"),
                text("Gender"),
                text("Birth"),
                text("Death"),
                text("IsAlive"),
                text("PrimaryFamilyUnitId"),
                checkbox("Archived"),
                integer("VersionRevision"),
                integer("Revision"),
                text("OperationId"),
                integer("FencingToken"),
                checkbox("IsTombstone"),
            ),
        ),
        "FamilyUnits": TableSpec(
            "FamilyUnits",
            "FamilyUnitId",
            (
                text("FamilyUnitId"),
                text("GraphScope"),
                text("Kind"),
                text("AdultAId"),
                text("AdultBId"),
                text("Status"),
                text("Start"),
                text("End"),
                checkbox("DistinctUnionConfirmed"),
                integer("CreatedRevision"),
                integer("Revision"),
                text("OperationId"),
                integer("FencingToken"),
                checkbox("IsTombstone"),
            ),
        ),
        "ParentChildLinks": TableSpec(
            "ParentChildLinks",
            "LinkId",
            (
                text("LinkId"),
                text("GraphScope"),
                text("ParentId"),
                text("ChildId"),
                text("Role"),
                text("RelationshipType"),
                text("FamilyUnitId"),
                integer("CreatedRevision"),
                integer("Revision"),
                text("OperationId"),
                integer("FencingToken"),
                checkbox("IsTombstone"),
            ),
        ),
        "UnresolvedRelationships": TableSpec(
            "UnresolvedRelationships",
            "UnresolvedId",
            (
                text("UnresolvedId"),
                text("GraphScope"),
                text("SubjectPersonId"),
                text("Kind"),
                text("UnresolvedName"),
                integer("CreatedRevision"),
                integer("Revision"),
                text("OperationId"),
                integer("FencingToken"),
                checkbox("IsTombstone"),
            ),
        ),
        "ChangeLog": TableSpec(
            "ChangeLog",
            "OperationId",
            (
                text("OperationId"),
                text("IdempotencyKey"),
                text("State"),
                text("ActorId"),
                text("RequestId"),
                text("SourceReference"),
                integer("ExpectedRevision"),
                integer("ResultRevision"),
                integer("FencingToken"),
                long_text("CommandsJson"),
                long_text("BeforeSnapshotJson"),
                long_text("AfterSnapshotJson"),
                long_text("InverseWriteSetJson"),
                text("CommitScope"),
                long_text("GraphCommitJson"),
                text("CommitSha256"),
                text("CreatedAt"),
                text("UpdatedAt"),
            ),
        ),
        "GraphCommits": TableSpec(
            "GraphCommits",
            "OperationId",
            (
                text("OperationId"),
                text("GraphScope"),
                integer("Revision"),
                integer("FencingToken"),
                text("PermitId"),
                text("SemanticChecksum"),
                text("CommittedAt"),
            ),
        ),
        "GraphState": TableSpec(
            "GraphState",
            "StateKey",
            (
                text("StateKey"),
                integer("Revision"),
                text("HeadOperationId"),
                integer("FencingToken"),
                text("SemanticChecksum"),
                text("UpdatedAt"),
            ),
        ),
        "EnrichmentAttempts": TableSpec(
            "EnrichmentAttempts",
            "AttemptId",
            (
                text("AttemptId"),
                integer("Sequence"),
                text("Status"),
                text("SubmissionId"),
                text("InputSha256"),
                text("RequestSha256"),
                text("PromptVersion"),
                text("Model"),
                long_text("CandidateIdsJson"),
                long_text("SuggestionJson"),
                text("SuggestionSha256"),
                text("ErrorCode"),
                text("CreatedAt"),
            ),
        ),
        "SubmissionReviews": TableSpec(
            "SubmissionReviews",
            "ReviewId",
            (
                text("ReviewId"),
                text("DecisionId"),
                text("AttemptId"),
                text("SuggestionKey"),
                text("Decision"),
                text("ReplacementPersonId"),
                long_text("ReplacementValue"),
                text("ActorId"),
                text("Status"),
                text("CreatedAt"),
            ),
        ),
    }
)


def _actual_options(field_schema: object) -> Mapping[str, object]:
    options = getattr(field_schema, "options", None)
    if options is None:
        return {}
    if isinstance(options, Mapping):
        return options
    return options.model_dump(mode="json", by_alias=True, exclude_none=True)


def validate_normalized_schema(actual: BaseSchema) -> tuple[SchemaIssue, ...]:
    issues: list[SchemaIssue] = []
    actual_tables = {table.name: table for table in actual.tables}
    for table_name, expected_table in NORMALIZED_SCHEMA.items():
        actual_table = actual_tables.get(table_name)
        if actual_table is None:
            issues.append(
                SchemaIssue("MISSING_TABLE", table_name, None, table_name, None)
            )
            continue

        actual_fields = {field.name: field for field in actual_table.fields}
        primary = next(
            (
                field.name
                for field in actual_table.fields
                if field.id == actual_table.primary_field_id
            ),
            None,
        )
        if primary != expected_table.primary_field:
            issues.append(
                SchemaIssue(
                    "PRIMARY_FIELD_MISMATCH",
                    table_name,
                    None,
                    expected_table.primary_field,
                    primary,
                )
            )

        expected_names = {field.name for field in expected_table.fields}
        for missing in sorted(expected_names - actual_fields.keys()):
            issues.append(
                SchemaIssue("MISSING_FIELD", table_name, missing, missing, None)
            )
        for extra in sorted(actual_fields.keys() - expected_names):
            issues.append(
                SchemaIssue("UNEXPECTED_FIELD", table_name, extra, None, extra)
            )

        for expected_field in expected_table.fields:
            actual_field = actual_fields.get(expected_field.name)
            if actual_field is None:
                continue
            actual_type = str(getattr(actual_field.type, "value", actual_field.type))
            if actual_type != expected_field.airtable_type:
                issues.append(
                    SchemaIssue(
                        "FIELD_TYPE_MISMATCH",
                        table_name,
                        expected_field.name,
                        expected_field.airtable_type,
                        actual_type,
                    )
                )
                continue
            actual_options = _actual_options(actual_field)
            for key, expected_value in expected_field.required_options.items():
                if actual_options.get(key) != expected_value:
                    issues.append(
                        SchemaIssue(
                            "FIELD_OPTION_MISMATCH",
                            table_name,
                            expected_field.name,
                            {key: expected_value},
                            {key: actual_options.get(key)},
                        )
                    )
    return tuple(issues)
