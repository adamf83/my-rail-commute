"""Tests for the National Rail API client."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError, ClientResponseError
from aioresponses import aioresponses
import pytest

from custom_components.my_rail_commute.api import (
    AuthenticationError,
    InvalidStationError,
    NationalRailAPI,
    NationalRailAPIError,
    RateLimitError,
)
from custom_components.my_rail_commute.const import (
    API_BASE_URL,
    STATUS_CANCELLED,
    STATUS_DELAYED,
    STATUS_ON_TIME,
)

from .conftest import load_json_fixture


@pytest.fixture(name="api_client")
async def api_client_fixture(aiohttp_session):
    """Create an API client with a real session."""
    client = NationalRailAPI("test_api_key", aiohttp_session)
    return client


class TestNationalRailAPIInit:
    """Tests for API client initialization."""

    async def test_init(self, aiohttp_session):
        """Test API client initialization."""
        api = NationalRailAPI("test_key", aiohttp_session)

        assert api._api_key == "test_key"
        assert api._session == aiohttp_session
        assert api._base_url == API_BASE_URL
        assert api._headers["x-apikey"] == "test_key"
        assert "User-Agent" in api._headers
        assert api._headers["Accept"] == "application/json"


class TestValidateAPIKey:
    """Tests for API key validation."""

    async def test_validate_api_key_success(self, api_client):
        """Test successful API key validation."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={"GetStationBoardResult": {"locationName": "London Paddington"}},
                status=200,
            )

            result = await api_client.validate_api_key()
            assert result is True

    async def test_validate_api_key_auth_failure(self, api_client):
        """Test API key validation with authentication failure."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                status=401,
            )

            with pytest.raises(AuthenticationError):
                await api_client.validate_api_key()

    async def test_validate_api_key_403_failure(self, api_client):
        """Test API key validation with 403 forbidden."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                status=403,
            )

            with pytest.raises(AuthenticationError):
                await api_client.validate_api_key()

    async def test_validate_api_key_network_error(self, api_client):
        """Test API key validation with network error."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                exception=ClientError("Network error"),
            )

            with pytest.raises(AuthenticationError):
                await api_client.validate_api_key()


class TestValidateStation:
    """Tests for station validation."""

    async def test_validate_station_success(self, api_client):
        """Test successful station validation."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={
                    "GetStationBoardResult": {
                        "locationName": "London Paddington",
                        "crs": "PAD",
                    }
                },
                status=200,
            )

            result = await api_client.validate_station("PAD")
            assert result == "London Paddington"

    async def test_validate_station_invalid_code(self, api_client):
        """Test station validation with invalid code."""
        with pytest.raises(InvalidStationError):
            await api_client.validate_station("")

        with pytest.raises(InvalidStationError):
            await api_client.validate_station("AB")

        with pytest.raises(InvalidStationError):
            await api_client.validate_station("ABCD")

    async def test_validate_station_not_found(self, api_client):
        """Test station validation with 404 not found."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/XYZ?numRows=1",
                status=404,
            )

            with pytest.raises(InvalidStationError):
                await api_client.validate_station("XYZ")

    async def test_validate_station_bad_request(self, api_client):
        """Test station validation with 400 bad request (invalid CRS code)."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAS?numRows=1",
                status=400,
            )

            with pytest.raises(InvalidStationError):
                await api_client.validate_station("PAS")

    async def test_validate_station_uppercase_conversion(self, api_client):
        """Test that station codes are converted to uppercase."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={
                    "GetStationBoardResult": {
                        "locationName": "London Paddington",
                    }
                },
                status=200,
            )

            result = await api_client.validate_station("pad")
            assert result == "London Paddington"


class TestGetDepartureBoard:
    """Tests for getting departure board."""

    async def test_get_departure_board_success(
        self, api_client, departure_board_response
    ):
        """Test successful departure board retrieval."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD?filterCrs=RDG&timeWindow=60&numRows=10",
                payload=departure_board_response,
                status=200,
            )

            result = await api_client.get_departure_board("PAD", "RDG")

            assert result["location_name"] == "London Paddington"
            assert result["destination_name"] == "Reading"
            assert len(result["services"]) == 3
            assert result["generated_at"] == "2024-01-15T08:30:00"

    async def test_get_departure_board_uppercase(self, api_client):
        """Test that station codes are converted to uppercase."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD?filterCrs=RDG&timeWindow=60&numRows=10",
                payload={"GetStationBoardResult": {"locationName": "Test", "trainServices": []}},
                status=200,
            )

            await api_client.get_departure_board("pad", "rdg")
            # If no exception, test passes

    async def test_get_departure_board_custom_params(self, api_client):
        """Test departure board with custom parameters."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD?filterCrs=RDG&timeWindow=120&numRows=5",
                payload={"GetStationBoardResult": {"locationName": "Test", "trainServices": []}},
                status=200,
            )

            await api_client.get_departure_board("PAD", "RDG", time_window=120, num_rows=5)
            # If no exception, test passes

    async def test_get_departure_board_invalid_station(self, api_client):
        """Test departure board with invalid station."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/XYZ?filterCrs=RDG&timeWindow=60&numRows=10",
                status=404,
            )

            with pytest.raises(InvalidStationError):
                await api_client.get_departure_board("XYZ", "RDG")

    async def test_get_departure_board_bad_request(self, api_client):
        """Test departure board with 400 bad request (invalid CRS code)."""
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAS?filterCrs=RDG&timeWindow=60&numRows=10",
                status=400,
            )

            with pytest.raises(InvalidStationError):
                await api_client.get_departure_board("PAS", "RDG")


class TestParseService:
    """Tests for service parsing."""

    async def test_parse_service_on_time(self, api_client):
        """Test parsing an on-time service."""
        service_data = {
            "std": "08:35",
            "etd": "On time",
            "platform": "3",
            "operator": "Great Western Railway",
            "serviceID": "service123",
            "destination": [{"locationName": "Reading"}],
            "subsequentCallingPoints": [
                {
                    "callingPoint": [
                        {"locationName": "Reading", "st": "08:55", "et": "On time"}
                    ]
                }
            ],
        }

        result = api_client._parse_service(service_data)

        assert result["scheduled_departure"] == "08:35"
        assert result["expected_departure"] == "08:35"
        assert result["platform"] == "3"
        assert result["operator"] == "Great Western Railway"
        assert result["status"] == STATUS_ON_TIME
        assert result["is_cancelled"] is False
        assert result["delay_minutes"] == 0
        assert "Reading" in result["calling_points"]

    async def test_parse_service_delayed(self, api_client):
        """Test parsing a delayed service."""
        service_data = {
            "std": "08:50",
            "etd": "09:05",
            "platform": "4",
            "operator": "Great Western Railway",
            "serviceID": "service456",
            "delayReason": "Signalling problems",
            "destination": [{"locationName": "Reading"}],
            "subsequentCallingPoints": [
                {
                    "callingPoint": [
                        {"locationName": "Reading", "st": "09:10", "et": "09:25"}
                    ]
                }
            ],
        }

        result = api_client._parse_service(service_data)

        assert result["scheduled_departure"] == "08:50"
        assert result["expected_departure"] == "09:05"
        assert result["status"] == STATUS_DELAYED
        assert result["delay_minutes"] == 15
        assert result["delay_reason"] == "Signalling problems"
        assert result["is_cancelled"] is False

    async def test_parse_service_cancelled(self, api_client):
        """Test parsing a cancelled service."""
        service_data = {
            "std": "09:05",
            "etd": "Cancelled",
            "platform": "2",
            "operator": "Great Western Railway",
            "serviceID": "service789",
            "cancelReason": "Train crew unavailable",
            "destination": [{"locationName": "Reading"}],
        }

        result = api_client._parse_service(service_data)

        assert result["scheduled_departure"] == "09:05"
        assert result["status"] == STATUS_CANCELLED
        assert result["is_cancelled"] is True
        assert result["cancellation_reason"] == "Train crew unavailable"

    async def test_parse_service_midnight_crossing(self, api_client):
        """Test parsing a service that crosses midnight."""
        service_data = {
            "std": "23:50",
            "etd": "00:05",
            "platform": "1",
            "operator": "Test Operator",
            "serviceID": "service999",
            "destination": [{"locationName": "Test"}],
        }

        result = api_client._parse_service(service_data)

        assert result["scheduled_departure"] == "23:50"
        assert result["expected_departure"] == "00:05"
        assert result["status"] == STATUS_DELAYED
        assert result["delay_minutes"] == 15  # Crosses midnight

    @pytest.mark.parametrize(
        ("std", "etd"),
        [
            ("08:35", "9:05"),        # Single-digit hour
            ("08:35", "abc:de"),      # Non-numeric
            ("08:35", "09:05:30"),    # HH:MM:SS instead of HH:MM
            ("8:35", "09:05"),        # Single-digit hour in std
            ("08:35", "Delayed: 5"),  # Text with colon
        ],
    )
    async def test_parse_service_invalid_time_format_no_delay(self, api_client, std, etd):
        """Test that invalid time formats don't produce a delay calculation."""
        service_data = {
            "std": std,
            "etd": etd,
            "platform": "1",
            "operator": "Test Operator",
            "serviceID": "service_invalid",
            "destination": [{"locationName": "Test"}],
        }

        result = api_client._parse_service(service_data)

        # Delay should remain 0 since the time format is invalid
        assert result["delay_minutes"] == 0


