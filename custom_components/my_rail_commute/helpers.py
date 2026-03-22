"""Helper entity management for My Rail Commute."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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

    # The input_text storage collection is registered under hass.data["input_text"]
    # as {"storage_collection": ..., ...} during HA core setup.
    it_data = hass.data.get(_INPUT_TEXT_DOMAIN, {})
    storage_collection = (
        it_data.get("storage_collection") if isinstance(it_data, dict) else None
    )

    if storage_collection is None:
        _LOGGER.warning(
            "input_text storage collection not available; "
            "helpers will not be created automatically"
        )
        return

    for entity_id, name, item_id in helpers_to_create:
        if hass.states.get(entity_id):
            _LOGGER.debug("Helper %s already exists, skipping", entity_id)
            continue
        try:
            await storage_collection.async_create_item(
                {"id": item_id, "name": name, "max": HELPER_MAX_LENGTH, "mode": "text"}
            )
            _LOGGER.info("Created helper %s", entity_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not create helper %s: %s", entity_id, err)
