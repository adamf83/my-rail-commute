"""Rail API client for Live Departure Boards."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import (
    API_BASE_URL,
    ARRIVAL_API_BASE_URL,
    API_TIMEOUT,
    ERROR_API_UNAVAILABLE,
    ERROR_AUTH,
    ERROR_INVALID_STATION,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    STATUS_CANCELLED,
    STATUS_DELAYED,
    SERVICE_DETAILS_API_BASE_URL,
    STATUS_ON_TIME,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Rail Data Marketplace sells each LDBWS operation group as a separate product
# with its own subscription and API key
PRODUCT_DEPARTURE = "departure"
PRODUCT_ARRIVAL = "arrival"
PRODUCT_SERVICE_DETAILS = "service_details"

_PRODUCT_LABELS = {
    PRODUCT_DEPARTURE: "departure board",
    PRODUCT_ARRIVAL: "arrival board",
    PRODUCT_SERVICE_DETAILS: "service details",
}

_TIME_FORMAT_RE = re.compile(r"^\d{2}:\d{2}$")

# Rate limit configuration
# These defaults are conservative; adjust based on actual API limits
DEFAULT_RATE_LIMIT_PER_MINUTE = 10
DEFAULT_RATE_LIMIT_PER_HOUR = 100
RATE_LIMIT_THROTTLE_THRESHOLD = 0.8  # Throttle at 80% of limit
# Extra random delay (as a fraction of the base delay) added on top of the
# throttle wait so multiple coordinators don't stay locked in sync
RATE_LIMIT_JITTER_FACTOR = 0.5


class NationalRailAPIError(Exception):
    """Base exception for Rail API errors."""


class AuthenticationError(NationalRailAPIError):
    """Authentication failed."""


class MissingAPIKeyError(AuthenticationError):
    """No API key is configured for the product an endpoint belongs to."""


class InvalidStationError(NationalRailAPIError):
    """Invalid station code.

    ``field`` optionally names the config key (e.g. origin/destination) that
    held the bad code, so the UI can flag the right input.
    """

    def __init__(self, message: str = "", field: str | None = None) -> None:
        """Initialise the error, optionally recording the offending field."""
        super().__init__(message)
        self.field = field


class RateLimitError(NationalRailAPIError):
    """API rate limit exceeded."""


class NationalRailAPI:
    """Rail API client for Live Departure Boards."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
        rate_limit_per_hour: int = DEFAULT_RATE_LIMIT_PER_HOUR,
        arrival_api_key: str | None = None,
        service_details_api_key: str | None = None,
    ) -> None:
        """Initialize the API client.

        Args:
            api_key: Rail Data Marketplace API key
            session: aiohttp client session
            rate_limit_per_minute: Maximum requests per minute
            rate_limit_per_hour: Maximum requests per hour
            arrival_api_key: Key for the arrival board product (used to
                confirm actual arrivals); None if not subscribed
            service_details_api_key: Key for the service details product;
                None if not subscribed
        """
        self._api_key = api_key
        self._session = session
        self._base_url = API_BASE_URL
        self._headers = self._build_headers(api_key)
        # Product -> (base URL, headers); a product without a key is absent
        self._products: dict[str, tuple[str, dict[str, str]]] = {
            PRODUCT_DEPARTURE: (API_BASE_URL, self._headers),
        }
        if arrival_api_key:
            self._products[PRODUCT_ARRIVAL] = (
                ARRIVAL_API_BASE_URL,
                self._build_headers(arrival_api_key),
            )
        if service_details_api_key:
            self._products[PRODUCT_SERVICE_DETAILS] = (
                SERVICE_DETAILS_API_BASE_URL,
                self._build_headers(service_details_api_key),
            )

        # Rate limit tracking
        self._rate_limit_per_minute = rate_limit_per_minute
        self._rate_limit_per_hour = rate_limit_per_hour
        self._call_timestamps: deque[datetime] = deque()  # Sliding window of API call times

    @staticmethod
    def _build_headers(api_key: str) -> dict[str, str]:
        """Return the request headers for an API key."""
        return {
            "x-apikey": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def has_product(self, product: str) -> bool:
        """Return True if a key is configured for the product."""
        return product in self._products

    def _clean_old_calls(self, window_minutes: int) -> None:
        """Remove API call timestamps older than the specified window.

        Args:
            window_minutes: Time window in minutes to keep
        """
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        while self._call_timestamps and self._call_timestamps[0] < cutoff_time:
            self._call_timestamps.popleft()

    def _get_calls_in_window(self, window_minutes: int) -> int:
        """Get the number of API calls within the specified time window.

        Args:
            window_minutes: Time window in minutes

        Returns:
            Number of calls within the window
        """
        self._clean_old_calls(window_minutes)
        return len(self._call_timestamps)

    def _check_rate_limit_proximity(self) -> tuple[bool, float]:
        """Check if we're approaching rate limits.

        Returns:
            Tuple of (should_throttle, wait_seconds)
            - should_throttle: True if we should wait before making the next call
            - wait_seconds: How long to wait (0 if no throttling needed)
        """
        # Check per-minute limit
        calls_per_minute = self._get_calls_in_window(1)
        minute_threshold = int(self._rate_limit_per_minute * RATE_LIMIT_THROTTLE_THRESHOLD)

        if calls_per_minute >= self._rate_limit_per_minute:
            # At or over limit - calculate wait time until oldest call expires
            if self._call_timestamps:
                oldest_call = self._call_timestamps[0]
                wait_until = oldest_call + timedelta(minutes=1)
                wait_seconds = max(0, (wait_until - datetime.now()).total_seconds())
                _LOGGER.warning(
                    "Rate limit reached: %s/%s calls per minute. Waiting %.1f seconds.",
                    calls_per_minute,
                    self._rate_limit_per_minute,
                    wait_seconds,
                )
                return True, wait_seconds
        elif calls_per_minute >= minute_threshold:
            # Approaching limit - add small delay to spread out requests, with
            # jitter so multiple coordinators don't wake up at the same time
            base_wait = 60.0 / self._rate_limit_per_minute
            wait_seconds = base_wait + random.uniform(
                0, base_wait * RATE_LIMIT_JITTER_FACTOR
            )
            _LOGGER.info(
                "Approaching rate limit: %s/%s calls per minute (threshold: %s). "
                "Adding %.1f second delay.",
                calls_per_minute,
                self._rate_limit_per_minute,
                minute_threshold,
                wait_seconds,
            )
            return True, wait_seconds

        # Check per-hour limit
        calls_per_hour = self._get_calls_in_window(60)
        hour_threshold = int(self._rate_limit_per_hour * RATE_LIMIT_THROTTLE_THRESHOLD)

        if calls_per_hour >= self._rate_limit_per_hour:
            # At or over limit
            if self._call_timestamps:
                # Find oldest call and wait until it expires from the hour window
                oldest_call = self._call_timestamps[0]
                wait_until = oldest_call + timedelta(hours=1)
                wait_seconds = max(0, (wait_until - datetime.now()).total_seconds())
                _LOGGER.warning(
                    "Hourly rate limit reached: %s/%s calls per hour. Waiting %.1f seconds.",
                    calls_per_hour,
                    self._rate_limit_per_hour,
                    wait_seconds,
                )
                return True, wait_seconds
        elif calls_per_hour >= hour_threshold:
            # Approaching hourly limit - add delay, with jitter so multiple
            # coordinators don't wake up at the same time
            base_wait = 3600.0 / self._rate_limit_per_hour
            wait_seconds = base_wait + random.uniform(
                0, base_wait * RATE_LIMIT_JITTER_FACTOR
            )
            _LOGGER.info(
                "Approaching hourly rate limit: %s/%s calls per hour (threshold: %s). "
                "Adding %.1f second delay.",
                calls_per_hour,
                self._rate_limit_per_hour,
                hour_threshold,
                wait_seconds,
            )
            return True, wait_seconds

        return False, 0.0

    async def _throttle_if_needed(self) -> None:
        """Proactively throttle requests if approaching rate limits."""
        should_throttle, wait_seconds = self._check_rate_limit_proximity()
        if should_throttle and wait_seconds > 0:
            _LOGGER.debug("Throttling request for %.1f seconds", wait_seconds)
            await asyncio.sleep(wait_seconds)

    def _record_api_call(self) -> None:
        """Record a successful API call for rate limit tracking."""
        self._call_timestamps.append(datetime.now())
        # Keep only last hour of data to prevent unbounded growth
        self._clean_old_calls(60)

        # Log current usage periodically (every 10th call)
        if len(self._call_timestamps) % 10 == 0:
            calls_per_minute = self._get_calls_in_window(1)
            calls_per_hour = self._get_calls_in_window(60)
            _LOGGER.debug(
                "API usage: %s/%s per minute, %s/%s per hour",
                calls_per_minute,
                self._rate_limit_per_minute,
                calls_per_hour,
                self._rate_limit_per_hour,
            )

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        product: str = PRODUCT_DEPARTURE,
    ) -> dict[str, Any]:
        """Make an API request with retry logic.

        Args:
            endpoint: API endpoint path
            params: Query parameters
            retry_count: Current retry attempt
            max_retries: Maximum number of retries
            product: Which Rail Data product (and so base URL and key) to use

        Returns:
            Parsed JSON response

        Raises:
            MissingAPIKeyError: If no key is configured for the product
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            NationalRailAPIError: For other API errors
        """
        if product not in self._products:
            raise MissingAPIKeyError(
                f"No API key configured for the {_PRODUCT_LABELS[product]} product"
            )
        base_url, headers = self._products[product]

        # Proactively check and throttle if approaching rate limits
        await self._throttle_if_needed()

        url = f"{base_url}/{endpoint}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.get(
                    url, headers=headers, params=params
                ) as response:
                    # Log the full request details for debugging
                    _LOGGER.debug(
                        "API request: %s %s (status: %s)",
                        "GET",
                        url,
                        response.status,
                    )

                    # Handle different status codes
                    if response.status == 401 or response.status == 403:
                        _LOGGER.error(
                            "Authentication failed with status %s (%s product)",
                            response.status,
                            _PRODUCT_LABELS[product],
                        )
                        raise AuthenticationError(ERROR_AUTH)

                    if response.status == 429:
                        # Rate limit exceeded
                        if retry_count < max_retries:
                            wait_time = 2 ** retry_count  # Exponential backoff
                            _LOGGER.warning(
                                "Rate limit exceeded, retrying in %s seconds",
                                wait_time,
                            )
                            await asyncio.sleep(wait_time)
                            return await self._request(
                                endpoint, params, retry_count + 1, max_retries, product
                            )
                        raise RateLimitError(ERROR_RATE_LIMIT)

                    if response.status == 400:
                        _LOGGER.error(
                            "Invalid request (400) for endpoint: %s. "
                            "This typically indicates an invalid CRS station code",
                            endpoint,
                        )
                        raise InvalidStationError(ERROR_INVALID_STATION)

                    if response.status == 404:
                        _LOGGER.error("Station not found (404) for endpoint: %s", endpoint)
                        raise InvalidStationError(ERROR_INVALID_STATION)

                    # Handle server errors (500+) with retry
                    if response.status >= 500:
                        if retry_count < max_retries:
                            wait_time = 2 ** retry_count
                            _LOGGER.warning(
                                "Server error %s from the %s product (%s), "
                                "retrying in %s seconds (attempt %s/%s)",
                                response.status,
                                _PRODUCT_LABELS[product],
                                endpoint.split("/", 1)[0],
                                wait_time,
                                retry_count + 1,
                                max_retries,
                            )
                            await asyncio.sleep(wait_time)
                            return await self._request(
                                endpoint, params, retry_count + 1, max_retries, product
                            )
                        _LOGGER.error(
                            "API server error %s from the %s product (%s) after %s retries",
                            response.status,
                            _PRODUCT_LABELS[product],
                            endpoint.split("/", 1)[0],
                            max_retries,
                        )
                        raise NationalRailAPIError(f"API server error {response.status}: {ERROR_API_UNAVAILABLE}")

                    # Check for other non-success status codes
                    response.raise_for_status()

                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError) as err:
                        _LOGGER.error("Invalid JSON response from API: %s", err)
                        raise NationalRailAPIError(ERROR_API_UNAVAILABLE) from err

                    # Record successful API call for rate limit tracking
                    self._record_api_call()

                    return data

        except asyncio.TimeoutError as err:
            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                _LOGGER.warning(
                    "Request timeout, retrying in %s seconds (attempt %s/%s)",
                    wait_time,
                    retry_count + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                return await self._request(
                    endpoint, params, retry_count + 1, max_retries, product
                )
            _LOGGER.error("Request timeout after %s retries", max_retries)
            raise NationalRailAPIError(ERROR_NETWORK) from err

        except ClientResponseError as err:
            # This should rarely be hit now since we handle status codes explicitly
            _LOGGER.error("Unhandled HTTP error %s: %s", err.status, err.message)
            raise NationalRailAPIError(f"HTTP error {err.status}: {ERROR_API_UNAVAILABLE}") from err

        except ClientError as err:
            if retry_count < max_retries:
                wait_time = 2 ** retry_count
                _LOGGER.warning("Network error, retrying in %s seconds", wait_time)
                await asyncio.sleep(wait_time)
                return await self._request(
                    endpoint, params, retry_count + 1, max_retries, product
                )
            _LOGGER.error("Network error: %s", err)
            raise NationalRailAPIError(ERROR_NETWORK) from err

    async def get_departure_board(
        self,
        origin_crs: str,
        destination_crs: str | None = None,
        time_window: int = 60,
        num_rows: int = 10,
    ) -> dict[str, Any]:
        """Get departure board for a route.

        Args:
            origin_crs: Origin station CRS code (3 letters)
            destination_crs: Destination station CRS code (3 letters), or None for all departures
            time_window: Time window in minutes
            num_rows: Number of services to retrieve

        Returns:
            Departure board data with services

        Raises:
            InvalidStationError: If station codes are invalid
            NationalRailAPIError: For other API errors
        """
        _LOGGER.debug(
            "Fetching departure board: %s -> %s (window: %s mins, rows: %s)",
            origin_crs,
            destination_crs or "ALL",
            time_window,
            num_rows,
        )

        # Use path parameters for the CRS code and query parameters for filters
        endpoint = f"GetDepBoardWithDetails/{origin_crs.upper()}"
        params: dict[str, Any] = {
            "timeWindow": time_window,
            "numRows": num_rows,
        }
        if destination_crs:
            params["filterCrs"] = destination_crs.upper()

        try:
            data = await self._request(endpoint, params)
            return self._parse_departure_board(data, destination_crs)
        except InvalidStationError:
            raise
        except NationalRailAPIError as err:
            _LOGGER.error("Failed to get departure board: %s", err)
            raise

    async def get_arrival_board(
        self,
        crs: str,
        origin_crs: str,
        time_offset: int = -10,
        time_window: int = 45,
        num_rows: int = 9,
        max_retries: int = 0,
    ) -> list[dict[str, Any]]:
        """Get raw train services arriving at a station from a given origin.

        Used to find the arrival-side service ID of a train that has left its
        origin, since service IDs are specific to the board that issued them.
        This is a separate Rail Data product from the departure board and needs
        its own API key.

        Args:
            crs: Station the board is for (the journey's destination)
            origin_crs: Only services that started at this station
            time_offset: Minutes relative to now at which the board starts
            time_window: Minutes the board covers from the offset
            num_rows: Maximum services (the "with details" board allows < 10)
            max_retries: Retries on a server error or timeout. The caller polls
                again soon anyway, so the default is not to block on them.

        Returns:
            Raw service items (as returned by the API), possibly empty

        Raises:
            MissingAPIKeyError: If no arrival board key is configured
            NationalRailAPIError: If the request fails or the response is not
                a station board
        """
        endpoint = f"GetArrBoardWithDetails/{crs.upper()}"
        params: dict[str, Any] = {
            "filterCrs": origin_crs.upper(),
            "filterType": "from",
            "timeOffset": time_offset,
            "timeWindow": time_window,
            "numRows": num_rows,
        }
        data = await self._request(
            endpoint, params, max_retries=max_retries, product=PRODUCT_ARRIVAL
        )
        if not isinstance(data, dict):
            raise NationalRailAPIError(
                f"Unexpected arrival board response type: {type(data).__name__}"
            )
        board = data.get("GetStationBoardResult", data)
        if not isinstance(board, dict):
            raise NationalRailAPIError(
                f"Unexpected station board structure: {type(board).__name__}"
            )
        return [
            s
            for s in self._extract_service_items(board, "trainServices")
            if isinstance(s, dict)
        ]

    async def get_service_details(self, service_id: str) -> dict[str, Any]:
        """Get details for one service, relative to the board that issued the ID.

        A service ID is only valid while the service is on that board (about
        two minutes after departure, or after a terminal arrival), so an
        expired ID is an expected failure. It is not retried. This is a
        separate Rail Data product and needs its own API key.

        Args:
            service_id: Service ID taken from a board response

        Returns:
            The raw service details

        Raises:
            MissingAPIKeyError: If no service details key is configured
            NationalRailAPIError: If the ID is no longer available or the
                response is not a service details object
        """
        data = await self._request(
            f"GetServiceDetails/{service_id}",
            max_retries=0,
            product=PRODUCT_SERVICE_DETAILS,
        )
        if isinstance(data, dict):
            details = data.get("GetServiceDetailsResult", data)
            if isinstance(details, dict):
                return details
        raise NationalRailAPIError(
            f"Unexpected service details response type: {type(data).__name__}"
        )

    def _parse_departure_board(self, data: dict[str, Any], destination_crs: str | None = None) -> dict[str, Any]:
        """Parse departure board response.

        Args:
            data: Raw API response

        Returns:
            Parsed departure board data

        Raises:
            NationalRailAPIError: If the response is not a station board
                structure (e.g. an unexpected type from the API)
        """
        if not isinstance(data, dict):
            raise NationalRailAPIError(
                f"Unexpected departure board response type: {type(data).__name__}"
            )

        # Handle different response structures
        board = data.get("GetStationBoardResult", data)

        if not isinstance(board, dict):
            raise NationalRailAPIError(
                f"Unexpected station board structure: {type(board).__name__}"
            )

        location_name = board.get("locationName", "Unknown")
        destination_name = board.get("filterLocationName") or None

        # Extract train and rail-replacement bus services. Darwin returns
        # these as separate sibling lists on the same board rather than
        # flagging bus entries within trainServices.
        services_list = self._extract_service_items(board, "trainServices")
        bus_services_list = self._extract_service_items(board, "busServices")

        parsed_services = []
        for service in services_list:
            parsed_service = self._parse_service(service, destination_crs, service_type="train")
            if parsed_service:
                parsed_services.append(parsed_service)

        for service in bus_services_list:
            parsed_service = self._parse_service(service, destination_crs, service_type="bus")
            if parsed_service:
                parsed_services.append(parsed_service)

        # Merge trains and buses into a single chronological timeline (Darwin
        # returns each list already ordered by departure, so this is a stable
        # merge on scheduled departure time).
        parsed_services.sort(key=self._service_sort_key)

        return {
            "location_name": location_name,
            "destination_name": destination_name,
            "services": parsed_services,
            "generated_at": board.get("generatedAt"),
            "nrcc_messages": board.get("nrccMessages", []),
        }

    def _extract_service_items(self, board: dict[str, Any], key: str) -> list[dict[str, Any]]:
        """Normalize a Darwin service list (trainServices/busServices) to a list.

        Args:
            board: The station board dict
            key: The list key to extract (e.g. "trainServices", "busServices")

        Returns:
            List of raw service item dicts (possibly empty)
        """
        services = board.get(key, {})
        if isinstance(services, list):
            services_list = services
        elif isinstance(services, dict):
            services_list = services.get("service", [])
        else:
            services_list = []

        if not isinstance(services_list, list):
            services_list = [services_list] if services_list else []

        return services_list

    def _service_sort_key(self, service: dict[str, Any]) -> tuple[int, int]:
        """Sort key placing services in scheduled-departure order.

        Services with an unparseable scheduled departure sort after all
        valid ones, preserving their relative order (stable sort).

        Args:
            service: A parsed service dict

        Returns:
            Tuple used as the sort key
        """
        std = service.get("scheduled_departure", "")
        if _TIME_FORMAT_RE.match(std):
            hours, minutes = std.split(":")
            return (0, int(hours) * 60 + int(minutes))
        return (1, 0)

    def _parse_service(
        self,
        service: dict[str, Any],
        destination_crs: str | None = None,
        service_type: str = "train",
    ) -> dict[str, Any] | None:
        """Parse a single train service.

        Args:
            service: Raw service data

        Returns:
            Parsed service data or None if invalid
        """
        try:
            # Basic service info
            std = service.get("std", "")  # Scheduled departure
            etd = service.get("etd", "")  # Estimated departure
            platform = service.get("platform", "")
            operator_name = service.get("operator", service.get("operatorName", ""))
            service_id = service.get("serviceID", service.get("serviceIdUrlSafe", ""))

            # Determine status
            is_cancelled = etd.lower() in ["cancelled", "canceled"]
            status = STATUS_CANCELLED if is_cancelled else STATUS_ON_TIME

            # Calculate delay
            delay_minutes = 0
            expected_departure = None

            if not is_cancelled and etd and etd != "On time":
                status = STATUS_DELAYED
                expected_departure = etd
                # Try to parse delay from etd if it's a time
                if _TIME_FORMAT_RE.match(etd) and _TIME_FORMAT_RE.match(std):
                    try:
                        # Use a reference date to parse times and handle midnight crossing
                        std_time = datetime.strptime(f"2000-01-01 {std}", "%Y-%m-%d %H:%M")
                        etd_time = datetime.strptime(f"2000-01-01 {etd}", "%Y-%m-%d %H:%M")

                        # Calculate initial time difference
                        time_diff_seconds = (etd_time - std_time).total_seconds()

                        # Handle midnight crossing: if absolute difference > 12 hours, adjust for day boundary
                        if time_diff_seconds < -12 * 3600:
                            # ETD is much earlier in the day, so it's actually next day
                            etd_time += timedelta(days=1)
                        elif time_diff_seconds > 12 * 3600:
                            # ETD is much later in the day, so it's actually previous day
                            etd_time -= timedelta(days=1)

                        delay_minutes = int((etd_time - std_time).total_seconds() / 60)
                    except ValueError:
                        pass

            # Cancellation/delay reason
            cancel_reason = service.get("cancelReason", service.get("delayReason"))
            delay_reason = service.get("delayReason")

            # Destination and calling points
            destination = service.get("destination", [])
            if isinstance(destination, list) and destination:
                destination = destination[0].get("locationName", "")
            elif isinstance(destination, dict):
                destination = destination.get("locationName", "")

            # Subsequent calling points and arrival time
            calling_points = []
            calling_point_details: list[dict[str, Any]] = []
            scheduled_arrival = None
            estimated_arrival = None
            subsequent_points = service.get("subsequentCallingPoints", [])
            if isinstance(subsequent_points, list) and subsequent_points:
                calling_point_list = subsequent_points[0].get("callingPoint", [])
                if not isinstance(calling_point_list, list):
                    calling_point_list = [calling_point_list]

                # Build calling points list, truncating at destination if configured
                dest_point = None
                filtered = []
                for cp in calling_point_list:
                    if not cp:
                        continue
                    filtered.append(cp)
                    if destination_crs and cp.get("crs", "").upper() == destination_crs.upper():
                        dest_point = cp
                        break  # Stop collecting stops after the destination

                if dest_point is None and filtered:
                    dest_point = filtered[-1]

                calling_points = [cp.get("locationName", "") for cp in filtered]
                calling_point_details = [
                    {
                        "name": cp.get("locationName", ""),
                        "crs": cp.get("crs", ""),
                        "scheduled": cp.get("st"),
                        # Real HH:MM, or text such as "On time" / "Delayed"
                        "expected": cp.get("et"),
                        "is_cancelled": cp.get("isCancelled") is True,
                    }
                    for cp in filtered
                ]
                if dest_point:
                    scheduled_arrival = dest_point.get("st")
                    # "et" is "On time"/"Delayed"/"Cancelled" etc. when not a
                    # real time (mirrors "etd" for departures) - only keep it
                    # when it's an actual HH:MM, otherwise fall back to the
                    # scheduled time below.
                    et = dest_point.get("et")
                    estimated_arrival = et if et and _TIME_FORMAT_RE.match(et) else None

            # Darwin doesn't assign platforms to rail-replacement buses;
            # show "via Bus" in that slot instead, matching how other
            # departure boards label these services.
            display_platform = "via Bus" if service_type == "bus" else platform

            return {
                "scheduled_departure": std,
                "expected_departure": expected_departure or std,
                "platform": display_platform,
                "operator": operator_name,
                "service_id": service_id,
                "calling_points": calling_points,
                "calling_point_details": calling_point_details,
                "delay_minutes": delay_minutes,
                "status": status,
                "is_cancelled": is_cancelled,
                "cancellation_reason": cancel_reason if is_cancelled else None,
                "delay_reason": delay_reason if not is_cancelled else None,
                "scheduled_arrival": scheduled_arrival,
                "estimated_arrival": estimated_arrival or scheduled_arrival,
                "destination": destination,
                "service_type": service_type,
            }
        except Exception as err:
            _LOGGER.error("Error parsing service: %s", err)
            return None

    async def validate_station(self, crs_code: str) -> str | None:
        """Validate a station CRS code and return the station name.

        Args:
            crs_code: 3-letter CRS code

        Returns:
            Station name if valid, None otherwise

        Raises:
            InvalidStationError: If station code is invalid
        """
        if not crs_code or len(crs_code) != 3:
            raise InvalidStationError(ERROR_INVALID_STATION)

        _LOGGER.debug("Validating station code: %s", crs_code)

        try:
            # Try to get a simple departure board with minimal rows
            endpoint = f"GetDepartureBoard/{crs_code.upper()}"
            params = {
                "numRows": 1,
            }
            data = await self._request(endpoint, params)

            # Extract station name from response
            board = data.get("GetStationBoardResult", data)
            location_name = board.get("locationName")

            if location_name:
                _LOGGER.debug("Station %s validated: %s", crs_code, location_name)
                return location_name

            raise InvalidStationError(ERROR_INVALID_STATION)

        except NationalRailAPIError as err:
            # Invalid-station, auth, rate-limit and connectivity errors keep
            # their own type so callers can report the real problem
            _LOGGER.debug("Station validation failed for %s: %s", crs_code, err)
            raise

    async def validate_api_key(self) -> bool:
        """Validate the API key by making a test request.

        Returns:
            True if API key is valid

        Raises:
            AuthenticationError: If authentication fails
        """
        _LOGGER.debug("Validating API key")

        try:
            # Make a simple request to validate credentials
            # Use a common station code for testing
            endpoint = "GetDepartureBoard/PAD"  # London Paddington
            params = {
                "numRows": 1,
            }
            await self._request(endpoint, params)
            _LOGGER.debug("API key validated successfully")
            return True

        except AuthenticationError:
            _LOGGER.error("API key validation failed")
            raise
        except Exception as err:
            _LOGGER.error("API key validation error: %s", err)
            raise AuthenticationError(ERROR_AUTH) from err

    async def validate_arrival_api_key(self) -> bool:
        """Validate the arrival board key with a one-row board request.

        Raises:
            AuthenticationError: If the key is rejected or not configured
            NationalRailAPIError: If the API cannot be reached
        """
        await self._request(
            "GetArrivalBoard/PAD",
            {"numRows": 1},
            max_retries=0,
            product=PRODUCT_ARRIVAL,
        )
        return True

    async def validate_service_details_api_key(self) -> bool:
        """Check that the service details key is accepted.

        There is no cheap call that always succeeds, so a lookup of an ID that
        cannot exist is made and only a rejected key counts as a failure: an
        "unknown service" answer proves the key is valid.

        Raises:
            AuthenticationError: If the key is rejected or not configured
            NationalRailAPIError: If the API cannot be reached
        """
        try:
            await self._request(
                "GetServiceDetails/0000000VALIDATE",
                max_retries=0,
                product=PRODUCT_SERVICE_DETAILS,
            )
        except (AuthenticationError, RateLimitError):
            raise
        except InvalidStationError:
            pass  # 400/404: the key was accepted, the ID is unknown
        except NationalRailAPIError as err:
            if str(err) == ERROR_NETWORK:
                raise
        return True

    async def close(self) -> None:
        """Close the API client and clean up resources.

        This should be called when the API client is no longer needed to ensure
        proper cleanup of the aiohttp ClientSession and prevent resource leaks.
        """
        if self._session and not self._session.closed:
            _LOGGER.debug("Closing aiohttp ClientSession")
            await self._session.close()
        self._session = None
