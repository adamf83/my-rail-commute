"""The My Rail Commute integration."""
from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import NationalRailAPI
from .const import (
    CONF_DESTINATION,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    DOMAIN,
)
from .coordinator import NationalRailDataUpdateCoordinator
from .statistics import CommuteStatisticsStore

SERVICE_GET_HISTORICAL_RAW_DATA = "get_historical_raw_data"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up My Rail Commute from a config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry

    Returns:
        True if setup was successful
    """
    _LOGGER.debug("Setting up My Rail Commute integration")

    try:
        # Get configuration (merge data and options)
        config = {**entry.data, **entry.options}

        _LOGGER.debug(
            "Config for setup: origin=%s, destination=%s, time_window=%s, num_services=%s",
            config.get(CONF_ORIGIN),
            config.get(CONF_DESTINATION),
            config.get(CONF_TIME_WINDOW),
            config.get(CONF_NUM_SERVICES),
        )

        # Create API client
        session = async_get_clientsession(hass)
        api = NationalRailAPI(config[CONF_API_KEY], session)

        # Create coordinator
        coordinator = NationalRailDataUpdateCoordinator(
            hass,
            api,
            config,
        )

        # Set up historical statistics store
        stats_store = CommuteStatisticsStore(hass, entry.entry_id)
        await stats_store.async_load()
        coordinator.stats_store = stats_store

        # Fetch initial data
        _LOGGER.debug("Fetching initial data for %s -> %s", config.get(CONF_ORIGIN), config.get(CONF_DESTINATION))
        await coordinator.async_config_entry_first_refresh()

        # Store coordinator
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register update listener for options changes
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))

        # Register domain-wide service (only once across all entries)
        if not hass.services.has_service(DOMAIN, SERVICE_GET_HISTORICAL_RAW_DATA):
            async def _handle_get_historical_raw_data(call: ServiceCall) -> dict:
                entry_id = call.data["entry_id"]
                if entry_id not in hass.data.get(DOMAIN, {}):
                    raise ServiceValidationError(
                        f"No commute found with entry_id: {entry_id}"
                    )
                coordinator = hass.data[DOMAIN][entry_id]
                return {"days": coordinator.stats_store.get_raw_data()}

            hass.services.async_register(
                DOMAIN,
                SERVICE_GET_HISTORICAL_RAW_DATA,
                _handle_get_historical_raw_data,
                schema=vol.Schema({vol.Required("entry_id"): cv.string}),
                supports_response=SupportsResponse.ONLY,
            )

        _LOGGER.debug("My Rail Commute integration setup complete")

        return True

    except Exception as err:
        _LOGGER.error("Error setting up My Rail Commute: %s", err, exc_info=True)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry

    Returns:
        True if unload was successful
    """
    _LOGGER.debug("Unloading My Rail Commute integration")

    # Unload platforms
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Clean up API resources before removing coordinator
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.api.close()

        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove domain-wide services when the last entry is unloaded
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_GET_HISTORICAL_RAW_DATA)

    return unload_ok


async def async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove stale train entities when num_services is reduced.

    Args:
        hass: Home Assistant instance
        entry: Config entry
    """
    # Get the new num_services from options (or data if not in options)
    config = {**entry.data, **entry.options}
    new_num_services = config.get(CONF_NUM_SERVICES, 5)

    _LOGGER.debug("Cleaning up stale entities (keeping %s trains)", new_num_services)

    # Get entity registry
    entity_reg = er.async_get(hass)

    # Matches both the single-leg shape ({entry_id}_train_{n}) and the
    # multi-leg shape ({entry_id}_leg{leg}_train_{n})
    leg_train_re = re.compile(rf"^{re.escape(entry.entry_id)}_(?:leg\d+_)?train_(\d+)$")

    # Find all train entities for this config entry
    entities_to_remove = []
    for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        # Check if this is a train entity with a number > new_num_services
        match = leg_train_re.match(entity.unique_id)
        if match:
            train_number = int(match.group(1))
            if train_number > new_num_services:
                entities_to_remove.append((entity.entity_id, train_number))
                _LOGGER.debug(
                    "Found stale train entity: %s (train_%s)",
                    entity.entity_id,
                    train_number,
                )

    # Remove stale entities
    for entity_id, train_number in entities_to_remove:
        _LOGGER.info("Removing stale entity: %s (train_%s)", entity_id, train_number)
        entity_reg.async_remove(entity_id)

    if entities_to_remove:
        _LOGGER.info("Removed %s stale train entities", len(entities_to_remove))
    else:
        _LOGGER.debug("No stale entities to remove")


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change.

    Args:
        hass: Home Assistant instance
        entry: Config entry
    """
    _LOGGER.debug("Reloading My Rail Commute integration")

    # Clean up stale entities before reloading
    await async_cleanup_stale_entities(hass, entry)

    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry

    Returns:
        True if migration was successful
    """
    _LOGGER.debug("Migrating from version %s", entry.version)

    if entry.version == 1:
        # No migrations needed yet
        pass

    _LOGGER.debug("Migration to version %s successful", entry.version)

    return True
