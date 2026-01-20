"""Sensor platform for My Rail Commute integration."""
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
    CONF_NUM_SERVICES,
    DOMAIN,
    STATUS_CANCELLATIONS,
    STATUS_MAJOR_DELAYS,
    STATUS_MAJOR_DELAY_THRESHOLD,
    STATUS_MINOR_DELAYS,
    STATUS_MINOR_DELAY_THRESHOLD,
    STATUS_NORMAL,
)
from .coordinator import NationalRailDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up My Rail Commute sensor platform.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        async_add_entities: Callback to add entities
    """
    coordinator: NationalRailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Get number of trains to track from configuration (data or options)
    config = {**entry.data, **entry.options}
    num_trains = int(config.get(CONF_NUM_SERVICES, 3))

    # Create sensors
    entities: list[SensorEntity] = [
        CommuteSummarySensor(coordinator, entry),
        CommuteStatusSensor(coordinator, entry),
        NextTrainSensor(coordinator, entry),  # Mirrors train_1 for convenience
    ]

    # Create individual train sensors dynamically based on configuration
    for train_number in range(1, num_trains + 1):
        entities.append(TrainSensor(coordinator, entry, train_number))

    async_add_entities(entities)


class NationalRailCommuteEntity(CoordinatorEntity[NationalRailDataUpdateCoordinator]):
    """Base entity for My Rail Commute sensors."""

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

        self._attr_name = "Summary"
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
            Dictionary of attributes including all_trains for custom cards
        """
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        services = data.get("services", [])

        # Build all_trains attribute with complete train data for custom cards
        all_trains = []
        for idx, service in enumerate(services, start=1):
            train_data = {
                "train_number": idx,
                "scheduled_departure": service.get("scheduled_departure"),
                "expected_departure": service.get("expected_departure"),
                "platform": service.get("platform") or "TBA",
                "operator": service.get("operator"),
                "service_id": service.get("service_id"),
                "status": service.get("status"),
                "delay_minutes": service.get("delay_minutes", 0),
                "is_cancelled": service.get("is_cancelled", False),
                "calling_points": service.get("calling_points", []),
                "estimated_arrival": service.get("estimated_arrival"),
                "scheduled_arrival": service.get("scheduled_arrival"),
            }

            # Add optional fields if present
            if service.get("cancellation_reason"):
                train_data["cancellation_reason"] = service.get("cancellation_reason")
            if service.get("delay_reason"):
                train_data["delay_reason"] = service.get("delay_reason")

            all_trains.append(train_data)

        return {
            ATTR_ORIGIN: data.get("origin"),
            ATTR_ORIGIN_NAME: data.get("origin_name"),
            ATTR_DESTINATION: data.get("destination"),
            ATTR_DESTINATION_NAME: data.get("destination_name"),
            ATTR_TIME_WINDOW: data.get("time_window"),
            "services_requested": self.coordinator.num_services,
            ATTR_SERVICES_TRACKED: data.get("services_tracked"),
            ATTR_TOTAL_SERVICES: data.get("total_services_found"),
            ATTR_ON_TIME_COUNT: data.get("on_time_count"),
            ATTR_DELAYED_COUNT: data.get("delayed_count"),
            ATTR_CANCELLED_COUNT: data.get("cancelled_count"),
            "last_updated": data.get("last_updated"),
            "next_update": data.get("next_update"),
            "all_trains": all_trains,  # Complete train data for custom cards
        }


class CommuteStatusSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for overall commute status (Normal/Minor Delays/Major Delays/Cancellations)."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the commute status sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
        """
        super().__init__(coordinator, entry)

        self._attr_name = "Status"
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_icon = "mdi:train"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Status: Normal, Minor Delays, Major Delays, or Cancellations
        """
        if not self.coordinator.data:
            return None

        services = self.coordinator.data.get("services", [])

        if not services:
            return STATUS_NORMAL

        # Check for cancellations first (highest priority)
        cancelled_count = sum(1 for s in services if s.get("is_cancelled", False))
        if cancelled_count > 0:
            return STATUS_CANCELLATIONS

        # Check for major delays
        major_delays = sum(
            1 for s in services
            if s.get("delay_minutes", 0) >= STATUS_MAJOR_DELAY_THRESHOLD
        )
        if major_delays > 0:
            return STATUS_MAJOR_DELAYS

        # Check for minor delays
        minor_delays = sum(
            1 for s in services
            if s.get("delay_minutes", 0) >= STATUS_MINOR_DELAY_THRESHOLD
        )
        if minor_delays > 0:
            return STATUS_MINOR_DELAYS

        return STATUS_NORMAL

    @property
    def icon(self) -> str:
        """Return icon based on commute status.

        Returns:
            Icon string
        """
        status = self.native_value

        if status == STATUS_CANCELLATIONS:
            return "mdi:alert-circle"
        elif status == STATUS_MAJOR_DELAYS:
            return "mdi:clock-alert"
        elif status == STATUS_MINOR_DELAYS:
            return "mdi:train-variant"

        return "mdi:train"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        services = data.get("services", [])

        # Calculate statistics
        total_trains = len(services)
        cancelled_count = sum(1 for s in services if s.get("is_cancelled", False))
        major_delays = sum(
            1 for s in services
            if not s.get("is_cancelled", False)
            and s.get("delay_minutes", 0) >= STATUS_MAJOR_DELAY_THRESHOLD
        )
        minor_delays = sum(
            1 for s in services
            if not s.get("is_cancelled", False)
            and s.get("delay_minutes", 0) >= STATUS_MINOR_DELAY_THRESHOLD
            and s.get("delay_minutes", 0) < STATUS_MAJOR_DELAY_THRESHOLD
        )
        on_time = total_trains - cancelled_count - major_delays - minor_delays

        # Get max delay
        max_delay = 0
        if services:
            max_delay = max(
                (s.get("delay_minutes", 0) for s in services if not s.get("is_cancelled", False)),
                default=0
            )

        return {
            "total_trains": total_trains,
            "on_time_count": on_time,
            "minor_delays_count": minor_delays,
            "major_delays_count": major_delays,
            "cancelled_count": cancelled_count,
            "max_delay_minutes": max_delay,
            ATTR_ORIGIN: data.get("origin"),
            ATTR_ORIGIN_NAME: data.get("origin_name"),
            ATTR_DESTINATION: data.get("destination"),
            ATTR_DESTINATION_NAME: data.get("destination_name"),
            "last_updated": data.get("last_updated"),
        }


class TrainSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for individual train information."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        train_number: int,
    ) -> None:
        """Initialize the train sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            train_number: Position in departure list (1 = next train)
        """
        super().__init__(coordinator, entry)

        self._train_number = train_number
        self._attr_name = f"Train {train_number}"
        self._attr_unique_id = f"{entry.entry_id}_train_{train_number}"

        # Icon based on train number (next train gets special icon)
        if train_number == 1:
            self._attr_icon = "mdi:train-car"
        else:
            self._attr_icon = "mdi:train"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Departure status: "On Time", "Delayed", "Cancelled", "Expected", or "No service"
        """
        if not self.coordinator.data:
            return None

        services = self.coordinator.data.get("services", [])

        # Check if this train exists in the service list
        if len(services) < self._train_number:
            return "No service"

        train = services[self._train_number - 1]

        # Return departure status
        return self._get_departure_status(train)

    @property
    def icon(self) -> str:
        """Return icon based on train status.

        Returns:
            Icon string
        """
        if not self.coordinator.data:
            return "mdi:train"

        services = self.coordinator.data.get("services", [])

        if len(services) < self._train_number:
            return "mdi:train"

        train = services[self._train_number - 1]

        # Dynamic icon based on status
        if train.get("is_cancelled"):
            return "mdi:alert-circle"

        delay_minutes = train.get("delay_minutes", 0)
        if delay_minutes > 10:
            return "mdi:clock-alert"
        elif delay_minutes > 0:
            return "mdi:train-variant"

        return "mdi:train"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {
                "train_number": self._train_number,
                "status": "unavailable",
            }

        services = self.coordinator.data.get("services", [])

        # If this train doesn't exist, return minimal attributes
        if len(services) < self._train_number:
            return {
                "train_number": self._train_number,
                "total_trains": len(services),
                "status": "no_service",
            }

        train = services[self._train_number - 1]

        # Determine display time (expected or scheduled)
        expected = train.get("expected_departure")
        scheduled = train.get("scheduled_departure")
        departure_time = expected or scheduled

        # Build comprehensive attributes
        attributes = {
            "train_number": self._train_number,
            "total_trains": len(services),
            "departure_time": departure_time,  # Moved from state to attribute
            ATTR_SCHEDULED_DEPARTURE: train.get("scheduled_departure"),
            ATTR_EXPECTED_DEPARTURE: train.get("expected_departure"),
            ATTR_PLATFORM: train.get("platform") or "TBA",
            "platform_changed": False,  # TODO: Detect platform changes if API provides this
            ATTR_OPERATOR: train.get("operator"),
            ATTR_SERVICE_ID: train.get("service_id"),
            ATTR_STATUS: train.get("status"),
            ATTR_DELAY_MINUTES: train.get("delay_minutes", 0),
            ATTR_IS_CANCELLED: train.get("is_cancelled", False),
            ATTR_CALLING_POINTS: train.get("calling_points", []),
            ATTR_SCHEDULED_ARRIVAL: train.get("scheduled_arrival"),
            ATTR_ESTIMATED_ARRIVAL: train.get("estimated_arrival"),
            "last_updated": self.coordinator.data.get("last_updated"),
        }

        # Add cancellation reason if cancelled
        if train.get("is_cancelled"):
            attributes[ATTR_CANCELLATION_REASON] = train.get("cancellation_reason")
            attributes[ATTR_DELAY_REASON] = None
        # Add delay reason if delayed
        elif train.get("delay_minutes", 0) > 0:
            attributes[ATTR_DELAY_REASON] = train.get("delay_reason")
            attributes[ATTR_CANCELLATION_REASON] = None
        else:
            attributes[ATTR_CANCELLATION_REASON] = None
            attributes[ATTR_DELAY_REASON] = None

        return attributes

    def _get_departure_status(self, train: dict[str, Any]) -> str:
        """Get human-readable departure status.

        Args:
            train: Train data dictionary

        Returns:
            Status string like "On Time", "Delayed", "Cancelled"
        """
        if train.get("is_cancelled"):
            return "Cancelled"

        delay_minutes = train.get("delay_minutes", 0)
        if delay_minutes > 0:
            return "Delayed"

        expected = train.get("expected_departure")
        scheduled = train.get("scheduled_departure")

        if expected and expected != scheduled:
            return "Expected"

        return "On Time"


class NextTrainSensor(NationalRailCommuteEntity, SensorEntity):
    """Convenience sensor that mirrors train_1 (next departing train)."""

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
        """Return the state of the sensor (mirrors train_1).

        Returns:
            Departure status: "On Time", "Delayed", "Cancelled", "Expected", or "No service"
        """
        if not self.coordinator.data:
            return None

        services = self.coordinator.data.get("services", [])

        # If no trains at all, show "No service" instead of unavailable
        if not services:
            return "No service"

        # Get first train (same as train_1)
        train = services[0]

        # Return departure status
        return self._get_departure_status(train)

    @property
    def icon(self) -> str:
        """Return icon based on train status.

        Returns:
            Icon string
        """
        if not self.coordinator.data:
            return "mdi:train-car"

        services = self.coordinator.data.get("services", [])

        if not services:
            return "mdi:train-car"

        train = services[0]

        # Dynamic icon based on status (same as train_1)
        if train.get("is_cancelled"):
            return "mdi:alert-circle"

        delay_minutes = train.get("delay_minutes", 0)
        if delay_minutes > 10:
            return "mdi:clock-alert"
        elif delay_minutes > 0:
            return "mdi:train-variant"

        return "mdi:train-car"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes (mirrors train_1).

        Returns:
            Dictionary of attributes
        """
        if not self.coordinator.data:
            return {
                "status": "unavailable",
            }

        services = self.coordinator.data.get("services", [])

        # If no trains, return appropriate status
        if not services:
            return {
                "status": "no_service",
            }

        train = services[0]

        # Determine display time (expected or scheduled)
        expected = train.get("expected_departure")
        scheduled = train.get("scheduled_departure")
        departure_time = expected or scheduled

        # Build comprehensive attributes (same as train_1)
        attributes = {
            "train_number": 1,
            "total_trains": len(services),
            "departure_time": departure_time,  # Moved from state to attribute
            ATTR_SCHEDULED_DEPARTURE: train.get("scheduled_departure"),
            ATTR_EXPECTED_DEPARTURE: train.get("expected_departure"),
            ATTR_PLATFORM: train.get("platform") or "TBA",
            ATTR_OPERATOR: train.get("operator"),
            ATTR_SERVICE_ID: train.get("service_id"),
            ATTR_STATUS: train.get("status"),
            ATTR_DELAY_MINUTES: train.get("delay_minutes", 0),
            ATTR_IS_CANCELLED: train.get("is_cancelled", False),
            ATTR_CALLING_POINTS: train.get("calling_points", []),
            ATTR_SCHEDULED_ARRIVAL: train.get("scheduled_arrival"),
            ATTR_ESTIMATED_ARRIVAL: train.get("estimated_arrival"),
            "last_updated": self.coordinator.data.get("last_updated"),
        }

        # Add cancellation reason if cancelled
        if train.get("is_cancelled"):
            attributes[ATTR_CANCELLATION_REASON] = train.get("cancellation_reason")
            attributes[ATTR_DELAY_REASON] = None
        # Add delay reason if delayed
        elif train.get("delay_minutes", 0) > 0:
            attributes[ATTR_DELAY_REASON] = train.get("delay_reason")
            attributes[ATTR_CANCELLATION_REASON] = None
        else:
            attributes[ATTR_CANCELLATION_REASON] = None
            attributes[ATTR_DELAY_REASON] = None

        return attributes

    def _get_departure_status(self, train: dict[str, Any]) -> str:
        """Get human-readable departure status.

        Args:
            train: Train data dictionary

        Returns:
            Status string like "On Time", "Delayed", "Cancelled"
        """
        if train.get("is_cancelled"):
            return "Cancelled"

        delay_minutes = train.get("delay_minutes", 0)
        if delay_minutes > 0:
            return "Delayed"

        expected = train.get("expected_departure")
        scheduled = train.get("scheduled_departure")

        if expected and expected != scheduled:
            return "Expected"

        return "On Time"
