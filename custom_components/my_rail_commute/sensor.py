"""Sensor platform for My Rail Commute integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_AVG_DELAY_7D,
    ATTR_AVG_DELAY_TODAY,
    ATTR_BEST_DAY,
    ATTR_CALLING_POINTS,
    ATTR_CANCELLATION_REASON,
    ATTR_CANCELLED_COUNT,
    ATTR_CANCELLED_COUNT_TODAY,
    ATTR_CATCHABLE,
    ATTR_CONNECTIONS,
    ATTR_DAILY_BREAKDOWN,
    ATTR_DELAY_MINUTES,
    ATTR_DELAY_REASON,
    ATTR_DELAYED_COUNT,
    ATTR_DELAYED_COUNT_TODAY,
    ATTR_DESTINATION,
    ATTR_DESTINATION_NAME,
    ATTR_ESTIMATED_ARRIVAL,
    ATTR_EXPECTED_DEPARTURE,
    ATTR_IS_CANCELLED,
    ATTR_IS_MULTI_LEG,
    ATTR_JOURNEY_FEASIBLE,
    ATTR_LEGS,
    ATTR_ON_TIME_COUNT,
    ATTR_ON_TIME_COUNT_TODAY,
    ATTR_ON_TIME_PCT_7D,
    ATTR_ON_TIME_PCT_30D,
    ATTR_ON_TIME_PCT_TODAY,
    ATTR_OPERATOR,
    ATTR_ORIGIN,
    ATTR_ORIGIN_NAME,
    ATTR_PLATFORM,
    ATTR_REVERSE_AVG_DELAY_7D,
    ATTR_REVERSE_BEST_DAY,
    ATTR_REVERSE_ON_TIME_PCT_7D,
    ATTR_REVERSE_ON_TIME_PCT_30D,
    ATTR_REVERSE_ON_TIME_PCT_TODAY,
    ATTR_REVERSE_WORST_DAY,
    ATTR_SCHEDULED_ARRIVAL,
    ATTR_SCHEDULED_DEPARTURE,
    ATTR_SERVICE_ID,
    ATTR_SERVICES_TRACKED,
    ATTR_STATUS,
    ATTR_TIME_WINDOW,
    ATTR_TOTAL_OBSERVATIONS_TODAY,
    ATTR_TOTAL_SERVICES,
    ATTR_WORST_DAY,
    CONF_COMMUTE_NAME,
    CONF_NUM_SERVICES,
    DOMAIN,
    STATUS_CONNECTION_DELAYED,
    STATUS_CONNECTION_MISSED,
    STATUS_CONNECTION_OK,
    STATUS_CONNECTION_TIGHT,
    STATUS_CRITICAL,
    STATUS_MAJOR_DELAYS,
    STATUS_MINOR_DELAYS,
    STATUS_NORMAL,
    STATUS_SEVERE_DISRUPTION,
)
from .coordinator import NationalRailDataUpdateCoordinator, build_route_id

_LOGGER = logging.getLogger(__name__)


def _get_departure_status(train: dict[str, Any]) -> str:
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


def _build_all_trains_attribute(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the all_trains attribute payload from a list of services.

    Args:
        services: List of service data dicts

    Returns:
        List of per-train dicts suitable for custom Lovelace cards
    """
    all_trains = []
    for idx, service in enumerate(services, start=1):
        train_data = {
            "train_number": idx,
            "scheduled_departure": service.get("scheduled_departure"),
            "expected_departure": service.get("expected_departure"),
            "platform": service.get("platform"),
            "operator": service.get("operator"),
            "service_id": service.get("service_id"),
            "status": service.get("status"),
            "delay_minutes": service.get("delay_minutes", 0),
            "is_cancelled": service.get("is_cancelled", False),
            "calling_points": service.get("calling_points", []),
            "estimated_arrival": service.get("estimated_arrival"),
            "scheduled_arrival": service.get("scheduled_arrival"),
            "destination": service.get("destination"),
            ATTR_CATCHABLE: service.get("catchable"),
        }

        # Add optional fields if present
        if service.get("cancellation_reason"):
            train_data["cancellation_reason"] = service.get("cancellation_reason")
        if service.get("delay_reason"):
            train_data["delay_reason"] = service.get("delay_reason")

        all_trains.append(train_data)

    return all_trains


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
    ]

    if coordinator.is_multi_leg:
        # Per-leg sensors replace the flat next-train/train-N range: each leg
        # has its own summary, status, next train, and tracked-train sensors
        for leg_index in range(1, len(coordinator.legs) + 1):
            entities.append(LegSummarySensor(coordinator, entry, leg_index))
            entities.append(LegStatusSensor(coordinator, entry, leg_index))
            entities.append(LegNextTrainSensor(coordinator, entry, leg_index))
            for train_number in range(1, num_trains + 1):
                entities.append(
                    LegTrainSensor(coordinator, entry, leg_index, train_number)
                )
        for connection_index in range(1, len(coordinator.legs)):
            entities.append(
                ConnectionStatusSensor(coordinator, entry, connection_index)
            )
    else:
        entities.append(
            NextTrainSensor(coordinator, entry)
        )  # Mirrors train_1 for convenience

        # Create individual train sensors dynamically based on configuration
        for train_number in range(1, num_trains + 1):
            entities.append(TrainSensor(coordinator, entry, train_number))

    # Historical performance sensors
    entities.append(HistoricalReliabilitySensor(coordinator, entry))
    entities.append(HistoricalDelaysSensor(coordinator, entry))

    _LOGGER.debug(
        "Setting up %d sensor entities for %s -> %s",
        len(entities),
        coordinator.origin,
        coordinator.destination or "ALL",
    )

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
        device_id = build_route_id(coordinator.legs)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
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
        all_trains = _build_all_trains_attribute(services)

        attrs: dict[str, Any] = {
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

        # Include historical stats so the card can read them directly from this
        # entity without needing a separate lookup — critical for route-toggle to
        # show the correct stats for the active direction.
        store = self.coordinator.stats_store
        if store is not None:
            today = store.get_today_stats()
            rolling_7 = store.get_rolling_stats(7)
            rolling_30 = store.get_rolling_stats(30)
            best_worst = store.get_best_and_worst_days(30)
            attrs[ATTR_ON_TIME_PCT_TODAY] = today.get("on_time_pct")
            attrs[ATTR_ON_TIME_PCT_7D] = rolling_7["on_time_pct"]
            attrs[ATTR_ON_TIME_PCT_30D] = rolling_30["on_time_pct"]
            attrs[ATTR_AVG_DELAY_7D] = rolling_7["avg_delay_minutes"]
            attrs[ATTR_WORST_DAY] = best_worst["worst_day"]
            attrs[ATTR_BEST_DAY] = best_worst["best_day"]
            # daily_breakdown omitted here (available on HistoricalReliabilitySensor)
            # to keep attribute payload within HA's 16 KB limit

        # Expose the paired reverse route's stats so that a card configured with
        # only this entity still has access to both directions' stats when toggled.
        # Without this the card shows the forward stats for both directions because
        # it reads from a fixed entity reference rather than switching on toggle.
        if self.hass is not None and DOMAIN in self.hass.data:
            rev_coordinator = next(
                (
                    c
                    for c in self.hass.data[DOMAIN].values()
                    if (
                        isinstance(c, NationalRailDataUpdateCoordinator)
                        and c is not self.coordinator
                        and c.origin == self.coordinator.destination
                        and c.destination == self.coordinator.origin
                    )
                ),
                None,
            )
            if rev_coordinator is not None and rev_coordinator.stats_store is not None:
                rev_store = rev_coordinator.stats_store
                rev_today = rev_store.get_today_stats()
                rev_7 = rev_store.get_rolling_stats(7)
                rev_30 = rev_store.get_rolling_stats(30)
                rev_bw = rev_store.get_best_and_worst_days(30)
                attrs[ATTR_REVERSE_ON_TIME_PCT_TODAY] = rev_today.get("on_time_pct")
                attrs[ATTR_REVERSE_ON_TIME_PCT_7D] = rev_7["on_time_pct"]
                attrs[ATTR_REVERSE_ON_TIME_PCT_30D] = rev_30["on_time_pct"]
                attrs[ATTR_REVERSE_AVG_DELAY_7D] = rev_7["avg_delay_minutes"]
                attrs[ATTR_REVERSE_WORST_DAY] = rev_bw["worst_day"]
                attrs[ATTR_REVERSE_BEST_DAY] = rev_bw["best_day"]

        if data.get("multi_destination"):
            attrs["multi_destination"] = True
            attrs["services_by_destination"] = data.get("services_by_destination", {})

        if data.get("is_multi_leg"):
            attrs[ATTR_IS_MULTI_LEG] = True
            attrs[ATTR_LEGS] = data.get("legs", [])
            attrs[ATTR_CONNECTIONS] = data.get("connections", [])
            attrs[ATTR_JOURNEY_FEASIBLE] = data.get("journey_feasible")

        return attrs


class CommuteStatusSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for overall commute status.

    Shows unified status with 5 levels:
    - Normal: All trains on time
    - Minor Delays: Delays 1-9 minutes
    - Major Delays: Delays ≥10 minutes
    - Severe Disruption: Meets user's configurable disruption thresholds
    - Critical: Any cancellations
    """

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

    def _get_route_data(self) -> dict[str, Any] | None:
        """Return the dict this sensor reports on (whole journey by default).

        Overridden by leg-scoped subclasses to read from a single leg's
        parsed data instead of the whole journey's.

        Returns:
            The route data dict, or None if unavailable
        """
        return self.coordinator.data

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Uses the unified status calculation from the coordinator.

        Returns:
            Status: Normal, Minor Delays, Major Delays, Severe Disruption, or Critical
        """
        data = self._get_route_data()
        if not data:
            return None

        # Use the unified status from coordinator (single source of truth)
        return data.get("overall_status", STATUS_NORMAL)

    @property
    def icon(self) -> str:
        """Return icon based on commute status.

        Icon progression from least to most severe:
        - Normal: train (blue)
        - Minor Delays: train-variant (yellow)
        - Major Delays: clock-alert (orange)
        - Severe Disruption: alert-circle (red)
        - Critical: alert-octagon (red)

        Returns:
            Icon string
        """
        status = self.native_value

        if status == STATUS_CRITICAL:
            return "mdi:alert-octagon"
        elif status == STATUS_SEVERE_DISRUPTION:
            return "mdi:alert-circle"
        elif status == STATUS_MAJOR_DELAYS:
            return "mdi:clock-alert"
        elif status == STATUS_MINOR_DELAYS:
            return "mdi:train-variant"

        return "mdi:train"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Provides detailed breakdown of service counts and status information.

        Returns:
            Dictionary of attributes
        """
        data = self._get_route_data()
        if not data:
            return {}

        services = data.get("services", [])

        # Calculate statistics
        total_trains = len(services)
        cancelled_count = sum(1 for s in services if s.get("is_cancelled", False))
        major_delays = sum(
            1
            for s in services
            if not s.get("is_cancelled", False)
            and s.get("delay_minutes", 0) >= self.coordinator.major_delay_threshold
        )
        minor_delays = sum(
            1
            for s in services
            if not s.get("is_cancelled", False)
            and s.get("delay_minutes", 0) >= self.coordinator.minor_delay_threshold
            and s.get("delay_minutes", 0) < self.coordinator.major_delay_threshold
        )
        on_time = total_trains - cancelled_count - major_delays - minor_delays

        # Get max delay
        max_delay = 0
        if services:
            max_delay = max(
                (
                    s.get("delay_minutes", 0)
                    for s in services
                    if not s.get("is_cancelled", False)
                ),
                default=0,
            )

        return {
            "total_trains": total_trains,
            "on_time_count": on_time,
            "minor_delays_count": minor_delays,
            "major_delays_count": major_delays,
            "cancelled_count": cancelled_count,
            "max_delay_minutes": max_delay,
            "disruption_threshold_met": data.get("overall_status", STATUS_NORMAL)
            != STATUS_NORMAL,
            ATTR_ORIGIN: data.get("origin"),
            ATTR_ORIGIN_NAME: data.get("origin_name"),
            ATTR_DESTINATION: data.get("destination"),
            ATTR_DESTINATION_NAME: data.get("destination_name"),
            "last_updated": (self.coordinator.data or {}).get("last_updated"),
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

        # Platform change tracking
        self._previous_platform: str | None = None
        self._platform_changed: bool = False
        self._current_service_id: str | None = None

        # Icon based on train number (next train gets special icon)
        if train_number == 1:
            self._attr_icon = "mdi:train-car"
        else:
            self._attr_icon = "mdi:train"

    def _get_services(self) -> list[dict[str, Any]]:
        """Return the list of services this sensor tracks.

        Overridden by leg-scoped subclasses to read from a single leg's
        service list instead of the whole journey's.

        Returns:
            List of service data dicts
        """
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("services", [])

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator and detect platform changes."""
        if not self.coordinator.data:
            _LOGGER.debug("Train %d: No coordinator data available", self._train_number)
            super()._handle_coordinator_update()
            return

        services = self._get_services()
        _LOGGER.debug(
            "Train %d: Processing update with %d services available",
            self._train_number,
            len(services),
        )

        # Check if this train exists in the service list
        if len(services) >= self._train_number:
            train = services[self._train_number - 1]
            current_platform = train.get("platform") or ""
            current_service_id = train.get("service_id")

            # Validate service_id is not empty/None before tracking
            # Empty or None service_id cannot be reliably used for platform change detection
            if not current_service_id or (
                isinstance(current_service_id, str) and not current_service_id.strip()
            ):
                # Invalid service_id - reset tracking and skip platform change detection
                _LOGGER.debug(
                    "Train %d: Invalid service_id (empty/None), skipping platform tracking",
                    self._train_number,
                )
                self._platform_changed = False
                self._previous_platform = None
                self._current_service_id = None
            elif (
                self._current_service_id
                and current_service_id == self._current_service_id
            ):
                # Same service - check for platform change
                if self._previous_platform != current_platform:
                    if self._previous_platform is not None:
                        # Platform has changed!
                        _LOGGER.info(
                            "Platform changed for train %d (service %s): %s -> %s",
                            self._train_number,
                            current_service_id,
                            self._previous_platform,
                            current_platform,
                        )
                        self._platform_changed = True
                        # Keep the previous platform stored (don't update it)
                    else:
                        # First time seeing this platform for this service
                        self._previous_platform = current_platform
                        self._platform_changed = False
                else:
                    # Platform hasn't changed
                    self._platform_changed = False
            else:
                # Different service or first time - reset tracking
                self._platform_changed = False
                self._previous_platform = current_platform
                self._current_service_id = current_service_id
        else:
            # Train doesn't exist anymore - reset tracking
            self._previous_platform = None
            self._platform_changed = False
            self._current_service_id = None

        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Departure status: "On Time", "Delayed", "Cancelled", "Expected", or "No service"
        """
        if not self.coordinator.data:
            return None

        services = self._get_services()

        # Check if this train exists in the service list
        if len(services) < self._train_number:
            return "No service"

        train = services[self._train_number - 1]

        # Return departure status
        return _get_departure_status(train)

    @property
    def icon(self) -> str:
        """Return icon based on train status.

        Returns:
            Icon string
        """
        if not self.coordinator.data:
            return "mdi:train"

        services = self._get_services()

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

        services = self._get_services()

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
            ATTR_PLATFORM: train.get("platform"),
            "platform_changed": self._platform_changed,
            "previous_platform": self._previous_platform
            if self._platform_changed
            else None,
            ATTR_OPERATOR: train.get("operator"),
            ATTR_SERVICE_ID: train.get("service_id"),
            ATTR_STATUS: train.get("status"),
            ATTR_DELAY_MINUTES: train.get("delay_minutes", 0),
            ATTR_IS_CANCELLED: train.get("is_cancelled", False),
            ATTR_CALLING_POINTS: train.get("calling_points", []),
            ATTR_SCHEDULED_ARRIVAL: train.get("scheduled_arrival"),
            ATTR_ESTIMATED_ARRIVAL: train.get("estimated_arrival"),
            ATTR_CATCHABLE: train.get("catchable"),
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

        # Platform change tracking (mirrors train_1)
        self._previous_platform: str | None = None
        self._platform_changed: bool = False
        self._current_service_id: str | None = None

    def _get_services(self) -> list[dict[str, Any]]:
        """Return the list of services this sensor tracks.

        Overridden by leg-scoped subclasses to read from a single leg's
        service list instead of the whole journey's.

        Returns:
            List of service data dicts
        """
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("services", [])

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator and detect platform changes."""
        if not self.coordinator.data:
            super()._handle_coordinator_update()
            return

        services = self._get_services()

        # Check if next train exists
        if services:
            train = services[0]
            current_platform = train.get("platform") or ""
            current_service_id = train.get("service_id")

            # Validate service_id is not empty/None before tracking
            # Empty or None service_id cannot be reliably used for platform change detection
            if not current_service_id or (
                isinstance(current_service_id, str) and not current_service_id.strip()
            ):
                # Invalid service_id - reset tracking and skip platform change detection
                _LOGGER.debug(
                    "Next train: Invalid service_id (empty/None), skipping platform tracking",
                )
                self._platform_changed = False
                self._previous_platform = None
                self._current_service_id = None
            elif (
                self._current_service_id
                and current_service_id == self._current_service_id
            ):
                # Same service - check for platform change
                if self._previous_platform != current_platform:
                    if self._previous_platform is not None:
                        # Platform has changed!
                        _LOGGER.info(
                            "Platform changed for next train (service %s): %s -> %s",
                            current_service_id,
                            self._previous_platform,
                            current_platform,
                        )
                        self._platform_changed = True
                        # Keep the previous platform stored (don't update it)
                    else:
                        # First time seeing this platform for this service
                        self._previous_platform = current_platform
                        self._platform_changed = False
                else:
                    # Platform hasn't changed
                    self._platform_changed = False
            else:
                # Different service or first time - reset tracking
                self._platform_changed = False
                self._previous_platform = current_platform
                self._current_service_id = current_service_id
        else:
            # Train doesn't exist anymore - reset tracking
            self._previous_platform = None
            self._platform_changed = False
            self._current_service_id = None

        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor (mirrors train_1).

        Returns:
            Departure status: "On Time", "Delayed", "Cancelled", "Expected", or "No service"
        """
        if not self.coordinator.data:
            return None

        services = self._get_services()

        # If no trains at all, show "No service" instead of unavailable
        if not services:
            return "No service"

        # Get first train (same as train_1)
        train = services[0]

        # Return departure status
        return _get_departure_status(train)

    @property
    def icon(self) -> str:
        """Return icon based on train status.

        Returns:
            Icon string
        """
        if not self.coordinator.data:
            return "mdi:train-car"

        services = self._get_services()

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

        services = self._get_services()

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
            ATTR_PLATFORM: train.get("platform"),
            "platform_changed": self._platform_changed,
            "previous_platform": self._previous_platform
            if self._platform_changed
            else None,
            ATTR_OPERATOR: train.get("operator"),
            ATTR_SERVICE_ID: train.get("service_id"),
            ATTR_STATUS: train.get("status"),
            ATTR_DELAY_MINUTES: train.get("delay_minutes", 0),
            ATTR_IS_CANCELLED: train.get("is_cancelled", False),
            ATTR_CALLING_POINTS: train.get("calling_points", []),
            ATTR_SCHEDULED_ARRIVAL: train.get("scheduled_arrival"),
            ATTR_ESTIMATED_ARRIVAL: train.get("estimated_arrival"),
            ATTR_CATCHABLE: train.get("catchable"),
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


class LegSummarySensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for a single leg's summary within a multi-leg journey."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        leg_index: int,
    ) -> None:
        """Initialize the leg summary sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            leg_index: 1-indexed position of this leg in the journey
        """
        super().__init__(coordinator, entry)

        self._leg_index = leg_index
        self._attr_name = f"Leg {leg_index} Summary"
        self._attr_unique_id = f"{entry.entry_id}_leg{leg_index}_summary"
        self._attr_icon = "mdi:train"

    def _get_leg_data(self) -> dict[str, Any] | None:
        """Return this sensor's leg's parsed data, or None if unavailable."""
        if not self.coordinator.data:
            return None
        legs = self.coordinator.data.get("legs", [])
        if len(legs) < self._leg_index:
            return None
        return legs[self._leg_index - 1]

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Summary text or None if unavailable
        """
        leg_data = self._get_leg_data()
        if not leg_data:
            return None
        return leg_data.get("summary")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes including all_trains for custom cards
        """
        leg_data = self._get_leg_data()
        if not leg_data:
            return {}

        services = leg_data.get("services", [])

        return {
            ATTR_ORIGIN: leg_data.get("origin"),
            ATTR_ORIGIN_NAME: leg_data.get("origin_name"),
            ATTR_DESTINATION: leg_data.get("destination"),
            ATTR_DESTINATION_NAME: leg_data.get("destination_name"),
            ATTR_SERVICES_TRACKED: leg_data.get("services_tracked"),
            ATTR_TOTAL_SERVICES: leg_data.get("total_services_found"),
            ATTR_ON_TIME_COUNT: leg_data.get("on_time_count"),
            ATTR_DELAYED_COUNT: leg_data.get("delayed_count"),
            ATTR_CANCELLED_COUNT: leg_data.get("cancelled_count"),
            "all_trains": _build_all_trains_attribute(services),
            "last_updated": (self.coordinator.data or {}).get("last_updated"),
        }


class LegStatusSensor(CommuteStatusSensor):
    """Sensor for a single leg's status within a multi-leg journey."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        leg_index: int,
    ) -> None:
        """Initialize the leg status sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            leg_index: 1-indexed position of this leg in the journey
        """
        self._leg_index = leg_index
        super().__init__(coordinator, entry)

        self._attr_name = f"Leg {leg_index} Status"
        self._attr_unique_id = f"{entry.entry_id}_leg{leg_index}_status"

    def _get_route_data(self) -> dict[str, Any] | None:
        """Return this sensor's leg's parsed data, or None if unavailable."""
        if not self.coordinator.data:
            return None
        legs = self.coordinator.data.get("legs", [])
        if len(legs) < self._leg_index:
            return None
        return legs[self._leg_index - 1]


class ConnectionStatusSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor for whether a change between two legs is achievable.

    One entity per interchange in a multi-leg journey, comparing the
    incoming leg's expected arrival against the outgoing leg's departures.
    """

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        connection_index: int,
    ) -> None:
        """Initialize the connection status sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            connection_index: 1-indexed position of this connection (the
                change between leg `connection_index` and the next leg)
        """
        super().__init__(coordinator, entry)

        self._connection_index = connection_index
        self._attr_name = f"Connection {connection_index} Status"
        self._attr_unique_id = f"{entry.entry_id}_connection{connection_index}_status"

    def _get_connection_data(self) -> dict[str, Any] | None:
        """Return this sensor's connection data, or None if unavailable."""
        if not self.coordinator.data:
            return None
        connections = self.coordinator.data.get("connections", [])
        if len(connections) < self._connection_index:
            return None
        return connections[self._connection_index - 1]

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor.

        Returns:
            Connection status: Connection OK, Tight Connection, Delayed
            Connection, Missed Connection, or Unknown
        """
        connection = self._get_connection_data()
        if not connection:
            return None
        return connection.get("status")

    @property
    def icon(self) -> str:
        """Return icon based on connection status.

        Returns:
            Icon string
        """
        status = self.native_value

        if status == STATUS_CONNECTION_MISSED:
            return "mdi:alert-octagon"
        if status == STATUS_CONNECTION_DELAYED:
            return "mdi:clock-alert"
        if status == STATUS_CONNECTION_TIGHT:
            return "mdi:clock-alert-outline"
        if status == STATUS_CONNECTION_OK:
            return "mdi:transit-connection-variant"

        return "mdi:help-circle-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Returns:
            Dictionary of attributes describing the connection
        """
        connection = self._get_connection_data()
        if not connection:
            return {}

        attrs = dict(connection)
        attrs["last_updated"] = (self.coordinator.data or {}).get("last_updated")
        return attrs


