"""Helper entity management for My Rail Commute."""
from __future__ import annotations

import logging
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
    HELPER_FAVOURITES_PREFIX,
    HELPER_FLAGGED_PREFIX,
    HELPER_MAX_LENGTH,
)

_LOGGER = logging.getLogger(__name__)

_INPUT_TEXT_DOMAIN = "input_text"
_STORAGE_VERSION = 1


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
