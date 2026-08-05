from types import SimpleNamespace

import pytest

from repositories.airtable.schema import NORMALIZED_SCHEMA, validate_normalized_schema


class FakeField:
    def __init__(self, name: str, field_id: str, field_type: str, **options: object) -> None:
        self.name = name
        self.id = field_id
        self.type = field_type
        self.options = options


class FakeTable:
    def __init__(self, name: str, primary_field_id: str, fields: list[FakeField]) -> None:
        self.name = name
        self.primary_field_id = primary_field_id
        self.fields = fields


def _schema_from_manifest() -> SimpleNamespace:
    tables = []
    for table_spec in NORMALIZED_SCHEMA.values():
        fields = [
            FakeField(
                field_spec.name,
                f"fld_{index}",
                field_spec.airtable_type,
                **dict(field_spec.required_options),
            )
            for index, field_spec in enumerate(table_spec.fields)
        ]
        tables.append(FakeTable(table_spec.name, fields[0].id, fields))
    return SimpleNamespace(tables=tables)


def _field(table: FakeTable, name: str) -> FakeField:
    return next(field for field in table.fields if field.name == name)


def test_normalized_schema_has_exactly_the_nine_canonical_tables() -> None:
    assert tuple(NORMALIZED_SCHEMA) == (
        "PersonVersions",
        "FamilyUnits",
        "ParentChildLinks",
        "UnresolvedRelationships",
        "ChangeLog",
        "GraphCommits",
        "GraphState",
        "EnrichmentAttempts",
        "SubmissionReviews",
    )


@pytest.mark.parametrize(
    "table_name",
    ("PersonVersions", "FamilyUnits", "ParentChildLinks", "UnresolvedRelationships"),
)
def test_entity_tables_require_authorization_and_tombstone_fields(table_name: str) -> None:
    fields = {field.name: field for field in NORMALIZED_SCHEMA[table_name].fields}

    assert fields["Revision"].airtable_type == "number"
    assert fields["Revision"].required_options == {"precision": 0}
    assert fields["FencingToken"].airtable_type == "number"
    assert fields["FencingToken"].required_options == {"precision": 0}
    assert fields["OperationId"].airtable_type == "singleLineText"
    assert fields["IsTombstone"].airtable_type == "checkbox"
    assert fields["IsTombstone"].required_options == {
        "icon": "check",
        "color": "greenBright",
    }


def test_schema_retains_required_compatibility_fields() -> None:
    person_fields = {field.name: field for field in NORMALIZED_SCHEMA["PersonVersions"].fields}
    change_log_fields = {field.name: field for field in NORMALIZED_SCHEMA["ChangeLog"].fields}
    graph_commit_fields = {
        field.name: field for field in NORMALIZED_SCHEMA["GraphCommits"].fields
    }

    assert person_fields["Archived"].airtable_type == "checkbox"
    assert graph_commit_fields["PermitId"].airtable_type == "singleLineText"
    assert change_log_fields["InverseWriteSetJson"].airtable_type == "multilineText"
    assert change_log_fields["CommitScope"].airtable_type == "singleLineText"
    assert change_log_fields["GraphCommitJson"].airtable_type == "multilineText"
    assert change_log_fields["CommitSha256"].airtable_type == "singleLineText"


def test_schema_validator_accepts_the_canonical_schema() -> None:
    assert validate_normalized_schema(_schema_from_manifest()) == ()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing_table", "MISSING_TABLE"),
        ("wrong_primary", "PRIMARY_FIELD_MISMATCH"),
        ("wrong_type", "FIELD_TYPE_MISMATCH"),
        ("wrong_precision", "FIELD_OPTION_MISMATCH"),
    ),
)
def test_schema_validator_reports_stable_issues(
    mutation: str, expected_code: str
) -> None:
    actual = _schema_from_manifest()
    person_versions = next(table for table in actual.tables if table.name == "PersonVersions")

    if mutation == "missing_table":
        actual.tables = [table for table in actual.tables if table.name != "GraphState"]
    elif mutation == "wrong_primary":
        person_versions.primary_field_id = _field(person_versions, "FullName").id
    elif mutation == "wrong_type":
        _field(person_versions, "Revision").type = "singleLineText"
    else:
        _field(person_versions, "Revision").options = {"precision": 2}

    issues = validate_normalized_schema(actual)

    assert any(issue.code == expected_code for issue in issues)
