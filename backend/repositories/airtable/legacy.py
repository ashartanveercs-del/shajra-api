"""Read-only access to the pre-normalized ApprovedMembers table."""

from collections.abc import Callable
from typing import Protocol

from repositories.airtable.mappers import LegacySnapshot, legacy_snapshot_from_records


class ApprovedMembersTable(Protocol):
    def all(self) -> list[dict[str, object]]: ...


class LegacySnapshotRepository:
    """Read a stable legacy snapshot without attempting any Airtable mutation."""

    def __init__(
        self,
        table: ApprovedMembersTable,
        *,
        sleep: Callable[[float], None],
        max_attempts: int = 5,
    ) -> None:
        self._table = table
        self._sleep = sleep
        self._max_attempts = max_attempts

    def load(self) -> LegacySnapshot:
        for attempt in range(self._max_attempts):
            try:
                return legacy_snapshot_from_records(self._table.all())
            except Exception as error:
                if not _is_rate_limited(error) or attempt == self._max_attempts - 1:
                    raise
                self._sleep(_retry_delay(error, attempt))
        raise RuntimeError("Legacy Airtable retry loop unexpectedly exited")


def _is_rate_limited(error: Exception) -> bool:
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 429


def _retry_delay(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after is not None:
        try:
            return min(30.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return min(30.0, float(2**attempt))
