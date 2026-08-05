import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"


def _canonical_parts(raw: str) -> tuple[DatePrecision, date, date]:
    if re.fullmatch(r"\d{4}", raw):
        year = int(raw)
        return DatePrecision.YEAR, date(year, 1, 1), date(year, 12, 31)
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        year, month = map(int, raw.split("-"))
        last = calendar.monthrange(year, month)[1]
        return DatePrecision.MONTH, date(year, month, 1), date(year, month, last)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        parsed = date.fromisoformat(raw)
        return DatePrecision.DAY, parsed, parsed
    raise ValueError("Date must be YYYY, YYYY-MM, or YYYY-MM-DD")


@dataclass(frozen=True, slots=True)
class PartialDate:
    value: str
    precision: DatePrecision
    earliest: date
    latest: date

    def __post_init__(self) -> None:
        precision, earliest, latest = _canonical_parts(self.value)
        if not isinstance(self.precision, DatePrecision):
            raise ValueError("PartialDate precision must be a DatePrecision")
        if (self.precision, self.earliest, self.latest) != (
            precision,
            earliest,
            latest,
        ):
            raise ValueError("PartialDate fields must match value")

    @classmethod
    def parse(cls, raw: str) -> "PartialDate":
        precision, earliest, latest = _canonical_parts(raw)
        return cls(raw, precision, earliest, latest)
