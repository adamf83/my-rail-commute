"""The My Rail Commute integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import NationalRailAPI
from .const import (
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    DOMAIN,
    SERVICE_ADD_FAVOURITE,
    SERVICE_CLEAR_FAVOURITES,
    SERVICE_CLEAR_FLAGGED,
    SERVICE_FLAG_TRAIN,
    SERVICE_REMOVE_FAVOURITE,
    SERVICE_UNFLAG_TRAIN,
)
from .coordinator import NationalRailDataUpdateCoordinator
from .helpers import FlagsStore, async_ensure_helpers

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

        # Create flags store (persists favourites/flagged trains across restarts)
        commute_name = config.get(CONF_COMMUTE_NAME, "")
        base = slugify(commute_name)
        flags_store = FlagsStore(hass, base)
        await flags_store.async_load()

        # Store coordinator and flags store together
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "flags_store": flags_store,
        }

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Create input_text helpers for the card to store favourites and flagged trains
        await async_ensure_helpers(hass, entry)

        # Register HA services (idempotent — only registers once across all entries)
        _async_register_services(hass)

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
        # Clean up API resources before removing coordinator
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        await entry_data["coordinator"].api.close()

        # Unregister services when last entry is removed
        if not hass.data[DOMAIN]:
            for service in (
                SERVICE_ADD_FAVOURITE,
                SERVICE_REMOVE_FAVOURITE,
                SERVICE_FLAG_TRAIN,
                SERVICE_UNFLAG_TRAIN,
                SERVICE_CLEAR_FAVOURITES,
                SERVICE_CLEAR_FLAGGED,
            ):
                hass.services.async_remove(DOMAIN, service)

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

    # Find all train entities for this config entry
    entities_to_remove = []
    for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
        # Check if this is a train entity with a number > new_num_services
        if entity.unique_id.startswith(f"{entry.entry_id}_train_"):
            # Extract train number from unique_id (format: {entry_id}_train_{number})
            try:
                train_number = int(entity.unique_id.split("_train_")[-1])
                if train_number > new_num_services:
                    entities_to_remove.append((entity.entity_id, train_number))
                    _LOGGER.debug(
                        "Found stale train entity: %s (train_%s)",
                        entity.entity_id,
                        train_number,
                    )
            except (ValueError, IndexError):
                # Skip if we can't parse the train number
                continue

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


def _async_register_services(hass: HomeAssistant) -> None:
    """Register HA services for managing flags and favourites.

    This is idempotent — services are only registered once even when multiple
    commute entries are set up.
    """
    if hass.services.has_service(DOMAIN, SERVICE_ADD_FAVOURITE):
        return

    def _get_flags_store(call: ServiceCall) -> FlagsStore | None:
        """Retrieve the FlagsStore for the given entry_id from service call data."""
        entry_id: str = call.data["entry_id"]
        entry_data: dict[str, Any] | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if entry_data is None:
            _LOGGER.warning("Unknown entry_id in service call: %s", entry_id)
            return None
        return entry_data["flags_store"]

    async def handle_add_favourite(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_add_favourite(
                call.data["scheduled_departure"],
                call.data.get("operator"),
            )

    async def handle_remove_favourite(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_remove_favourite(call.data["scheduled_departure"])

    async def handle_flag_train(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_flag_train(
                call.data["service_id"],
                call.data["scheduled_departure"],
                call.data.get("operator"),
                call.data.get("reason"),
            )

    async def handle_unflag_train(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_unflag_train(call.data["service_id"])

    async def handle_clear_favourites(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_clear_favourites()

    async def handle_clear_flagged(call: ServiceCall) -> None:
        store = _get_flags_store(call)
        if store:
            await store.async_clear_flagged()

    hass.services.async_register(DOMAIN, SERVICE_ADD_FAVOURITE, handle_add_favourite)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_FAVOURITE, handle_remove_favourite)
    hass.services.async_register(DOMAIN, SERVICE_FLAG_TRAIN, handle_flag_train)
    hass.services.async_register(DOMAIN, SERVICE_UNFLAG_TRAIN, handle_unflag_train)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_FAVOURITES, handle_clear_favourites)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_FLAGGED, handle_clear_flagged)


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
