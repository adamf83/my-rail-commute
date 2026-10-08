"""Persistent storage for Delay Repay claim records."""

from __future__ import annotations

from collections.abc import Iterator
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DELAY_REPAY_STORAGE_VERSION, DOMAIN
from .models import ClaimRecord

_LOGGER = logging.getLogger(__name__)


class DelayRepayStore:
    """Holds claim records keyed by journey and persists them via HA's Store.

    Writes happen only when something changed, so the frequent coordinator
    updates cost nothing when there is nothing to record.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store; its file is separate from the statistics store."""
        self._store: Store[dict[str, Any]] = Store(
            hass, DELAY_REPAY_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_delay_repay"
        )
        self._records: dict[str, ClaimRecord] = {}
        self._dirty = False

    async def async_load(self) -> None:
        """Load persisted records, skipping any that cannot be read."""
        raw = await self._store.async_load()
        self._records = {}
        self._dirty = False
        if not raw:
            return
        for key, data in (raw.get("records") or {}).items():
            try:
                record = ClaimRecord.from_dict(data)
            except (ValueError, AttributeError):
                _LOGGER.warning("Skipping unreadable Delay Repay record %s", key)
                self._dirty = True
                continue
            self._records[record.key] = record
        _LOGGER.debug("Loaded %d Delay Repay records", len(self._records))

    async def async_save_if_dirty(self) -> bool:
        """Persist if anything changed since the last save. Returns True if saved."""
        if not self._dirty:
            return False
        await self._store.async_save(
            {
                "version": DELAY_REPAY_STORAGE_VERSION,
                "records": {k: r.to_dict() for k, r in self._records.items()},
            }
        )
        self._dirty = False
        return True

    def get(self, key: str) -> ClaimRecord | None:
        """Return the record for a key, if any."""
        return self._records.get(key)

    def values(self) -> Iterator[ClaimRecord]:
        """Iterate over a snapshot of all records."""
        return iter(list(self._records.values()))

    def set(self, record: ClaimRecord) -> None:
        """Insert or replace a record (no-op if identical)."""
        if self._records.get(record.key) == record:
            return
        self._records[record.key] = record
        self._dirty = True

    def remove(self, key: str) -> None:
        """Delete a record if present."""
        if self._records.pop(key, None) is not None:
            self._dirty = True

    def __len__(self) -> int:
        """Return the number of stored records."""
        return len(self._records)
