"""Helper entity management for My Rail Commute."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from homeassistant.util import slugify

from .const import (
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_ORIGIN,
    EVENT_FLAGS_UPDATED,
    FLAGS_STORAGE_KEY_PREFIX,
    FLAGS_STORAGE_VERSION,
    HELPER_FAVOURITES_PREFIX,
    HELPER_FLAGGED_PREFIX,
    HELPER_MAX_LENGTH,
    STORE_KEY_FAVOURITES,
    STORE_KEY_FLAGGED,
)

_LOGGER = logging.getLogger(__name__)

_INPUT_TEXT_DOMAIN = "input_text"
_STORAGE_VERSION = 1


class FlagsStore:
    """Persistent storage for favourites and flagged trains.

    Data is stored in HA's .storage directory as JSON and survives restarts.
    Mutations fire EVENT_FLAGS_UPDATED so sensors can refresh.
    """

    def __init__(self, hass: HomeAssistant, base: str) -> None:
        """Initialise the store.

        Args:
            hass: Home Assistant instance
            base: Slugified commute name used as part of the storage key
        """
        self._hass = hass
        self._base = base
        self._store: Store[dict[str, Any]] = Store(
            hass, FLAGS_STORAGE_VERSION, f"{FLAGS_STORAGE_KEY_PREFIX}{base}"
        )
        self._data: dict[str, Any] = {
            STORE_KEY_FAVOURITES: [],
            STORE_KEY_FLAGGED: [],
        }

    async def async_load(self) -> None:
        """Load persisted data from disk into memory."""
        stored = await self._store.async_load()
        if stored:
            self._data[STORE_KEY_FAVOURITES] = stored.get(STORE_KEY_FAVOURITES, [])
            self._data[STORE_KEY_FLAGGED] = stored.get(STORE_KEY_FLAGGED, [])

    def get_favourites(self) -> list[dict[str, Any]]:
        """Return the current in-memory list of favourites."""
        return list(self._data[STORE_KEY_FAVOURITES])

    def get_flagged(self) -> list[dict[str, Any]]:
        """Return the current in-memory list of flagged trains."""
        return list(self._data[STORE_KEY_FLAGGED])

    async def async_add_favourite(
        self,
        scheduled_departure: str,
        operator: str | None = None,
    ) -> None:
        """Add a favourite by scheduled departure time (deduplicated).

        Args:
            scheduled_departure: Scheduled departure time string, e.g. "08:15"
            operator: Optional operator name
        """
        favourites: list[dict[str, Any]] = self._data[STORE_KEY_FAVOURITES]
        if any(f["scheduled_departure"] == scheduled_departure for f in favourites):
            return
        entry: dict[str, Any] = {
            "scheduled_departure": scheduled_departure,
            "added_at": datetime.now(UTC).isoformat(),
        }
        if operator:
            entry["operator"] = operator
        favourites.append(entry)
        await self._async_save()

    async def async_remove_favourite(self, scheduled_departure: str) -> None:
        """Remove a favourite by scheduled departure time.

        Args:
            scheduled_departure: Scheduled departure time to remove
        """
        self._data[STORE_KEY_FAVOURITES] = [
            f
            for f in self._data[STORE_KEY_FAVOURITES]
            if f["scheduled_departure"] != scheduled_departure
        ]
        await self._async_save()

    async def async_flag_train(
        self,
        service_id: str,
        scheduled_departure: str,
        operator: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Flag a train by service ID (deduplicated).

        Args:
            service_id: Unique service identifier from the API
            scheduled_departure: Scheduled departure time string
            operator: Optional operator name
            reason: Optional reason for flagging (e.g. "delay", "cancellation")
        """
        flagged: list[dict[str, Any]] = self._data[STORE_KEY_FLAGGED]
        if any(f["service_id"] == service_id for f in flagged):
            return
        entry: dict[str, Any] = {
            "service_id": service_id,
            "scheduled_departure": scheduled_departure,
            "flagged_at": datetime.now(UTC).isoformat(),
        }
        if operator:
            entry["operator"] = operator
        if reason:
            entry["reason"] = reason
        flagged.append(entry)
        await self._async_save()

    async def async_unflag_train(self, service_id: str) -> None:
        """Remove a flagged train by service ID.

        Args:
            service_id: Unique service identifier to remove
        """
        self._data[STORE_KEY_FLAGGED] = [
            f
            for f in self._data[STORE_KEY_FLAGGED]
            if f["service_id"] != service_id
        ]
        await self._async_save()

    async def async_clear_favourites(self) -> None:
        """Remove all favourites."""
        self._data[STORE_KEY_FAVOURITES] = []
        await self._async_save()

    async def async_clear_flagged(self) -> None:
        """Remove all flagged trains."""
        self._data[STORE_KEY_FLAGGED] = []
        await self._async_save()

    async def _async_save(self) -> None:
        """Persist current data to disk and notify listeners."""
        await self._store.async_save(self._data)
        self._hass.bus.async_fire(
            EVENT_FLAGS_UPDATED,
            {"base": self._base},
        )


