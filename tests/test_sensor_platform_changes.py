"""Tests for platform change detection in sensors."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.my_rail_commute.const import DOMAIN
from custom_components.my_rail_commute.sensor import TrainSensor


async def test_train_sensor_no_platform_change_for_different_service(
    hass: HomeAssistant,
    mock_config_entry,
    mock_api_client,
) -> None:
    """Test that platform changes are NOT flagged when service changes."""
    # Freeze time to before the scheduled departure (08:00)
    test_time = datetime(2024, 1, 15, 8, 0, 0, tzinfo=dt_util.UTC)

    with patch("custom_components.my_rail_commute.coordinator.dt_util.now", return_value=test_time):
        # Initial response
        mock_api_client.get_departure_board.return_value = {
            "location_name": "London Paddington",
            "destination_name": "Reading",
            "services": [
                {
                    "scheduled_departure": "08:35",
                    "expected_departure": "08:35",
                    "platform": "3",
                    "operator": "Great Western Railway",
                    "service_id": "service123",
                    "calling_points": ["Slough", "Reading"],
                    "delay_minutes": 0,
                    "status": "on_time",
                    "is_cancelled": False,
                    "cancellation_reason": "",
                    "delay_reason": "",
                    "scheduled_arrival": "08:55",
                    "estimated_arrival": "08:55",
                    "destination": "Reading",
                }
            ],
            "generated_at": "2024-01-15T08:30:00",
            "nrcc_messages": [],
        }

        # Set up the integration
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify initial state
        train_1_state = hass.states.get("sensor.test_commute_train_1")
        assert train_1_state.attributes["platform"] == "3"
        assert train_1_state.attributes["platform_changed"] is False

        # Simulate different service (train has departed, next train takes its place)
        mock_api_client.get_departure_board.return_value = {
            "location_name": "London Paddington",
            "destination_name": "Reading",
            "services": [
                {
                    "scheduled_departure": "08:50",
                    "expected_departure": "08:50",
                    "platform": "4",  # Different platform
                    "operator": "Great Western Railway",
                    "service_id": "service456",  # Different service
                    "calling_points": ["Reading"],
                    "delay_minutes": 0,
                    "status": "on_time",
                    "is_cancelled": False,
                    "cancellation_reason": "",
                    "delay_reason": "",
                    "scheduled_arrival": "09:10",
                    "estimated_arrival": "09:10",
                    "destination": "Reading",
                }
            ],
            "generated_at": "2024-01-15T08:36:00",
            "nrcc_messages": [],
        }

        # Get the coordinator and trigger a manual refresh
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # Platform change should NOT be flagged (different service)
        train_1_state = hass.states.get("sensor.test_commute_train_1")
        assert train_1_state.attributes["platform"] == "4"
        assert train_1_state.attributes["platform_changed"] is False
        assert train_1_state.attributes["previous_platform"] is None


def test_platform_change_detection_unit():
    """Unit test for platform change detection logic."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Create the sensor
    sensor = TrainSensor(mock_coordinator, mock_entry, train_number=1)

    # Mock the hass attribute to prevent "Attribute hass is None" error
    sensor.hass = MagicMock()

    # Test 1: Initial update with platform "3"
    mock_coordinator.data = {
        "services": [
            {
                "platform": "3",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == "3"
    assert sensor._platform_changed is False

    # Test 2: Same service, platform changed from "3" to "5"
    mock_coordinator.data = {
        "services": [
            {
                "platform": "5",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == "3"  # Should NOT update - preserved
    assert sensor._platform_changed is True

    # Test 3: Same service, platform changed again from "5" to "7"
    mock_coordinator.data = {
        "services": [
            {
                "platform": "7",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == "3"  # Still the original
    assert sensor._platform_changed is True

    # Test 4: Different service - should reset
    mock_coordinator.data = {
        "services": [
            {
                "platform": "4",
                "service_id": "service456",
                "scheduled_departure": "08:50",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service456"
    assert sensor._previous_platform == "4"
    assert sensor._platform_changed is False


def test_platform_change_from_tba_unit():
    """Unit test for platform assignment from TBA."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Create the sensor
    sensor = TrainSensor(mock_coordinator, mock_entry, train_number=1)

    # Mock the hass attribute to prevent "Attribute hass is None" error
    sensor.hass = MagicMock()

    # Test 1: Initial update with empty platform (TBA)
    mock_coordinator.data = {
        "services": [
            {
                "platform": "",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == ""
    assert sensor._platform_changed is False

    # Test 2: Same service, platform assigned from "" to "3"
    mock_coordinator.data = {
        "services": [
            {
                "platform": "3",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == ""  # Preserved original (empty)
    assert sensor._platform_changed is True


def test_platform_change_no_service_id():
    """Unit test for handling missing service_id."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Create the sensor
    sensor = TrainSensor(mock_coordinator, mock_entry, train_number=1)

    # Mock the hass attribute to prevent "Attribute hass is None" error
    sensor.hass = MagicMock()

    # Test: Update with missing service_id
    mock_coordinator.data = {
        "services": [
            {
                "platform": "3",
                "service_id": None,
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    # Should still set platform but not track service
    assert sensor._previous_platform == "3"
    assert sensor._current_service_id is None
    assert sensor._platform_changed is False


def test_platform_no_change_same_service():
    """Unit test for no platform change with same service."""
    # Create a mock coordinator
    mock_coordinator = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry"

    # Create the sensor
    sensor = TrainSensor(mock_coordinator, mock_entry, train_number=1)

    # Mock the hass attribute to prevent "Attribute hass is None" error
    sensor.hass = MagicMock()

    # Test 1: Initial update
    mock_coordinator.data = {
        "services": [
            {
                "platform": "3",
                "service_id": "service123",
                "scheduled_departure": "08:35",
            }
        ]
    }
    sensor._handle_coordinator_update()

    assert sensor._platform_changed is False

    # Test 2: Same service, same platform - should remain False
    sensor._handle_coordinator_update()

    assert sensor._current_service_id == "service123"
    assert sensor._previous_platform == "3"
    assert sensor._platform_changed is False
