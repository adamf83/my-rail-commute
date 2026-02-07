"""Data update coordinator for My Rail Commute integration."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NationalRailAPI, NationalRailAPIError
from .const import (
    CONF_DESTINATION,
    CONF_DISRUPTION_MULTIPLE_COUNT,
    CONF_DISRUPTION_MULTIPLE_DELAY,
    CONF_DISRUPTION_SINGLE_DELAY,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DEFAULT_MAJOR_DELAY_THRESHOLD,
    DEFAULT_MINOR_DELAY_THRESHOLD,
    DEFAULT_SEVERE_DELAY_THRESHOLD,
    DOMAIN,
    MIN_DELAY_THRESHOLD,
    NIGHT_HOURS,
    PEAK_HOURS,
    STATUS_CANCELLED,
    STATUS_CRITICAL,
    STATUS_DELAYED,
    STATUS_MAJOR_DELAYS,
    STATUS_MINOR_DELAYS,
    STATUS_NORMAL,
    STATUS_ON_TIME,
    STATUS_SEVERE_DISRUPTION,
    UPDATE_INTERVAL_NIGHT,
    UPDATE_INTERVAL_OFF_PEAK,
    UPDATE_INTERVAL_PEAK,
)

_LOGGER = logging.getLogger(__name__)

_TIME_FORMAT_RE = re.compile(r"^\d{2}:\d{2}$")


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

        # Delay thresholds (user-configurable)
        # Support migration from old config format
        if CONF_SEVERE_DELAY_THRESHOLD in config:
            # New format
            self.severe_delay_threshold = int(config[CONF_SEVERE_DELAY_THRESHOLD])
            self.major_delay_threshold = int(config[CONF_MAJOR_DELAY_THRESHOLD])
            self.minor_delay_threshold = int(config[CONF_MINOR_DELAY_THRESHOLD])
        else:
            # Old format - migrate by using single delay threshold for severe
            # and multiple delay threshold for major, default for minor
            _LOGGER.info("Migrating from old threshold configuration format")
            self.severe_delay_threshold = int(
                config.get(CONF_DISRUPTION_SINGLE_DELAY, DEFAULT_SEVERE_DELAY_THRESHOLD)
            )
            self.major_delay_threshold = int(
                config.get(CONF_DISRUPTION_MULTIPLE_DELAY, DEFAULT_MAJOR_DELAY_THRESHOLD)
            )
            self.minor_delay_threshold = DEFAULT_MINOR_DELAY_THRESHOLD

        # Validate threshold hierarchy (catches manually edited .storage files)
        if not (
            self.severe_delay_threshold
            >= self.major_delay_threshold
            >= self.minor_delay_threshold
            >= MIN_DELAY_THRESHOLD
        ):
            _LOGGER.warning(
                "Invalid delay threshold hierarchy detected: "
                "severe (%s) >= major (%s) >= minor (%s) >= %s. "
                "Resetting to defaults",
                self.severe_delay_threshold,
                self.major_delay_threshold,
                self.minor_delay_threshold,
                MIN_DELAY_THRESHOLD,
            )
            self.severe_delay_threshold = DEFAULT_SEVERE_DELAY_THRESHOLD
            self.major_delay_threshold = DEFAULT_MAJOR_DELAY_THRESHOLD
            self.minor_delay_threshold = DEFAULT_MINOR_DELAY_THRESHOLD

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
        _LOGGER.debug("Starting data update for %s -> %s", self.origin, self.destination)

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

            _LOGGER.debug(
                "Data update complete: %d services found, status=%s",
                len(parsed_data.get("services", [])),
                parsed_data.get("overall_status", "Unknown"),
            )

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

            if not departure_time or not _TIME_FORMAT_RE.match(departure_time):
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

        # Calculate overall status using user-configurable thresholds
        overall_status = self._calculate_overall_status(services)

        # Collect delay information for attributes
        delay_info = self._collect_delay_info(services)

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
            "overall_status": overall_status,  # Unified status for all sensors
            "max_delay_minutes": delay_info["max_delay_minutes"],
            "disruption_reasons": delay_info["disruption_reasons"],
            "summary": summary,
            "last_updated": dt_util.now().isoformat(),
            "next_update": (dt_util.now() + self.update_interval).isoformat(),
            "nrcc_messages": data.get("nrcc_messages", []),
        }

    def _collect_delay_info(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        """Collect delay information for display attributes.

        Args:
            services: List of service data

        Returns:
            Dictionary with max_delay_minutes and disruption_reasons
        """
        max_delay = 0
        disruption_reasons = []

        for service in services:
            is_cancelled = service.get("is_cancelled", False)
            delay_minutes = service.get("delay_minutes", 0)

            if is_cancelled:
                # Collect cancellation reason
                reason = service.get("cancellation_reason")
                if reason and reason not in disruption_reasons:
                    disruption_reasons.append(reason)
            elif delay_minutes > 0:
                # Track max delay
                max_delay = max(max_delay, delay_minutes)

                # Collect delay reason
                reason = service.get("delay_reason")
                if reason and reason not in disruption_reasons:
                    disruption_reasons.append(reason)

        return {
            "max_delay_minutes": max_delay,
            "disruption_reasons": disruption_reasons,
        }

    def _calculate_overall_status(self, services: list[dict[str, Any]]) -> str:
        """Calculate overall commute status using user-configurable thresholds.

        This method provides a unified status hierarchy checked in priority order:
        1. Critical: Any cancellations (highest priority)
        2. Severe Disruption: Any train ≥ severe_delay_threshold
        3. Major Delays: Any train ≥ major_delay_threshold
        4. Minor Delays: Any train ≥ minor_delay_threshold
        5. Normal: All trains on time

        All thresholds are user-configurable with validation ensuring proper hierarchy.

        Args:
            services: List of service data

        Returns:
            Status string: Normal, Minor Delays, Major Delays, Severe Disruption, or Critical
        """
        if not services:
            return STATUS_NORMAL

        # Check for cancellations first (CRITICAL - highest priority)
        if any(s.get("is_cancelled", False) for s in services):
            return STATUS_CRITICAL

        # Get maximum delay from non-cancelled services
        max_delay = max(
            (s.get("delay_minutes", 0) for s in services if not s.get("is_cancelled", False)),
            default=0
        )

        # Check thresholds in priority order (high to low)
        if max_delay >= self.severe_delay_threshold:
            return STATUS_SEVERE_DISRUPTION
        if max_delay >= self.major_delay_threshold:
            return STATUS_MAJOR_DELAYS
        if max_delay >= self.minor_delay_threshold:
            return STATUS_MINOR_DELAYS

        # Everything is on time (or below minor threshold)
        return STATUS_NORMAL

    def _build_summary(
        self, on_time_count: int, delayed_count: int, cancelled_count: int
    ) -> str:
        """Build a summary string for the commute status.

        Focuses on counts rather than severity (severity is handled by overall_status).

        Args:
            on_time_count: Number of on-time services
            delayed_count: Number of delayed services
            cancelled_count: Number of cancelled services

        Returns:
            Summary string with counts
        """
        total = on_time_count + delayed_count + cancelled_count

        if total == 0:
            return "No trains found"

        # Build narrative summary based on counts
        if cancelled_count > 0:
            if cancelled_count == total:
                return "All trains cancelled"
            if delayed_count > 0:
                # Both cancellations and delays
                running = on_time_count + delayed_count
                return f"{running} train{'s' if running != 1 else ''} running, {cancelled_count} cancelled"
            # Cancellations only
            return f"{cancelled_count} train{'s' if cancelled_count != 1 else ''} cancelled"

        if delayed_count > 0:
            if delayed_count == total:
                return "All trains delayed"
            if on_time_count > 0:
                # Mix of on-time and delayed
                running = on_time_count + delayed_count
                return f"{running} train{'s' if running != 1 else ''} running, {delayed_count} delayed"
            # Delayed only
            return f"{delayed_count} train{'s' if delayed_count != 1 else ''} delayed"

        # All on time
        return f"{on_time_count} train{'s' if on_time_count != 1 else ''} on time"
