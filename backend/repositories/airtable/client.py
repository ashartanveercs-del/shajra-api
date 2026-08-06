"""Lazy pyairtable client construction for repository adapters."""

from collections.abc import Callable
from typing import Any

from pyairtable import Api


class AirtableClient:
    def __init__(
        self,
        personal_access_token: str | None,
        base_id: str | None,
        *,
        api_factory: Callable[[str], Any] = Api,
    ) -> None:
        self._personal_access_token = personal_access_token
        self._base_id = base_id
        self._api_factory = api_factory
        self._api: Any | None = None

    def table(self, table_name: str) -> Any:
        _, base_id = self._credentials()
        return self.api.table(base_id, table_name)

    @property
    def api(self) -> Any:
        personal_access_token, _ = self._credentials()
        if self._api is None:
            self._api = self._api_factory(personal_access_token)
        return self._api

    def _credentials(self) -> tuple[str, str]:
        if not self._personal_access_token or not self._base_id:
            raise RuntimeError("Airtable credentials are not configured")
        return self._personal_access_token, self._base_id
