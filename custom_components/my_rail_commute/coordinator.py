"""Data update coordinator for My Rail Commute integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NationalRailAPI, NationalRailAPIError
from .const import (
    CONF_DESTINATION,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    CONF_NIGHT_UPDATES,
    DISRUPTION_DELAY_THRESHOLD_MULTIPLE,
    DISRUPTION_DELAY_THRESHOLD_SINGLE,
    DISRUPTION_MULTIPLE_SERVICES,
    DOMAIN,
    NIGHT_HOURS,
    PEAK_HOURS,
    STATUS_CANCELLED,
    STATUS_DELAYED,
    STATUS_ON_TIME,
    UPDATE_INTERVAL_NIGHT,
    UPDATE_INTERVAL_OFF_PEAK,
    UPDATE_INTERVAL_PEAK,
)

_LOGGER = logging.getLogger(__name__)


class NationalRailDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Rail data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: NationalRailAPI,
        config: dict[str, Any],
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            api: Rail API client
            config: Configuration dictionary
        """
        self.api = api
        self.config = config
        self._failed_updates = 0
        self._max_failed_updates = 3

        # Get configuration
        self.origin = config[CONF_ORIGIN]
        self.destination = config[CONF_DESTINATION]
        self.time_window = int(config[CONF_TIME_WINDOW])
        self.num_services = int(config[CONF_NUM_SERVICES])
        self.night_updates_enabled = config.get(CONF_NIGHT_UPDATES, False)

        # Station names (will be populated on first update)
        self.origin_name: str | None = None
        self.destination_name: str | None = None

        # Initialize with off-peak interval
        update_interval = self._get_update_interval()

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    def _get_update_interval(self) -> timedelta:
        """Get update interval based on current time.

        Returns:
            Update interval timedelta
        """
        now = dt_util.now()
        current_hour = now.hour

        # Check if in night time
        night_start, night_end = NIGHT_HOURS
        if night_start <= current_hour or current_hour < night_end:
            if not self.night_updates_enabled:
                # Use a moderate interval so coordinator can reschedule when morning comes
                # This ensures manual refresh works and automatic updates resume at dawn
                _LOGGER.debug("Using longer interval during night time (manual refresh still works)")
                return timedelta(hours=1)
            return UPDATE_INTERVAL_NIGHT

        # Check if in peak hours
        for peak_start, peak_end in PEAK_HOURS:
            if peak_start <= current_hour < peak_end:
                return UPDATE_INTERVAL_PEAK

        # Off-peak hours
        return UPDATE_INTERVAL_OFF_PEAK

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Rail API.

        Returns:
            Parsed data dictionary

        Raises:
            UpdateFailed: If update fails
        """
        # Update interval may have changed (e.g., switching between peak/off-peak/night)
        new_interval = self._get_update_interval()
        if new_interval != self.update_interval:
            _LOGGER.debug("Updating interval from %s to %s", self.update_interval, new_interval)
            self.update_interval = new_interval

        try:
            _LOGGER.debug(
                "Fetching departure data for %s -> %s",
                self.origin,
                self.destination,
            )

            # Fetch departure board
            data = await self.api.get_departure_board(
                self.origin,
                self.destination,
                self.time_window,
                self.num_services,
            )

            # Store station names
            self.origin_name = data.get("location_name", self.origin)
            self.destination_name = data.get("destination_name", self.destination)

            # Parse and enrich data
            parsed_data = self._parse_data(data)

            # Reset failed update counter on success
            self._failed_updates = 0

            return parsed_data

        except NationalRailAPIError as err:
            self._failed_updates += 1
            _LOGGER.error("Error fetching data: %s (attempt %s/%s)",
                         err, self._failed_updates, self._max_failed_updates)

            # If we've failed too many times, raise UpdateFailed
            if self._failed_updates >= self._max_failed_updates:
                raise UpdateFailed(f"Failed to fetch data: {err}") from err

            # Check if cached data is too old (more than 2 hours)
            if self.data and self.data.get("last_updated"):
                try:
                    last_updated = dt_util.parse_datetime(self.data["last_updated"])
                    if last_updated:
                        age = dt_util.now() - last_updated
                        if age > timedelta(hours=2):
                            _LOGGER.warning(
                                "Cached data is too old (%s hours), not returning stale data",
                                age.total_seconds() / 3600
                            )
                            raise UpdateFailed(f"Failed to fetch data and cached data too old: {err}") from err
                except (ValueError, TypeError):
                    pass

            # Otherwise, return last known data if available and recent
            if self.data:
                _LOGGER.warning("Using last known data after failed update (data age: recent)")
                return self.data

            raise UpdateFailed(f"Failed to fetch data: {err}") from err

    def _filter_departed_trains(self, services: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out trains that have already departed.

        Args:
            services: List of service data

        Returns:
            Filtered list containing only trains that haven't departed yet
        """
        if not services:
            return services

        now = dt_util.now()
        current_time_str = now.strftime("%H:%M")
        filtered_services = []

        for service in services:
            # Skip cancelled trains - they should be shown regardless of time
            if service.get("is_cancelled", False):
                filtered_services.append(service)
                continue

            # Get departure time (prefer expected, fallback to scheduled)
            departure_time = service.get("expected_departure") or service.get("scheduled_departure")

            if not departure_time or ":" not in departure_time:
                # If we can't parse the time, keep the service
                filtered_services.append(service)
                continue

            try:
                # Parse current time and departure time using a reference date
                current_dt = datetime.strptime(f"2000-01-01 {current_time_str}", "%Y-%m-%d %H:%M")
                departure_dt = datetime.strptime(f"2000-01-01 {departure_time}", "%Y-%m-%d %H:%M")

                # Calculate time difference
                time_diff_seconds = (departure_dt - current_dt).total_seconds()

                # Handle midnight crossing: if difference > 12 hours in either direction,
                # adjust for day boundary
                if time_diff_seconds < -12 * 3600:
                    # Departure is much earlier in the day, so it's actually tomorrow
                    departure_dt += timedelta(days=1)
                    time_diff_seconds = (departure_dt - current_dt).total_seconds()
                elif time_diff_seconds > 12 * 3600:
                    # Departure is much later in the day, so it's actually yesterday
                    departure_dt -= timedelta(days=1)
                    time_diff_seconds = (departure_dt - current_dt).total_seconds()

                # Keep the train if it hasn't departed yet
                # Add a 2-minute grace period to account for update delays
                if time_diff_seconds >= -120:  # -2 minutes
                    filtered_services.append(service)
                else:
                    _LOGGER.debug(
                        "Filtering out departed train: scheduled %s, expected %s, current time %s",
                        service.get("scheduled_departure"),
                        service.get("expected_departure"),
                        current_time_str,
                    )

            except (ValueError, TypeError) as err:
                # If we can't parse the time, keep the service to be safe
                _LOGGER.debug("Could not parse departure time for filtering: %s", err)
                filtered_services.append(service)

        return filtered_services

    def _parse_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Parse and enrich API data.

        Args:
            data: Raw API data

        Returns:
            Parsed data with additional calculated fields
        """
        services = data.get("services", [])

        # Limit to configured number of services
        services = services[: self.num_services]

        # Filter out trains that have already departed
        services = self._filter_departed_trains(services)

        # Calculate statistics
        on_time_count = sum(
            1 for s in services if s.get("status") == STATUS_ON_TIME
        )
        delayed_count = sum(
            1 for s in services if s.get("status") == STATUS_DELAYED
        )
        cancelled_count = sum(
            1 for s in services if s.get("status") == STATUS_CANCELLED
        )

        # Determine disruption status
        disruption_data = self._calculate_disruption(services)

        # Build summary
        summary = self._build_summary(on_time_count, delayed_count, cancelled_count)

        # Get next train (first non-cancelled service)
        next_train = None
        for service in services:
            if not service.get("is_cancelled", False):
                next_train = service
                break

        return {
            "origin": self.origin,
            "origin_name": self.origin_name or self.origin,
            "destination": self.destination,
            "destination_name": self.destination_name or self.destination,
            "time_window": self.time_window,
            "services_tracked": len(services),
            "total_services_found": len(data.get("services", [])),
            "services": services,
            "on_time_count": on_time_count,
            "delayed_count": delayed_count,
            "cancelled_count": cancelled_count,
            "next_train": next_train,
            "disruption": disruption_data,
            "summary": summary,
            "last_updated": dt_util.now().isoformat(),
            "next_update": (dt_util.now() + self.update_interval).isoformat(),
            "nrcc_messages": data.get("nrcc_messages", []),
        }

    def _calculate_disruption(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate if there is severe disruption.

        Args:
            services: List of service data

        Returns:
            Disruption data dictionary
        """
        has_disruption = False
        disruption_type = None
        affected_services = 0
        cancelled_services = 0
        delayed_services = 0
        max_delay = 0
        disruption_reasons = []

        for service in services:
            is_cancelled = service.get("is_cancelled", False)
            delay_minutes = service.get("delay_minutes", 0)

            # Check for cancellations
            if is_cancelled:
                has_disruption = True
                disruption_type = "cancellation"
                cancelled_services += 1
                affected_services += 1

                # Add cancellation reason
                reason = service.get("cancellation_reason")
                if reason and reason not in disruption_reasons:
                    disruption_reasons.append(reason)

            # Check for significant delays
            elif delay_minutes >= DISRUPTION_DELAY_THRESHOLD_SINGLE:
                has_disruption = True
                if disruption_type != "cancellation":
                    disruption_type = "delay"
                delayed_services += 1
                affected_services += 1
                max_delay = max(max_delay, delay_minutes)

                # Add delay reason
                reason = service.get("delay_reason")
                if reason and reason not in disruption_reasons:
                    disruption_reasons.append(reason)

            elif delay_minutes >= DISRUPTION_DELAY_THRESHOLD_MULTIPLE:
                delayed_services += 1
                max_delay = max(max_delay, delay_minutes)

                # Add delay reason
                reason = service.get("delay_reason")
                if reason and reason not in disruption_reasons:
                    disruption_reasons.append(reason)

        # Check for multiple moderate delays
        if (
            not has_disruption
            and delayed_services >= DISRUPTION_MULTIPLE_SERVICES
        ):
            has_disruption = True
            disruption_type = "delay"
            affected_services = delayed_services

        # Set disruption type to "multiple" if both cancellations and delays
        if cancelled_services > 0 and delayed_services > 0 and has_disruption:
            disruption_type = "multiple"

        return {
            "has_disruption": has_disruption,
            "disruption_type": disruption_type,
            "affected_services": affected_services,
            "cancelled_services": cancelled_services,
            "delayed_services": delayed_services,
            "max_delay_minutes": max_delay,
            "disruption_reasons": disruption_reasons,
        }

    def _build_summary(
        self, on_time_count: int, delayed_count: int, cancelled_count: int
    ) -> str:
        """Build a summary string for the commute status.

        Args:
            on_time_count: Number of on-time services
            delayed_count: Number of delayed services
            cancelled_count: Number of cancelled services

        Returns:
            Summary string
        """
        total = on_time_count + delayed_count + cancelled_count

        if total == 0:
            return "No trains found"

        # Check for severe disruption
        if cancelled_count > 0 and delayed_count > 0:
            return "Severe disruptions"

        if cancelled_count > 0:
            if cancelled_count == total:
                return "All trains cancelled"
            return f"{cancelled_count} train{'s' if cancelled_count != 1 else ''} cancelled"

        if delayed_count > 0:
            if delayed_count == total:
                return "All trains delayed"
            running = on_time_count + delayed_count
            return f"{running} train{'s' if running != 1 else ''} running, {delayed_count} delayed"

        # All on time
        return f"{on_time_count} train{'s' if on_time_count != 1 else ''} on time"
