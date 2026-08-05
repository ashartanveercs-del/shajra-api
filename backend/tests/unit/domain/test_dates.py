from datetime import date

import pytest

from domain.dates import DatePrecision, PartialDate


@pytest.mark.parametrize(
    ("raw", "precision"),
    [
        ("1960", DatePrecision.YEAR),
        ("1960-04", DatePrecision.MONTH),
        ("1960-04-23", DatePrecision.DAY),
    ],
)
def test_parse_supported_partial_dates(raw, precision):
    assert PartialDate.parse(raw).precision is precision


def test_rejects_invalid_calendar_date():
    with pytest.raises(ValueError):
        PartialDate.parse("2024-02-31")


def test_rejects_unsupported_date_format():
    with pytest.raises(ValueError, match="Date must be YYYY, YYYY-MM, or YYYY-MM-DD"):
        PartialDate.parse("1960/04")


@pytest.mark.parametrize(
    ("precision", "earliest", "latest"),
    [
        (DatePrecision.DAY, date(1960, 1, 1), date(1960, 12, 31)),
        (DatePrecision.YEAR, date(1959, 1, 1), date(1960, 12, 31)),
        (DatePrecision.YEAR, date(1960, 1, 1), date(1960, 1, 1)),
    ],
)
def test_direct_construction_rejects_noncanonical_partial_date_fields(
    precision, earliest, latest
):
    with pytest.raises(ValueError, match="PartialDate fields must match value"):
        PartialDate("1960", precision, earliest, latest)


def test_direct_construction_rejects_string_precision_with_matching_value():
    with pytest.raises(ValueError, match="PartialDate precision must be a DatePrecision"):
        PartialDate(
            "1960",
            "year",  # type: ignore[arg-type]
            date(1960, 1, 1),
            date(1960, 12, 31),
        )


def test_precision_aware_ordering_uses_possible_ranges():
    year = PartialDate.parse("1960")
    next_year = PartialDate.parse("1961")
    assert year.latest < next_year.earliest
