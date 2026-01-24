"""Binary sensor platform for My Rail Commute integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CANCELLED_COUNT,
    ATTR_DELAYED_COUNT,
    ATTR_DISRUPTION_REASONS,
    ATTR_MAX_DELAY,
    CONF_COMMUTE_NAME,
    DOMAIN,
)
from .coordinator import NationalRailDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up My Rail Commute binary sensor platform.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        async_add_entities: Callback to add entities
    """
    coordinator: NationalRailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Create disruption sensor
    entities: list[BinarySensorEntity] = [
        DisruptionSensor(coordinator, entry),
    ]

    _LOGGER.debug(
        "Setting up binary sensor entities for %s -> %s",
        coordinator.origin,
        coordinator.destination,
    )

    async_add_entities(entities)


class NationalRailCommuteBinarySensor(
    CoordinatorEntity[NationalRailDataUpdateCoordinator], BinarySensorEntity
):
    """Base binary sensor for My Rail Commute."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
        """
        super().__init__(coordinator)

        self._entry = entry
        self._attr_has_entity_name = True

        # Create device info
        commute_name = entry.data.get(CONF_COMMUTE_NAME, "My Rail Commute")
        origin = coordinator.origin
        destination = coordinator.destination

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{origin}_{destination}")},
            name=commute_name,
            manufacturer="National Rail",
            model="Live Departure Board",
            entry_type="service",
        )


class DisruptionSensor(NationalRailCommuteBinarySensor):
    """Binary sensor for any disruption detection.

    Simplified sensor for automation triggers:
    - ON: Any delays or cancellations (Status != Normal)
    - OFF: All trains on time (Status == Normal)
    """

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the disruption sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
        """
        super().__init__(coordinator, entry)

        self._attr_name = "Has Disruption"
        self._attr_unique_id = f"{entry.entry_id}_disruption"
        self._attr_translation_key = "disruption"
        # No device_class - removes "Problem" display in UI
        self._attr_icon = "mdi:alert-circle"

    @property
    def is_on(self) -> bool:
        """Return true if there is any disruption.

        Uses the unified overall_status from coordinator.

        Returns:
            True if any disruption (Status != Normal), False otherwise
        """
        if not self.coordinator.data:
            _LOGGER.debug("Disruption sensor: No coordinator data available")
            return False

        # Simple logic: ON if status is anything other than Normal
        overall_status = self.coordinator.data.get("overall_status", "Normal")
        is_disrupted = overall_status != "Normal"
        _LOGGER.debug(
            "Disruption sensor: status=%s, is_disrupted=%s",
            overall_status,
            is_disrupted,
        )
        return is_disrupted

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Provides detailed disruption information including the current status level.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data

        attributes = {
            "current_status": data.get("overall_status", "Normal"),
            ATTR_CANCELLED_COUNT: data.get("cancelled_count", 0),
            ATTR_DELAYED_COUNT: data.get("delayed_count", 0),
            ATTR_MAX_DELAY: data.get("max_delay_minutes", 0),
            ATTR_DISRUPTION_REASONS: data.get("disruption_reasons", []),
            "last_checked": data.get("last_updated"),
        }

        return attributes

    @property
    def icon(self) -> str:
        """Return icon based on disruption state.

        Returns:
            Icon string
        """
        if self.is_on:
            return "mdi:alert-circle"
        return "mdi:check-circle"
