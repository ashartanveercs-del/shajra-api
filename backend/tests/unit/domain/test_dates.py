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


def test_precision_aware_ordering_uses_possible_ranges():
    year = PartialDate.parse("1960")
    next_year = PartialDate.parse("1961")
    assert year.latest < next_year.earliest
