"""Data update coordinator for My Rail Commute integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import NationalRailAPI, NationalRailAPIError
from .const import (
    CONF_ALL_DEPARTURES,
    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
    CONF_DESTINATION,
    CONF_DISRUPTION_MULTIPLE_DELAY,
    CONF_DISRUPTION_SINGLE_DELAY,
    CONF_LEGS,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MIN_CONNECTION_TIME,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ONLY_CATCHABLE_SERVICES,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD,
    DEFAULT_MAJOR_DELAY_THRESHOLD,
    DEFAULT_MIN_CONNECTION_TIME,
    DEFAULT_MINOR_DELAY_THRESHOLD,
    DEFAULT_SEVERE_DELAY_THRESHOLD,
    DOMAIN,
    MIN_DELAY_THRESHOLD,
    NIGHT_HOURS,
    PEAK_HOURS,
    STATUS_CANCELLED,
    STATUS_CONNECTION_DELAYED,
    STATUS_CONNECTION_MISSED,
    STATUS_CONNECTION_OK,
    STATUS_CONNECTION_TIGHT,
    STATUS_CONNECTION_UNKNOWN,
    STATUS_CRITICAL,
    STATUS_DELAYED,
    STATUS_MAJOR_DELAYS,
    STATUS_MINOR_DELAYS,
    STATUS_NORMAL,
    STATUS_ON_TIME,
    STATUS_SEVERE_DISRUPTION,
    TIGHT_CONNECTION_MARGIN,
    UPDATE_INTERVAL_NIGHT,
    UPDATE_INTERVAL_OFF_PEAK,
    UPDATE_INTERVAL_PEAK,
)

_LOGGER = logging.getLogger(__name__)

_TIME_FORMAT_RE = re.compile(r"^\d{2}:\d{2}$")

# Severity ranking used to combine per-leg statuses into one overall status
_STATUS_ORDER: list[str] = [
    STATUS_NORMAL,
    STATUS_MINOR_DELAYS,
    STATUS_MAJOR_DELAYS,
    STATUS_SEVERE_DISRUPTION,
    STATUS_CRITICAL,
]

# Maps each per-connection feasibility status onto the same severity hierarchy
# used for per-leg statuses, so a missed connection can push the journey's
# overall_status up without introducing a separate vocabulary.
_CONNECTION_STATUS_SEVERITY: dict[str, str] = {
    STATUS_CONNECTION_OK: STATUS_NORMAL,
    STATUS_CONNECTION_UNKNOWN: STATUS_NORMAL,
    STATUS_CONNECTION_TIGHT: STATUS_MINOR_DELAYS,
    STATUS_CONNECTION_DELAYED: STATUS_MAJOR_DELAYS,
    STATUS_CONNECTION_MISSED: STATUS_CRITICAL,
}


def build_route_id(legs: list[dict[str, Any]]) -> str:
    """Build a stable, chain-based route identifier from a list of legs.

    For a single leg with a destination this is byte-identical to the
    historical `f"{origin}_{destination}"` format. Every intermediate
    station is embedded in the id, so different multi-leg chains that
    happen to share the same overall origin/destination never collide.

    Args:
        legs: List of {"origin": crs, "destination": crs|None} dicts

    Returns:
        Route identifier string
    """
    if len(legs) == 1 and legs[0]["destination"] is None:
        return f"{legs[0]['origin']}_all_departures"
    return "_".join([legs[0]["origin"]] + [leg["destination"] for leg in legs])


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
        self._update_interval_lock = asyncio.Lock()

        # Get configuration
        self.origin = config[CONF_ORIGIN]
        self.destination = config.get(CONF_DESTINATION)  # None when all_departures=True
        self.all_departures = config.get(CONF_ALL_DEPARTURES, False)

        # Journey legs: a list of {"origin": crs, "destination": crs|None} dicts.
        # Falls back to a single leg built from origin/destination when CONF_LEGS
        # is absent (all existing single-leg entries), so behaviour is unchanged.
        self.legs: list[dict[str, Any]] = config.get(CONF_LEGS) or [
            {"origin": self.origin, "destination": self.destination}
        ]
        self.is_multi_leg = len(self.legs) > 1
        self.time_window = int(config[CONF_TIME_WINDOW])
        self.num_services = int(config[CONF_NUM_SERVICES])
        self.night_updates_enabled = config.get(CONF_NIGHT_UPDATES, False)
        self.departed_train_grace_period = int(
            config.get(
                CONF_DEPARTED_TRAIN_GRACE_PERIOD, DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD
            )
        )
        self.min_connection_time = int(
            config.get(CONF_MIN_CONNECTION_TIME, DEFAULT_MIN_CONNECTION_TIME)
        )
        self.only_catchable_services = bool(
            config.get(CONF_ONLY_CATCHABLE_SERVICES, False)
        )

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
                config.get(
                    CONF_DISRUPTION_MULTIPLE_DELAY, DEFAULT_MAJOR_DELAY_THRESHOLD
                )
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

        # Historical stats recorder — attached externally by async_setup_entry
        self.stats_store: Any | None = None

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
                _LOGGER.debug(
                    "Using longer interval during night time (manual refresh still works)"
                )
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
        _LOGGER.debug(
            "Starting data update for %s -> %s",
            self.origin,
            self.destination or "ALL",
        )

        # Update interval may have changed (e.g., switching between peak/off-peak/night)
        # Use async lock to prevent race conditions with concurrent updates
        async with self._update_interval_lock:
            new_interval = self._get_update_interval()
            if new_interval != self.update_interval:
                _LOGGER.debug(
                    "Updating interval from %s to %s",
                    self.update_interval,
                    new_interval,
                )
                self.update_interval = new_interval

        try:
            _LOGGER.debug(
                "Fetching departure data for %s -> %s",
                self.origin,
                self.destination or "ALL",
            )

            if self.is_multi_leg:
                # Fetch each leg sequentially (not concurrently) so the shared
                # API client's rate limiter can throttle correctly between calls
                raw_leg_data: list[dict[str, Any]] = []
                # When filtering to catchable-only, over-fetch so there's a
                # large enough candidate pool to filter down from and match
                # connections against (mirrors all_departures' over-fetch).
                leg_num_rows = (
                    max(self.num_services, 20)
                    if self.only_catchable_services
                    else self.num_services
                )
                for leg in self.legs:
                    raw_leg_data.append(
                        await self.api.get_departure_board(
                            leg["origin"],
                            destination_crs=leg["destination"],
                            time_window=self.time_window,
                            num_rows=leg_num_rows,
                        )
                    )

                self.origin_name = raw_leg_data[0].get("location_name", self.origin)
                self.destination_name = raw_leg_data[-1].get(
                    "destination_name", self.destination
                )

                parsed_data = self._parse_data(raw_leg_data)
            else:
                # When showing all departures, fetch enough rows to populate multiple destinations
                num_rows = (
                    max(self.num_services, 20)
                    if self.all_departures
                    else self.num_services
                )

                # Fetch departure board
                data = await self.api.get_departure_board(
                    self.origin,
                    destination_crs=self.destination,
                    time_window=self.time_window,
                    num_rows=num_rows,
                )

                # Store station names
                self.origin_name = data.get("location_name", self.origin)
                self.destination_name = data.get("destination_name", self.destination)

                # Parse and enrich data
                parsed_data = self._parse_data(data)

            # Record observation in historical stats store
            if self.stats_store is not None:
                await self.stats_store.async_record_observation(parsed_data)

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
            _LOGGER.error(
                "Error fetching data: %s (attempt %s/%s)",
                err,
                self._failed_updates,
                self._max_failed_updates,
            )

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
                                age.total_seconds() / 3600,
                            )
                            raise UpdateFailed(
                                f"Failed to fetch data and cached data too old: {err}"
                            ) from err
                    else:
                        _LOGGER.error(
                            "Failed to parse last_updated timestamp '%s', cannot verify data age",
                            self.data.get("last_updated"),
                        )
                except (ValueError, TypeError) as parse_err:
                    _LOGGER.error(
                        "Error parsing last_updated timestamp '%s': %s - cannot verify data age",
                        self.data.get("last_updated"),
                        parse_err,
                    )

            # Otherwise, return last known data if available and recent
            if self.data:
                _LOGGER.warning(
                    "Using last known data after failed update (data age: unverified)"
                )
                return self.data

            raise UpdateFailed(f"Failed to fetch data: {err}") from err

    @staticmethod
    def _minutes_between(start: str | None, end: str | None) -> int | None:
        """Return the number of minutes from `start` to `end`.

        Both times are "HH:MM" strings without a date component, so a naive
        subtraction can be off by a day when the window straddles midnight
        (e.g. start="23:55", end="00:05"). If the raw difference is more
        than 12 hours in either direction, `end` is assumed to fall on the
        other side of a day boundary from `start`.

        Args:
            start: Reference time, "HH:MM"
            end: Target time, "HH:MM"

        Returns:
            Minutes from start to end (negative if end is before start), or
            None if either time is missing or not in "HH:MM" format
        """
        if (
            not start
            or not end
            or not _TIME_FORMAT_RE.match(start)
            or not _TIME_FORMAT_RE.match(end)
        ):
            return None

        start_dt = datetime.strptime(f"2000-01-01 {start}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"2000-01-01 {end}", "%Y-%m-%d %H:%M")

        diff_seconds = (end_dt - start_dt).total_seconds()
        if diff_seconds < -12 * 3600:
            end_dt += timedelta(days=1)
        elif diff_seconds > 12 * 3600:
            end_dt -= timedelta(days=1)

        return int((end_dt - start_dt).total_seconds() / 60)

    def _filter_departed_trains(
        self, services: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
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
            if "expected_departure" in service:
                departure_time = service["expected_departure"]
            else:
                departure_time = service.get("scheduled_departure")

            time_diff_minutes = self._minutes_between(current_time_str, departure_time)

            if time_diff_minutes is None:
                # If we can't parse the time, keep the service
                filtered_services.append(service)
                continue

            # Keep the train if it hasn't departed yet
            # Add a grace period to account for update delays and slight delays
            grace_period_minutes = self.departed_train_grace_period
            if time_diff_minutes >= -grace_period_minutes:
                filtered_services.append(service)
            else:
                _LOGGER.debug(
                    "Filtering out departed train: scheduled %s, expected %s, current time %s",
                    service.get("scheduled_departure"),
                    service.get("expected_departure"),
                    current_time_str,
                )

        return filtered_services

    def _build_services_by_destination(
        self, services: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Group services by their destination and compute per-destination status.

        Args:
            services: Flat list of service data

        Returns:
            Dict keyed by destination name, each entry containing services + status counts
        """
        groups: dict[str, Any] = {}
        for svc in services:
            dest = svc.get("destination") or "Unknown"
            if dest not in groups:
                groups[dest] = {
                    "services": [],
                    "on_time_count": 0,
                    "delayed_count": 0,
                    "cancelled_count": 0,
                }
            groups[dest]["services"].append(svc)
            if svc.get("is_cancelled"):
                groups[dest]["cancelled_count"] += 1
            elif svc.get("status") == STATUS_DELAYED:
                groups[dest]["delayed_count"] += 1
            else:
                groups[dest]["on_time_count"] += 1

        for dest_data in groups.values():
            dest_data["status"] = self._calculate_overall_status(dest_data["services"])

        return groups

    def _parse_leg_data(
        self,
        leg: dict[str, Any],
        raw_data: dict[str, Any],
        next_leg_services: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Parse and enrich a single leg's raw API data.

        Args:
            leg: The leg's {"origin", "destination"} config
            raw_data: Raw departure board data for this leg
            next_leg_services: Raw services of the following leg, used to tag
                each service as catchable/not; None for the last leg (or a
                single-leg journey), which has nothing to connect onto

        Returns:
            Parsed per-leg data with additional calculated fields
        """
        services = raw_data.get("services", [])

        if next_leg_services is not None:
            # Evaluate (and optionally filter) against the full raw list
            # before truncating to num_services, so a catchable service
            # further down isn't cut off by a low num_services setting.
            services = self._filter_departed_trains(services)
            self._tag_catchable(services, next_leg_services)
            # Keep the full tagged candidate pool (pre-only_catchable-filter)
            # for connection-feasibility evaluation, so enabling the display
            # filter can't turn a real "Missed Connection" into a falsely
            # reassuring "Unknown" just because it emptied this leg's
            # displayed service list.
            connection_pool = services
            if self.only_catchable_services:
                services = [s for s in services if s["catchable"]]
            services = services[: self.num_services]
        else:
            # Limit to configured number of services
            services = services[: self.num_services]

            # Filter out trains that have already departed
            services = self._filter_departed_trains(services)
            connection_pool = services

        # Calculate statistics
        on_time_count = sum(1 for s in services if s.get("status") == STATUS_ON_TIME)
        delayed_count = sum(1 for s in services if s.get("status") == STATUS_DELAYED)
        cancelled_count = sum(
            1 for s in services if s.get("status") == STATUS_CANCELLED
        )

        # Calculate overall status using user-configurable thresholds
        overall_status = self._calculate_overall_status(services)

        # Collect delay information for attributes
        delay_info = self._collect_delay_info(services)

        # Build summary
        if self.all_departures:
            summary = self._build_all_departures_summary(
                len(services), on_time_count, delayed_count, cancelled_count
            )
        elif not services and connection_pool:
            # only_catchable_services filtered out every real service on this
            # leg — say so explicitly rather than the misleading "No trains
            # found" (which implies no service is running at all).
            summary = "No catchable connections"
        else:
            summary = self._build_summary(on_time_count, delayed_count, cancelled_count)

        # Get next train (first non-cancelled service)
        next_train = None
        for service in services:
            if not service.get("is_cancelled", False):
                next_train = service
                break

        # Next train from the full (pre-only_catchable-filter) candidate
        # pool, used for connection-feasibility evaluation so that filtering
        # this leg's displayed services can't hide a real missed connection.
        connection_next_train = None
        for service in connection_pool:
            if not service.get("is_cancelled", False):
                connection_next_train = service
                break

        return {
            "origin": leg["origin"],
            "origin_name": raw_data.get("location_name", leg["origin"]),
            "destination": leg["destination"],
            "destination_name": raw_data.get("destination_name", leg["destination"]),
            "services_tracked": len(services),
            "total_services_found": len(raw_data.get("services", [])),
            "services": services,
            "on_time_count": on_time_count,
            "delayed_count": delayed_count,
            "cancelled_count": cancelled_count,
            "next_train": next_train,
            "overall_status": overall_status,  # Unified status for all sensors
            "max_delay_minutes": delay_info["max_delay_minutes"],
            "disruption_reasons": delay_info["disruption_reasons"],
            "summary": summary,
            "nrcc_messages": raw_data.get("nrcc_messages", []),
            "connection_services": connection_pool,
            "connection_next_train": connection_next_train,
        }

    def _combine_statuses(self, statuses: list[str]) -> str:
        """Combine per-leg statuses into one overall status (worst case wins).

        Args:
            statuses: List of per-leg overall_status strings

        Returns:
            The most severe status across all legs, or Normal if empty
        """
        if not statuses:
            return STATUS_NORMAL
        return max(statuses, key=_STATUS_ORDER.index)

    def _find_connecting_service(
        self,
        arrival: str | None,
        candidates: list[dict[str, Any]],
        *,
        scheduled_only: bool = False,
    ) -> tuple[dict[str, Any] | None, int | None]:
        """Find the first candidate service catchable from an arrival time.

        Args:
            arrival: HH:MM arrival time at the interchange, or None
            candidates: Non-cancelled outgoing services to search, in
                departure order
            scheduled_only: Ignore live running estimates and match purely
                on timetabled times. Used to work out which service would
                have formed the connection in a delay-free world, so actual
                delays can be told apart from ordinary timetable spacing
                (e.g. several closely-spaced local departures before the
                one that's actually reachable).

        Returns:
            (service, buffer_minutes) for the first candidate departing at
            least `min_connection_time` minutes after `arrival`, or
            (None, None) if none qualify
        """
        if not arrival or not _TIME_FORMAT_RE.match(arrival):
            return None, None
        for service in candidates:
            if scheduled_only:
                departure = service.get("scheduled_departure")
            else:
                departure = service.get("expected_departure") or service.get(
                    "scheduled_departure"
                )
            buffer_minutes = self._minutes_between(arrival, departure)
            if buffer_minutes is None:
                continue
            if buffer_minutes >= self.min_connection_time:
                return service, buffer_minutes
        return None, None

    def _tag_catchable(
        self,
        services: list[dict[str, Any]],
        next_leg_services: list[dict[str, Any]],
    ) -> None:
        """Tag each service in-place with whether its connection is catchable.

        Matches against the next leg's full raw candidate pool rather than
        its own (possibly truncated/filtered) displayed list, so a train
        isn't excluded from consideration just because of display limits.

        Args:
            services: This leg's services, already filtered for departed
                trains
            next_leg_services: The following leg's raw services
        """
        candidates = self._filter_departed_trains(
            [s for s in next_leg_services if not s.get("is_cancelled", False)]
        )
        for service in services:
            arrival = service.get("estimated_arrival") or service.get(
                "scheduled_arrival"
            )
            matched, _ = self._find_connecting_service(arrival, candidates)
            service["catchable"] = matched is not None

    def _evaluate_connection(
        self, leg_from: dict[str, Any], leg_to: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate whether the change between two adjacent legs is feasible.

        Compares the incoming leg's next train's expected arrival at the
        interchange against the outgoing leg's tracked services, walking
        forward to find the first non-cancelled service that leaves with at
        least `min_connection_time` minutes to spare.

        Args:
            leg_from: Parsed data for the leg arriving at the interchange
            leg_to: Parsed data for the leg departing from the interchange

        Returns:
            Dict describing the connection: station, timings, buffer, and a
            status of Connection OK / Tight Connection / Delayed Connection /
            Missed Connection / Unknown
        """
        base: dict[str, Any] = {
            "station": leg_from.get("destination"),
            "station_name": leg_from.get("destination_name"),
            "arrival_time": None,
            "connecting_departure": None,
            "connecting_service_id": None,
            "buffer_minutes": None,
            "min_required_minutes": self.min_connection_time,
            "feasible": None,
            "status": STATUS_CONNECTION_UNKNOWN,
        }

        # Use the full (pre-only_catchable-filter) candidate pool rather than
        # the possibly display-filtered "next_train"/"services", so enabling
        # only_catchable_services can't turn a real missed connection into a
        # falsely reassuring "Unknown" just because it emptied a leg's
        # displayed service list. Falls back to the display fields when the
        # connection-specific ones aren't present (e.g. hand-built dicts in
        # tests, or a last leg with nothing to connect onto).
        next_train_from = leg_from.get("connection_next_train") or leg_from.get(
            "next_train"
        )
        if not next_train_from:
            return base

        arrival = next_train_from.get("estimated_arrival") or next_train_from.get(
            "scheduled_arrival"
        )
        if not arrival or not _TIME_FORMAT_RE.match(arrival):
            return base

        base["arrival_time"] = arrival

        candidates = [
            service
            for service in (
                leg_to.get("connection_services") or leg_to.get("services", [])
            )
            if not service.get("is_cancelled", False)
        ]
        matched_service, matched_buffer = self._find_connecting_service(
            arrival, candidates
        )

        if matched_service is None:
            base["feasible"] = False
            base["status"] = STATUS_CONNECTION_MISSED
            return base

        base["connecting_departure"] = matched_service.get(
            "expected_departure"
        ) or matched_service.get("scheduled_departure")
        base["connecting_service_id"] = matched_service.get("service_id")
        base["buffer_minutes"] = matched_buffer
        base["feasible"] = True

        # Work out which service would have formed this connection in a
        # delay-free world (timetabled arrival vs timetabled departures
        # only). Several closely-spaced services departing too soon after
        # arrival to catch is normal timetable spacing, not a delay - so
        # only label the connection "Delayed" when an actual delay is what
        # changed which service is being used, not just because it isn't
        # the very next departure from the interchange.
        scheduled_arrival = next_train_from.get("scheduled_arrival") or arrival
        scheduled_matched, _ = self._find_connecting_service(
            scheduled_arrival, candidates, scheduled_only=True
        )
        delayed = (
            scheduled_matched is not None
            and scheduled_matched.get("service_id") != matched_service.get(
                "service_id"
            )
        )
        if delayed:
            base["status"] = STATUS_CONNECTION_DELAYED
        elif matched_buffer >= self.min_connection_time + TIGHT_CONNECTION_MARGIN:
            base["status"] = STATUS_CONNECTION_OK
        else:
            base["status"] = STATUS_CONNECTION_TIGHT

        return base

    def _parse_data(
        self, data: dict[str, Any] | list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Parse and enrich API data.

        Args:
            data: Raw API data for a single leg, or a list of raw API data
                (one per leg, same order as self.legs) for a multi-leg journey

        Returns:
            Parsed data with additional calculated fields
        """
        if not self.is_multi_leg:
            leg_result = self._parse_leg_data(self.legs[0], data)

            result: dict[str, Any] = {
                "origin": self.origin,
                "origin_name": self.origin_name or self.origin,
                "destination": self.destination,
                "destination_name": self.destination_name,
                "time_window": self.time_window,
                "services_tracked": leg_result["services_tracked"],
                "total_services_found": leg_result["total_services_found"],
                "services": leg_result["services"],
                "on_time_count": leg_result["on_time_count"],
                "delayed_count": leg_result["delayed_count"],
                "cancelled_count": leg_result["cancelled_count"],
                "next_train": leg_result["next_train"],
                "overall_status": leg_result[
                    "overall_status"
                ],  # Unified status for all sensors
                "max_delay_minutes": leg_result["max_delay_minutes"],
                "disruption_reasons": leg_result["disruption_reasons"],
                "summary": leg_result["summary"],
                "multi_destination": self.all_departures,
                "last_updated": dt_util.now().isoformat(),
                "next_update": (dt_util.now() + self.update_interval).isoformat(),
                "nrcc_messages": leg_result["nrcc_messages"],
            }

            if self.all_departures:
                result["services_by_destination"] = self._build_services_by_destination(
                    leg_result["services"]
                )

            return result

        # Multi-leg journey: data is a list of raw responses, one per leg
        leg_results = [
            self._parse_leg_data(
                leg,
                raw,
                next_leg_services=(
                    data[i + 1].get("services") if i + 1 < len(data) else None
                ),
            )
            for i, (leg, raw) in enumerate(zip(self.legs, data, strict=True))
        ]

        all_services: list[dict[str, Any]] = []
        for leg_number, leg_result in enumerate(leg_results, start=1):
            for service in leg_result["services"]:
                tagged_service = dict(service)
                tagged_service["leg"] = leg_number
                all_services.append(tagged_service)

        total_on_time = sum(lr["on_time_count"] for lr in leg_results)
        total_delayed = sum(lr["delayed_count"] for lr in leg_results)
        total_cancelled = sum(lr["cancelled_count"] for lr in leg_results)

        disruption_reasons: list[str] = []
        for lr in leg_results:
            for reason in lr["disruption_reasons"]:
                if reason not in disruption_reasons:
                    disruption_reasons.append(reason)

        nrcc_messages: list[Any] = []
        for lr in leg_results:
            for message in lr["nrcc_messages"]:
                if message not in nrcc_messages:
                    nrcc_messages.append(message)

        connections = [
            self._evaluate_connection(leg_results[i], leg_results[i + 1])
            for i in range(len(leg_results) - 1)
        ]
        journey_feasible = all(conn["feasible"] is not False for conn in connections)

        return {
            "origin": self.legs[0]["origin"],
            "origin_name": leg_results[0]["origin_name"],
            "destination": self.legs[-1]["destination"],
            "destination_name": leg_results[-1]["destination_name"],
            "time_window": self.time_window,
            "services_tracked": sum(lr["services_tracked"] for lr in leg_results),
            "total_services_found": sum(
                lr["total_services_found"] for lr in leg_results
            ),
            "services": all_services,
            "on_time_count": total_on_time,
            "delayed_count": total_delayed,
            "cancelled_count": total_cancelled,
            "next_train": leg_results[0]["next_train"],
            "overall_status": self._combine_statuses(
                [lr["overall_status"] for lr in leg_results]
                + [_CONNECTION_STATUS_SEVERITY[conn["status"]] for conn in connections]
            ),
            "max_delay_minutes": max(
                (lr["max_delay_minutes"] for lr in leg_results), default=0
            ),
            "disruption_reasons": disruption_reasons,
            "summary": self._build_summary(
                total_on_time, total_delayed, total_cancelled
            ),
            "multi_destination": False,
            "is_multi_leg": True,
            "legs": leg_results,
            "connections": connections,
            "journey_feasible": journey_feasible,
            "last_updated": dt_util.now().isoformat(),
            "next_update": (dt_util.now() + self.update_interval).isoformat(),
            "nrcc_messages": nrcc_messages,
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
            (
                s.get("delay_minutes", 0)
                for s in services
                if not s.get("is_cancelled", False)
            ),
            default=0,
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

    def _build_all_departures_summary(
        self, total: int, on_time_count: int, delayed_count: int, cancelled_count: int
    ) -> str:
        """Build summary text for all-departures mode.

        Args:
            total: Total number of services
            on_time_count: Number of on-time services
            delayed_count: Number of delayed services
            cancelled_count: Number of cancelled services

        Returns:
            Summary string for all departures from this origin
        """
        if total == 0:
            return f"No departures from {self.origin_name or self.origin}"

        issues = []
        if cancelled_count:
            issues.append(f"{cancelled_count} cancelled")
        if delayed_count:
            issues.append(f"{delayed_count} delayed")

        if issues:
            return f"{total} departure{'s' if total != 1 else ''} from {self.origin_name or self.origin} — {', '.join(issues)}"

        return f"{total} departure{'s' if total != 1 else ''} from {self.origin_name or self.origin}"