class TestAPIRetryLogic:
    """Tests for API retry logic."""

    async def test_rate_limit_retry(self, api_client):
        """Test retry logic for rate limiting."""
        with aioresponses() as mock:
            # First request: rate limited
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                status=429,
            )
            # Second request: success
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={"GetStationBoardResult": {"locationName": "London Paddington"}},
                status=200,
            )

            result = await api_client.validate_api_key()
            assert result is True

    async def test_rate_limit_max_retries(self, api_client):
        """Test that rate limiting fails after max retries."""
        # Test _request method directly since validate_api_key catches all exceptions
        # and converts them to AuthenticationError
        with aioresponses() as mock:
            # Mock 4 rate limit responses (initial + 3 retries)
            url = f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1"
            for _ in range(4):
                mock.get(url, status=429)

            # Test _request directly to verify retry logic
            with pytest.raises(RateLimitError):
                await api_client._request("GetDepartureBoard/PAD", {"numRows": 1})

    async def test_server_error_retry(self, api_client):
        """Test retry logic for server errors."""
        with aioresponses() as mock:
            # First request: server error
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                status=500,
            )
            # Second request: success
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={"GetStationBoardResult": {"locationName": "London Paddington"}},
                status=200,
            )

            result = await api_client.validate_api_key()
            assert result is True

    async def test_timeout_retry(self, api_client):
        """Test retry logic for timeouts."""
        with aioresponses() as mock:
            # First request: timeout
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                exception=asyncio.TimeoutError(),
            )
            # Second request: success
            mock.get(
                f"{API_BASE_URL}/GetDepartureBoard/PAD?numRows=1",
                payload={"GetStationBoardResult": {"locationName": "London Paddington"}},
                status=200,
            )

            result = await api_client.validate_api_key()
            assert result is True