class LegTrainSensor(TrainSensor):
    """Sensor for individual train information within one leg of a multi-leg journey."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        leg_index: int,
        train_number: int,
    ) -> None:
        """Initialize the leg train sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            leg_index: 1-indexed position of this leg in the journey
            train_number: Position in this leg's departure list (1 = next train)
        """
        self._leg_index = leg_index
        super().__init__(coordinator, entry, train_number)

        self._attr_name = f"Leg {leg_index} Train {train_number}"
        self._attr_unique_id = f"{entry.entry_id}_leg{leg_index}_train_{train_number}"

    def _get_services(self) -> list[dict[str, Any]]:
        """Return this leg's list of services, or an empty list if unavailable."""
        if not self.coordinator.data:
            return []
        legs = self.coordinator.data.get("legs", [])
        if len(legs) < self._leg_index:
            return []
        return legs[self._leg_index - 1].get("services", [])


class LegNextTrainSensor(NextTrainSensor):
    """Convenience sensor that mirrors leg_train_1 for one leg of a multi-leg journey."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
        leg_index: int,
    ) -> None:
        """Initialize the leg next-train sensor.

        Args:
            coordinator: Data coordinator
            entry: Config entry
            leg_index: 1-indexed position of this leg in the journey
        """
        self._leg_index = leg_index
        super().__init__(coordinator, entry)

        self._attr_name = f"Leg {leg_index} Next Train"
        self._attr_unique_id = f"{entry.entry_id}_leg{leg_index}_next_train"

    def _get_services(self) -> list[dict[str, Any]]:
        """Return this leg's list of services, or an empty list if unavailable."""
        if not self.coordinator.data:
            return []
        legs = self.coordinator.data.get("legs", [])
        if len(legs) < self._leg_index:
            return []
        return legs[self._leg_index - 1].get("services", [])


class HistoricalReliabilitySensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor exposing on-time percentage over rolling windows."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Historical Reliability"
        self._attr_unique_id = f"{entry.entry_id}_historical_reliability"
        self._attr_icon = "mdi:chart-line"
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        if self.coordinator.stats_store is None:
            return None
        return self.coordinator.stats_store.get_rolling_stats(7)["on_time_pct"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        store = self.coordinator.stats_store
        if store is None:
            return {}

        today = store.get_today_stats()
        rolling_7 = store.get_rolling_stats(7)
        rolling_30 = store.get_rolling_stats(30)

        return {
            ATTR_ON_TIME_PCT_TODAY: today.get("on_time_pct"),
            ATTR_ON_TIME_PCT_7D: rolling_7["on_time_pct"],
            ATTR_ON_TIME_PCT_30D: rolling_30["on_time_pct"],
            ATTR_ON_TIME_COUNT_TODAY: today.get("on_time_count", 0),
            ATTR_DELAYED_COUNT_TODAY: today.get("delayed_count", 0),
            ATTR_CANCELLED_COUNT_TODAY: today.get("cancelled_count", 0),
            ATTR_TOTAL_OBSERVATIONS_TODAY: today.get("total_observations", 0),
            "days_with_data_7day": rolling_7["days_with_data"],
            "days_with_data_30day": rolling_30["days_with_data"],
            ATTR_DAILY_BREAKDOWN: store.get_daily_breakdown(30),
        }


class HistoricalDelaysSensor(NationalRailCommuteEntity, SensorEntity):
    """Sensor exposing average delay statistics over rolling windows."""

    def __init__(
        self,
        coordinator: NationalRailDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = "Historical Delays"
        self._attr_unique_id = f"{entry.entry_id}_historical_delays"
        self._attr_icon = "mdi:clock-alert-outline"
        self._attr_native_unit_of_measurement = "min"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        if self.coordinator.stats_store is None:
            return None
        return self.coordinator.stats_store.get_rolling_stats(7)["avg_delay_minutes"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        store = self.coordinator.stats_store
        if store is None:
            return {}

        today = store.get_today_stats()
        rolling_7 = store.get_rolling_stats(7)
        best_worst = store.get_best_and_worst_days(30)

        return {
            ATTR_AVG_DELAY_TODAY: today.get("avg_delay_minutes"),
            ATTR_AVG_DELAY_7D: rolling_7["avg_delay_minutes"],
            ATTR_WORST_DAY: best_worst["worst_day"],
            ATTR_BEST_DAY: best_worst["best_day"],
            "days_with_data_7day": rolling_7["days_with_data"],
        }
