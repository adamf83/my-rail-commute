"""Rail API client for Live Departure Boards."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import (
    API_BASE_URL,
    API_TIMEOUT,
    ERROR_API_UNAVAILABLE,
    ERROR_AUTH,
    ERROR_INVALID_STATION,
    ERROR_NETWORK,
    ERROR_RATE_LIMIT,
    STATUS_CANCELLED,
    STATUS_DELAYED,
    STATUS_ON_TIME,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class NationalRailAPIError(Exception):
    """Base exception for Rail API errors."""


class AuthenticationError(NationalRailAPIError):
    """Authentication failed."""


class InvalidStationError(NationalRailAPIError):
    """Invalid station code."""


class RateLimitError(NationalRailAPIError):
    """API rate limit exceeded."""


class NationalRailAPI:
    """Rail API client for Live Departure Boards."""

    def __init__(self, api_key: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client.

        Args:
            api_key: Rail Data Marketplace API key
            session: aiohttp client session
        """
        self._api_key = api_key
        self._session = session
        self._base_url = API_BASE_URL
        self._headers = {
            "x-apikey": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Make an API request with retry logic.

        Args:
            endpoint: API endpoint path
            params: Query parameters
            retry_count: Current retry attempt
            max_retries: Maximum number of retries

        Returns:
            Parsed JSON response

        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            NationalRailAPIError: For other API errors
        """
        url = f"{self._base_url}/{endpoint}"

        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.get(
                    url, headers=self._headers, params=params
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
                        _LOGGER.error("Authentication failed with status %s", response.status)
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
                                endpoint, params, retry_count + 1, max_retries
                            )
                        raise RateLimitError(ERROR_RATE_LIMIT)

                    if response.status == 404:
                        _LOGGER.error("Station not found (404) for endpoint: %s", endpoint)
                        raise InvalidStationError(ERROR_INVALID_STATION)

                    # Handle server errors (500+) with retry
                    if response.status >= 500:
                        if retry_count < max_retries:
                            wait_time = 2 ** retry_count
                            _LOGGER.warning(
                                "Server error %s, retrying in %s seconds (attempt %s/%s)",
                                response.status,
                                wait_time,
                                retry_count + 1,
                                max_retries,
                            )
                            await asyncio.sleep(wait_time)
                            return await self._request(
                                endpoint, params, retry_count + 1, max_retries
                            )
                        _LOGGER.error("API server error %s after %s retries", response.status, max_retries)
                        raise NationalRailAPIError(f"API server error {response.status}: {ERROR_API_UNAVAILABLE}")

                    # Check for other non-success status codes
                    response.raise_for_status()

                    data = await response.json()
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
                    endpoint, params, retry_count + 1, max_retries
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
                    endpoint, params, retry_count + 1, max_retries
                )
            _LOGGER.error("Network error: %s", err)
            raise NationalRailAPIError(ERROR_NETWORK) from err

    async def get_departure_board(
        self,
        origin_crs: str,
        destination_crs: str,
        time_window: int = 60,
        num_rows: int = 10,
    ) -> dict[str, Any]:
        """Get departure board for a route.

        Args:
            origin_crs: Origin station CRS code (3 letters)
            destination_crs: Destination station CRS code (3 letters)
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
            destination_crs,
            time_window,
            num_rows,
        )

        # Use path parameters for the CRS code and query parameters for filters
        endpoint = f"GetDepBoardWithDetails/{origin_crs.upper()}"
        params = {
            "filterCrs": destination_crs.upper(),
            "timeWindow": time_window,
            "numRows": num_rows,
        }

        try:
            data = await self._request(endpoint, params)
            return self._parse_departure_board(data)
        except InvalidStationError:
            raise
        except NationalRailAPIError as err:
            _LOGGER.error("Failed to get departure board: %s", err)
            raise

    def _parse_departure_board(self, data: dict[str, Any]) -> dict[str, Any]:
        """Parse departure board response.

        Args:
            data: Raw API response

        Returns:
            Parsed departure board data
        """
        # Handle different response structures
        board = data.get("GetStationBoardResult", data)

        location_name = board.get("locationName", "Unknown")
        destination_name = board.get("filterLocationName", "Unknown")

        # Extract services
        train_services = board.get("trainServices", {})
        services_list = train_services if isinstance(train_services, list) else train_services.get("service", [])

        if not isinstance(services_list, list):
            services_list = [services_list] if services_list else []

        parsed_services = []
        for service in services_list:
            parsed_service = self._parse_service(service)
            if parsed_service:
                parsed_services.append(parsed_service)

        return {
            "location_name": location_name,
            "destination_name": destination_name,
            "services": parsed_services,
            "generated_at": board.get("generatedAt"),
            "nrcc_messages": board.get("nrccMessages", []),
        }

    def _parse_service(self, service: dict[str, Any]) -> dict[str, Any] | None:
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
                if ":" in etd and ":" in std:
                    try:
                        # Use a reference date to parse times and handle midnight crossing
                        std_time = datetime.strptime(f"2000-01-01 {std}", "%Y-%m-%d %H:%M")
                        etd_time = datetime.strptime(f"2000-01-01 {etd}", "%Y-%m-%d %H:%M")

                        # Handle midnight crossing: if expected time is earlier than scheduled,
                        # it means the train departs/arrives the next day
                        if etd_time < std_time:
                            etd_time += timedelta(days=1)

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

            # Subsequent calling points
            calling_points = []
            subsequent_points = service.get("subsequentCallingPoints", [])
            if isinstance(subsequent_points, list) and subsequent_points:
                calling_point_list = subsequent_points[0].get("callingPoint", [])
                if not isinstance(calling_point_list, list):
                    calling_point_list = [calling_point_list]
                calling_points = [
                    cp.get("locationName", "") for cp in calling_point_list if cp
                ]

            # Arrival time (use last calling point or estimate)
            scheduled_arrival = None
            estimated_arrival = None
            if calling_points and isinstance(subsequent_points, list) and subsequent_points:
                calling_point_list = subsequent_points[0].get("callingPoint", [])
                if isinstance(calling_point_list, list) and calling_point_list:
                    last_point = calling_point_list[-1]
                    scheduled_arrival = last_point.get("st")
                    estimated_arrival = last_point.get("et")

            return {
                "scheduled_departure": std,
                "expected_departure": expected_departure or std,
                "platform": platform,
                "operator": operator_name,
                "service_id": service_id,
                "calling_points": calling_points,
                "delay_minutes": delay_minutes,
                "status": status,
                "is_cancelled": is_cancelled,
                "cancellation_reason": cancel_reason if is_cancelled else None,
                "delay_reason": delay_reason if not is_cancelled else None,
                "scheduled_arrival": scheduled_arrival,
                "estimated_arrival": estimated_arrival or scheduled_arrival,
                "destination": destination,
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

        except (AuthenticationError, RateLimitError):
            # Re-raise auth and rate limit errors
            raise
        except Exception as err:
            _LOGGER.error("Station validation failed for %s: %s", crs_code, err)
            raise InvalidStationError(ERROR_INVALID_STATION) from err

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
