"""Tests for per-leg sensors on multi-leg journey config entries."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.my_rail_commute.const import (
    CONF_COMMUTE_NAME,
    CONF_DESTINATION,
    CONF_LEGS,
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
)

_TEST_TIME = datetime(2024, 1, 15, 8, 0, 0, tzinfo=dt_util.UTC)


def _leg_response(origin_name: str, destination_name: str, service_id: str) -> dict:
    return {
        "location_name": origin_name,
        "destination_name": destination_name,
        "services": [
            {
                "scheduled_departure": "08:35",
                "expected_departure": "08:35",
                "platform": "3",
                "operator": "Great Western Railway",
                "service_id": service_id,
                "calling_points": [destination_name],
                "delay_minutes": 0,
                "status": "on_time",
                "is_cancelled": False,
                "cancellation_reason": None,
                "delay_reason": None,
                "scheduled_arrival": "08:55",
                "estimated_arrival": "08:55",
                "destination": destination_name,
            }
        ],
        "generated_at": "2024-01-15T08:30:00",
        "nrcc_messages": [],
    }


@pytest.fixture(name="multi_leg_entry")
def multi_leg_entry_fixture() -> MockConfigEntry:
    """Return a mock multi-leg config entry (PAD -> RDG -> OXF)."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_KEY: "test_api_key_12345",
            CONF_ORIGIN: "PAD",
            CONF_DESTINATION: "OXF",
            CONF_LEGS: legs,
            CONF_COMMUTE_NAME: "Test Journey",
            CONF_TIME_WINDOW: 60,
            CONF_NUM_SERVICES: 2,
            CONF_NIGHT_UPDATES: True,
            CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
            CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
            CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
        },
        unique_id="PAD_RDG_OXF",
    )


async def test_multi_leg_entry_creates_per_leg_sensors(
    hass: HomeAssistant, multi_leg_entry, mock_api_client
) -> None:
    """A multi-leg entry creates leg-scoped sensors instead of the flat train range."""
    mock_api_client.get_departure_board = AsyncMock(
        side_effect=[
            _leg_response("Paddington", "Reading", "svc-leg1"),
            _leg_response("Reading", "Oxford", "svc-leg2"),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        multi_leg_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(multi_leg_entry.entry_id)
        await hass.async_block_till_done()

    # Overall (combined) sensors still exist under the historical names
    assert hass.states.get("sensor.test_journey_summary") is not None
    assert hass.states.get("sensor.test_journey_status") is not None

    # Per-leg sensors exist for both legs, using the new leg-scoped naming
    leg1_train1 = hass.states.get("sensor.test_journey_leg_1_train_1")
    leg2_train1 = hass.states.get("sensor.test_journey_leg_2_train_1")
    assert leg1_train1 is not None
    assert leg2_train1 is not None
    assert leg1_train1.attributes["service_id"] == "svc-leg1"
    assert leg2_train1.attributes["service_id"] == "svc-leg2"

    assert hass.states.get("sensor.test_journey_leg_1_summary") is not None
    assert hass.states.get("sensor.test_journey_leg_1_status") is not None
    assert hass.states.get("sensor.test_journey_leg_1_next_train") is not None
    assert hass.states.get("sensor.test_journey_leg_2_summary") is not None
    assert hass.states.get("sensor.test_journey_leg_2_status") is not None
    assert hass.states.get("sensor.test_journey_leg_2_next_train") is not None

    # The flat single-leg sensors must NOT be created for a multi-leg entry
    assert hass.states.get("sensor.test_journey_next_train") is None
    assert hass.states.get("sensor.test_journey_train_1") is None


async def test_multi_leg_overall_status_reflects_worst_leg(
    hass: HomeAssistant, multi_leg_entry, mock_api_client
) -> None:
    """The combined Status sensor reflects the worst status across legs."""
    delayed_leg2 = _leg_response("Reading", "Oxford", "svc-leg2")
    delayed_leg2["services"][0]["expected_departure"] = "09:15"
    delayed_leg2["services"][0]["delay_minutes"] = 20
    delayed_leg2["services"][0]["status"] = "delayed"

    mock_api_client.get_departure_board = AsyncMock(
        side_effect=[
            _leg_response("Paddington", "Reading", "svc-leg1"),
            delayed_leg2,
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        multi_leg_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(multi_leg_entry.entry_id)
        await hass.async_block_till_done()

    overall_status = hass.states.get("sensor.test_journey_status")
    leg1_status = hass.states.get("sensor.test_journey_leg_1_status")
    leg2_status = hass.states.get("sensor.test_journey_leg_2_status")

    assert leg1_status.state == "Normal"
    assert leg2_status.state == "Severe Disruption"
    assert overall_status.state == "Severe Disruption"


async def test_multi_leg_device_id_uses_full_chain(
    hass: HomeAssistant, multi_leg_entry, mock_api_client
) -> None:
    """The device identifier embeds the full station chain, not just endpoints."""
    from homeassistant.helpers import device_registry as dr

    mock_api_client.get_departure_board = AsyncMock(
        side_effect=[
            _leg_response("Paddington", "Reading", "svc-leg1"),
            _leg_response("Reading", "Oxford", "svc-leg2"),
        ]
    )

    multi_leg_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(multi_leg_entry.entry_id)
    await hass.async_block_till_done()

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, "PAD_RDG_OXF")})
    assert device is not None
    assert device.name == "Test Journey"