class TestParseDepartureBoard:
    """Tests for departure board parsing."""

    async def test_parse_departure_board_with_services(
        self, api_client, departure_board_response
    ):
        """Test parsing departure board with multiple services."""
        result = api_client._parse_departure_board(departure_board_response)

        assert result["location_name"] == "London Paddington"
        assert result["destination_name"] == "Reading"
        assert len(result["services"]) == 3

        # Check first service (on time)
        assert result["services"][0]["status"] == STATUS_ON_TIME

        # Check second service (delayed)
        assert result["services"][1]["status"] == STATUS_DELAYED
        assert result["services"][1]["delay_minutes"] == 15

        # Check third service (cancelled)
        assert result["services"][2]["status"] == STATUS_CANCELLED
        assert result["services"][2]["is_cancelled"] is True

    async def test_parse_departure_board_empty(
        self, api_client, empty_departure_board_response
    ):
        """Test parsing empty departure board."""
        result = api_client._parse_departure_board(empty_departure_board_response)

        assert result["location_name"] == "London Paddington"
        assert result["destination_name"] == "Reading"
        assert len(result["services"]) == 0

    async def test_parse_departure_board_alternative_structure(self, api_client):
        """Test parsing departure board with alternative data structure."""
        data = {
            "locationName": "Test Station",
            "filterLocationName": "Test Destination",
            "trainServices": [],
        }

        result = api_client._parse_departure_board(data)

        assert result["location_name"] == "Test Station"
        assert result["destination_name"] == "Test Destination"
        assert len(result["services"]) == 0
