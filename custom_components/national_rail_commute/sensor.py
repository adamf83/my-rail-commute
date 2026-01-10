"""Sensor platform for National Rail Commute integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    ATTR_CALLING_POINTS,
    ATTR_CANCELLATION_REASON,
    ATTR_CANCELLED_COUNT,
    ATTR_DELAY_MINUTES,
    ATTR_DELAY_REASON,
    ATTR_DELAYED_COUNT,
    ATTR_DESTINATION,
    ATTR_DESTINATION_NAME,
    ATTR_ESTIMATED_ARRIVAL,
    ATTR_EXPECTED_DEPARTURE,
    ATTR_IS_CANCELLED,
    ATTR_ON_TIME_COUNT,
    ATTR_OPERATOR,
    ATTR_ORIGIN,
    ATTR_ORIGIN_NAME,
    ATTR_PLATFORM,
    ATTR_SCHEDULED_ARRIVAL,
    ATTR_SCHEDULED_DEPARTURE,
    ATTR_SERVICE_ID,
    ATTR_SERVICES_TRACKED,
    ATTR_STATUS,
    ATTR_TIME_WINDOW,
    ATTR_TOTAL_SERVICES,
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
    """Set up National Rail Commute sensor platform.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        async_add_entities: Callback to add entities
    """
    coordinator: NationalRailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Create sensors
    entities: list[SensorEntity] = [
        CommuteSummarySensor(coordinator, entry),
        NextTrainSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class NationalRailCommuteEntity(CoordinatorEntity[NationalRailDataUpdateCoordinator]):
    """Base entity for National Rail Commute sensors."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the entity.

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


class CommuteSummarySensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for commute summary."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the summary sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
        """
        super().__init__(coordinator, entry)

        commute_name = entry.data.get(CONF_COMMUTE_NAME, "Commute")
        self._attr_name = "Commute Summary"
        self._attr_unique_id = f"{entry.entry_id}_summary"
        self._attr_icon = "mdi:train"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Summary text or None if unavailable
        """
        if not self.coordinator.data:
            return None

        return self.coordinator.data.get("summary")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data

        return {
            ATTR_ORIGIN: data.get("origin"),
            ATTR_ORIGIN_NAME: data.get("origin_name"),
            ATTR_DESTINATION: data.get("destination"),
            ATTR_DESTINATION_NAME: data.get("destination_name"),
            ATTR_TIME_WINDOW: data.get("time_window"),
            ATTR_SERVICES_TRACKED: data.get("services_tracked"),
            ATTR_TOTAL_SERVICES: data.get("total_services_found"),
            ATTR_ON_TIME_COUNT: data.get("on_time_count"),
            ATTR_DELAYED_COUNT: data.get("delayed_count"),
            ATTR_CANCELLED_COUNT: data.get("cancelled_count"),
            "last_updated": data.get("last_updated"),
            "next_update": data.get("next_update"),
        }


class NextTrainSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for next train information."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the next train sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
        """
        super().__init__(coordinator, entry)

        self._attr_name = "Next Train"
        self._attr_unique_id = f"{entry.entry_id}_next_train"
        self._attr_icon = "mdi:train-car"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Departure time or status text
        """
        if not self.coordinator.data:
            return None

        next_train = self.coordinator.data.get("next_train")

        if not next_train:
            return "No trains found"

        # Return appropriate state based on status
        if next_train.get("is_cancelled"):
            return "Cancelled"

        expected = next_train.get("expected_departure")
        scheduled = next_train.get("scheduled_departure")

        if expected and expected != scheduled:
            delay = next_train.get("delay_minutes", 0)
            if delay > 0:
                return f"Delayed {delay} mins"

        return scheduled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {}

        next_train = self.coordinator.data.get("next_train")

        if not next_train:
            return {
                "status": "no_trains",
            }

        # Build comprehensive attributes
        attributes = {
            ATTR_SCHEDULED_DEPARTURE: next_train.get("scheduled_departure"),
            ATTR_EXPECTED_DEPARTURE: next_train.get("expected_departure"),
            ATTR_PLATFORM: next_train.get("platform") or "TBA",
            ATTR_OPERATOR: next_train.get("operator"),
            ATTR_SERVICE_ID: next_train.get("service_id"),
            ATTR_CALLING_POINTS: next_train.get("calling_points", []),
            ATTR_DELAY_MINUTES: next_train.get("delay_minutes", 0),
            ATTR_STATUS: next_train.get("status"),
            ATTR_IS_CANCELLED: next_train.get("is_cancelled", False),
            ATTR_SCHEDULED_ARRIVAL: next_train.get("scheduled_arrival"),
            ATTR_ESTIMATED_ARRIVAL: next_train.get("estimated_arrival"),
        }

        # Add cancellation reason if cancelled
        if next_train.get("is_cancelled"):
            attributes[ATTR_CANCELLATION_REASON] = next_train.get("cancellation_reason")
        # Add delay reason if delayed
        elif next_train.get("delay_minutes", 0) > 0:
            attributes[ATTR_DELAY_REASON] = next_train.get("delay_reason")

        return attributes
