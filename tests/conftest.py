"""Fixtures for My Rail Commute tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.const import (
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ORIGIN,
    CONF_TIME_WINDOW,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture(name="mock_config_entry")
def mock_config_entry_fixture() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_KEY: "test_api_key_12345",
            CONF_ORIGIN: "PAD",
            CONF_DESTINATION: "RDG",
            CONF_COMMUTE_NAME: "Test Commute",
            CONF_TIME_WINDOW: 60,
            CONF_NUM_SERVICES: 3,
            CONF_NIGHT_UPDATES: True,
        },
        unique_id="PAD_RDG",
    )


@pytest.fixture(name="mock_api_client")
def mock_api_client_fixture() -> Generator[AsyncMock]:
    """Return a mocked API client."""
    with patch(
        "custom_components.my_rail_commute.NationalRailAPI",
        autospec=True,
    ) as mock_api:
        client = mock_api.return_value
        client.validate_api_key = AsyncMock(return_value=True)
        client.validate_station = AsyncMock(return_value="Test Station")
        client.get_departure_board = AsyncMock(
            return_value={
                "location_name": "London Paddington",
                "destination_name": "Reading",
                "services": [],
                "generated_at": "2024-01-01T12:00:00",
                "nrcc_messages": [],
            }
        )
        yield client


@pytest.fixture(scope="function")
async def aiohttp_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Create a real aiohttp session for testing."""
    # Create a new session for each test
    session = aiohttp.ClientSession()
    try:
        yield session
    finally:
        # Ensure proper cleanup even if test fails
        if not session.closed:
            await session.close()


def load_fixture(filename: str) -> str:
    """Load a fixture file."""
    path = Path(__file__).parent / "fixtures" / filename
    return path.read_text()


def load_json_fixture(filename: str) -> dict:
    """Load a JSON fixture file."""
    return json.loads(load_fixture(filename))


@pytest.fixture(name="departure_board_response")
def departure_board_response_fixture() -> dict:
    """Return a sample departure board API response."""
    return {
        "GetStationBoardResult": {
            "generatedAt": "2024-01-15T08:30:00",
            "locationName": "London Paddington",
            "crs": "PAD",
            "filterLocationName": "Reading",
            "filtercrs": "RDG",
            "trainServices": {
                "service": [
                    {
                        "std": "08:35",
                        "etd": "On time",
                        "platform": "3",
                        "operator": "Great Western Railway",
                        "serviceID": "service123",
                        "destination": [
                            {
                                "locationName": "Reading",
                                "crs": "RDG",
                            }
                        ],
                        "subsequentCallingPoints": [
                            {
                                "callingPoint": [
                                    {
                                        "locationName": "Slough",
                                        "crs": "SLO",
                                        "st": "08:47",
                                        "et": "On time",
                                    },
                                    {
                                        "locationName": "Reading",
                                        "crs": "RDG",
                                        "st": "08:55",
                                        "et": "On time",
                                    },
                                ]
                            }
                        ],
                    },
                    {
                        "std": "08:50",
                        "etd": "09:05",
                        "platform": "4",
                        "operator": "Great Western Railway",
                        "serviceID": "service456",
                        "delayReason": "Signalling problems",
                        "destination": [
                            {
                                "locationName": "Reading",
                                "crs": "RDG",
                            }
                        ],
                        "subsequentCallingPoints": [
                            {
                                "callingPoint": [
                                    {
                                        "locationName": "Reading",
                                        "crs": "RDG",
                                        "st": "09:10",
                                        "et": "09:25",
                                    },
                                ]
                            }
                        ],
                    },
                    {
                        "std": "09:05",
                        "etd": "Cancelled",
                        "platform": "2",
                        "operator": "Great Western Railway",
                        "serviceID": "service789",
                        "cancelReason": "Train crew unavailable",
                        "destination": [
                            {
                                "locationName": "Reading",
                                "crs": "RDG",
                            }
                        ],
                    },
                ]
            },
        }
    }


@pytest.fixture(name="station_validation_response")
def station_validation_response_fixture() -> dict:
    """Return a sample station validation API response."""
    return {
        "GetStationBoardResult": {
            "generatedAt": "2024-01-15T08:30:00",
            "locationName": "London Paddington",
            "crs": "PAD",
            "trainServices": [],
        }
    }


@pytest.fixture(name="empty_departure_board_response")
def empty_departure_board_response_fixture() -> dict:
    """Return an empty departure board API response."""
    return {
        "GetStationBoardResult": {
            "generatedAt": "2024-01-15T08:30:00",
            "locationName": "London Paddington",
            "crs": "PAD",
            "filterLocationName": "Reading",
            "filtercrs": "RDG",
            "trainServices": [],
        }
    }
