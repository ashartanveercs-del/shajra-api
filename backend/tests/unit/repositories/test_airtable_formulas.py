import pytest
from pyairtable.formulas import match

from repositories.airtable.formulas import (
    case_insensitive_exact,
    case_insensitive_substring,
    exact_match,
)


def test_exact_match_escapes_user_text() -> None:
    value = "Robert') & DELETE() & ('"

    assert exact_match("FullName", value) == str(match({"FullName": value}))


def test_field_name_is_allowlisted() -> None:
    with pytest.raises(ValueError, match="Unsupported Airtable field"):
        exact_match("FullName})", "Ashar")


def test_case_insensitive_substring_uses_a_literal_formula_value() -> None:
    formula = case_insensitive_substring("FullName", r"O'Connor\) & DELETE()")

    assert formula == r"FIND(LOWER('O\'Connor\\) & DELETE()'), LOWER({FullName}))"


def test_case_insensitive_exact_uses_the_fixed_email_field() -> None:
    assert case_insensitive_exact("Email", "Ada@Example.test") == (
        "LOWER({Email})=LOWER('Ada@Example.test')"
    )
