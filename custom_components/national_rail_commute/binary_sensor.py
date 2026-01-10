"""Binary sensor platform for National Rail Commute integration."""
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
    ATTR_AFFECTED_SERVICES,
    ATTR_CANCELLED_COUNT,
    ATTR_DELAYED_COUNT,
    ATTR_DISRUPTION_REASONS,
    ATTR_DISRUPTION_TYPE,
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
    """Set up National Rail Commute binary sensor platform.

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

    async_add_entities(entities)


class NationalRailCommuteBinarySensor(
    CoordinatorEntity[NationalRailDataUpdateCoordinator], BinarySensorEntity
):
    """Base binary sensor for National Rail Commute."""

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
        commute_name = entry.data.get(CONF_COMMUTE_NAME, "National Rail Commute")
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
    """Binary sensor for severe disruption detection."""

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

        self._attr_name = "Severe Disruption"
        self._attr_unique_id = f"{entry.entry_id}_disruption"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:alert-circle"

    @property
    def is_on(self) -> bool:
        """Return true if there is severe disruption.

        Returns:
            True if disruption detected, False otherwise
        """
        if not self.coordinator.data:
            return False

        disruption = self.coordinator.data.get("disruption", {})
        return disruption.get("has_disruption", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {}

        disruption = self.coordinator.data.get("disruption", {})

        attributes = {
            ATTR_DISRUPTION_TYPE: disruption.get("disruption_type"),
            ATTR_AFFECTED_SERVICES: disruption.get("affected_services", 0),
            ATTR_CANCELLED_COUNT: disruption.get("cancelled_services", 0),
            ATTR_DELAYED_COUNT: disruption.get("delayed_services", 0),
            ATTR_MAX_DELAY: disruption.get("max_delay_minutes", 0),
            ATTR_DISRUPTION_REASONS: disruption.get("disruption_reasons", []),
            "last_checked": self.coordinator.data.get("last_updated"),
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
