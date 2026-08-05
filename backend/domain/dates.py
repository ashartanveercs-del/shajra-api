import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class PartialDate:
    value: str
    precision: DatePrecision
    earliest: date
    latest: date

    @classmethod
    def parse(cls, raw: str) -> "PartialDate":
        if re.fullmatch(r"\d{4}", raw):
            year = int(raw)
            return cls(raw, DatePrecision.YEAR, date(year, 1, 1), date(year, 12, 31))
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            year, month = map(int, raw.split("-"))
            last = calendar.monthrange(year, month)[1]
            return cls(
                raw,
                DatePrecision.MONTH,
                date(year, month, 1),
                date(year, month, last),
            )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed = date.fromisoformat(raw)
            return cls(raw, DatePrecision.DAY, parsed, parsed)
        raise ValueError("Date must be YYYY, YYYY-MM, or YYYY-MM-DD")
