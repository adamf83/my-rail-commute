"""The My Rail Commute integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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

        # Fetch initial data
        _LOGGER.debug("Fetching initial data for %s -> %s", config[CONF_ORIGIN], config[CONF_DESTINATION])
        await coordinator.async_config_entry_first_refresh()

        # Store coordinator
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register update listener for options changes
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))

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
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change.

    Args:
        hass: Home Assistant instance
        entry: Config entry
    """
    _LOGGER.debug("Reloading My Rail Commute integration")
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