async def async_ensure_helpers(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create input_text helpers for this commute if they don't already exist.

    Creates two helpers per commute route:
    - input_text.rail_commute_favourites_<base>  (stores favourite departure times)
    - input_text.rail_commute_flagged_<base>     (stores flagged-train metadata)

    The base is slugify(commute_name), which matches the entity ID that the
    Lovelace card derives from the summary sensor entity ID.
    """
    commute_name = entry.data.get(CONF_COMMUTE_NAME, "")
    origin = entry.data.get(CONF_ORIGIN, "")
    destination = entry.data.get(CONF_DESTINATION, "")
    base = slugify(commute_name)

    helpers_to_create = [
        (
            f"{_INPUT_TEXT_DOMAIN}.{HELPER_FAVOURITES_PREFIX}{base}",
            f"Rail Commute Favourites - {origin} to {destination}",
            f"{HELPER_FAVOURITES_PREFIX}{base}",
        ),
        (
            f"{_INPUT_TEXT_DOMAIN}.{HELPER_FLAGGED_PREFIX}{base}",
            f"Rail Commute Flagged - {origin} to {destination}",
            f"{HELPER_FLAGGED_PREFIX}{base}",
        ),
    ]

    # Ensure input_text component is loaded. async_setup_component is idempotent.
    if not await async_setup_component(hass, _INPUT_TEXT_DOMAIN, {}):
        _LOGGER.warning(
            "Could not load input_text component; helpers will not be created automatically"
        )
        return

    # Write new items to the Store so they persist across HA restarts.
    # The input_text storage collection loads from this file on every startup.
    store: Store[dict[str, Any]] = Store(hass, _STORAGE_VERSION, _INPUT_TEXT_DOMAIN)
    data: dict[str, Any] = await store.async_load() or {"items": []}
    existing_ids = {item["id"] for item in data.get("items", [])}

    new_items: list[tuple[str, dict[str, Any]]] = []
    for entity_id_str, name, item_id in helpers_to_create:
        if item_id not in existing_ids and not hass.states.get(entity_id_str):
            new_items.append(
                (
                    entity_id_str,
                    {
                        "id": item_id,
                        "name": name,
                        "min": 0,
                        "max": HELPER_MAX_LENGTH,
                        "mode": "text",
                    },
                )
            )

    if not new_items:
        _LOGGER.debug("All helpers already exist, nothing to create")
        return

    data.setdefault("items", []).extend(config for _, config in new_items)
    await store.async_save(data)

    # Also add entities to the live EntityComponent so they appear immediately
    # in the current HA session without requiring a restart.
    # EntityComponent stores itself in hass.data[DATA_INSTANCES][domain] on init.
    component = hass.data.get(DATA_INSTANCES, {}).get(_INPUT_TEXT_DOMAIN)
    if component is None:
        _LOGGER.info(
            "input_text EntityComponent not found; "
            "helpers will be available after HA restart"
        )
        return

    # Import InputText here to avoid a hard dependency at module load time.
    from homeassistant.components.input_text import InputText  # noqa: PLC0415

    for entity_id_str, config in new_items:
        try:
            entity = InputText.from_storage(config)
            entity.entity_id = entity_id_str
            await component.async_add_entities([entity])
            _LOGGER.info("Created helper %s", entity_id_str)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not create helper %s: %s", entity_id_str, err)
