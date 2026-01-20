"""Tests for platform change detection in sensors."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.const import DOMAIN


async def test_train_sensor_platform_change_detection(
    hass: HomeAssistant,
    mock_config_entry,
    mock_api_client,
) -> None:
    """Test that platform changes are detected for individual train sensors."""
    # Initial response with platform "3"
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

    # Get train 1 sensor
    train_1_state = hass.states.get("sensor.test_commute_train_1")
    assert train_1_state is not None
    assert train_1_state.attributes["platform"] == "3"
    assert train_1_state.attributes["platform_changed"] is False
    assert train_1_state.attributes["previous_platform"] is None

    # Get next train sensor (mirrors train 1)
    next_train_state = hass.states.get("sensor.test_commute_next_train")
    assert next_train_state is not None
    assert next_train_state.attributes["platform"] == "3"
    assert next_train_state.attributes["platform_changed"] is False
    assert next_train_state.attributes["previous_platform"] is None

    # Simulate platform change - same service, different platform
    mock_api_client.get_departure_board.return_value = {
        "location_name": "London Paddington",
        "destination_name": "Reading",
        "services": [
            {
                "scheduled_departure": "08:35",
                "expected_departure": "08:35",
                "platform": "5",  # Platform changed from 3 to 5
                "operator": "Great Western Railway",
                "service_id": "service123",  # Same service
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
        "generated_at": "2024-01-15T08:31:00",
        "nrcc_messages": [],
    }

    # Get the coordinator and trigger a manual refresh
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Check that platform change was detected
    train_1_state = hass.states.get("sensor.test_commute_train_1")
    assert train_1_state.attributes["platform"] == "5"
    assert train_1_state.attributes["platform_changed"] is True
    assert train_1_state.attributes["previous_platform"] == "3"

    # Check next train sensor also detected the change
    next_train_state = hass.states.get("sensor.test_commute_next_train")
    assert next_train_state.attributes["platform"] == "5"
    assert next_train_state.attributes["platform_changed"] is True
    assert next_train_state.attributes["previous_platform"] == "3"


async def test_train_sensor_no_platform_change_for_different_service(
    hass: HomeAssistant,
    mock_config_entry,
    mock_api_client,
) -> None:
    """Test that platform changes are NOT flagged when service changes."""
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


async def test_train_sensor_platform_change_from_tba(
    hass: HomeAssistant,
    mock_config_entry,
    mock_api_client,
) -> None:
    """Test that platform assignment from TBA is detected as a change."""
    # Initial response with empty platform (TBA)
    mock_api_client.get_departure_board.return_value = {
        "location_name": "London Paddington",
        "destination_name": "Reading",
        "services": [
            {
                "scheduled_departure": "08:35",
                "expected_departure": "08:35",
                "platform": "",  # Platform TBA
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
    assert train_1_state.attributes["platform"] == "TBA"
    assert train_1_state.attributes["platform_changed"] is False

    # Platform is now assigned
    mock_api_client.get_departure_board.return_value = {
        "location_name": "London Paddington",
        "destination_name": "Reading",
        "services": [
            {
                "scheduled_departure": "08:35",
                "expected_departure": "08:35",
                "platform": "3",  # Platform assigned
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
        "generated_at": "2024-01-15T08:31:00",
        "nrcc_messages": [],
    }

    # Get the coordinator and trigger a manual refresh
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Platform assignment should be detected as a change
    train_1_state = hass.states.get("sensor.test_commute_train_1")
    assert train_1_state.attributes["platform"] == "3"
    assert train_1_state.attributes["platform_changed"] is True
    assert train_1_state.attributes["previous_platform"] == ""


async def test_train_sensor_multiple_platform_changes(
    hass: HomeAssistant,
    mock_config_entry,
    mock_api_client,
) -> None:
    """Test that multiple platform changes are tracked correctly."""
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

    # Get the coordinator
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]

    # First platform change: 3 -> 5
    mock_api_client.get_departure_board.return_value["services"][0]["platform"] = "5"
    mock_api_client.get_departure_board.return_value["generated_at"] = "2024-01-15T08:31:00"
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    train_1_state = hass.states.get("sensor.test_commute_train_1")
    assert train_1_state.attributes["platform"] == "5"
    assert train_1_state.attributes["platform_changed"] is True
    assert train_1_state.attributes["previous_platform"] == "3"

    # Second platform change: 5 -> 7
    mock_api_client.get_departure_board.return_value["services"][0]["platform"] = "7"
    mock_api_client.get_departure_board.return_value["generated_at"] = "2024-01-15T08:32:00"
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    train_1_state = hass.states.get("sensor.test_commute_train_1")
    assert train_1_state.attributes["platform"] == "7"
    assert train_1_state.attributes["platform_changed"] is True
    # Previous platform should still be from the first change (3)
    assert train_1_state.attributes["previous_platform"] == "3"
