"""Tests for the get_historical_raw_data service action."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.my_rail_commute import (
    SERVICE_GET_HISTORICAL_RAW_DATA,
    async_setup_entry,
)
from custom_components.my_rail_commute.const import DOMAIN


def _make_hass(entry_id="test_entry_id", raw_data=None):
    """Build a minimal hass mock with one commute entry registered."""
    if raw_data is None:
        raw_data = {"2026-05-17": {"on_time_count": 5, "delayed_count": 1}}

    stats_store = MagicMock()
    stats_store.get_raw_data = MagicMock(return_value=raw_data)

    coordinator = MagicMock()
    coordinator.stats_store = stats_store

    hass = MagicMock()
    hass.data = {DOMAIN: {entry_id: coordinator}}
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)

    return hass, coordinator, stats_store


class _FakeServiceCall:
    def __init__(self, entry_id):
        self.data = {"entry_id": entry_id}


@pytest.mark.asyncio
async def test_get_historical_raw_data_returns_days():
    """Service handler returns a 'days' dict with all raw stats."""
    raw_data = {"2026-05-17": {"on_time_count": 10, "delayed_count": 2}}
    hass, coordinator, stats_store = _make_hass(raw_data=raw_data)

    # Simulate calling the handler directly via the registered service
    registered_handlers: dict = {}

    def capture_register(domain, service_name, handler, **kwargs):
        registered_handlers[service_name] = handler

    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock(side_effect=capture_register)

    # Build a minimal entry mock so async_setup_entry can run
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {"api_key": "test_key", "origin": "PAD", "destination": "RDG",
                  "time_window": 60, "num_services": 3, "night_updates": False,
                  "severe_delay_threshold": 15, "major_delay_threshold": 10,
                  "minor_delay_threshold": 3, "departed_train_grace_period": 5}
    entry.options = {}

    with (
        patch("custom_components.my_rail_commute.async_get_clientsession"),
        patch("custom_components.my_rail_commute.NationalRailAPI"),
        patch("custom_components.my_rail_commute.NationalRailDataUpdateCoordinator") as MockCoord,
        patch("custom_components.my_rail_commute.CommuteStatisticsStore") as MockStore,
    ):
        mock_coord_instance = MagicMock()
        mock_coord_instance.stats_store = stats_store
        mock_coord_instance.async_config_entry_first_refresh = AsyncMock()
        MockCoord.return_value = mock_coord_instance

        mock_store_instance = MagicMock()
        mock_store_instance.async_load = AsyncMock()
        mock_store_instance.get_raw_data = MagicMock(return_value=raw_data)
        MockStore.return_value = mock_store_instance

        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.data = {}

        await async_setup_entry(hass, entry)

    assert SERVICE_GET_HISTORICAL_RAW_DATA in registered_handlers
    call = _FakeServiceCall("test_entry_id")
    result = await registered_handlers[SERVICE_GET_HISTORICAL_RAW_DATA](call)
    assert "days" in result
    assert result["days"] == raw_data


@pytest.mark.asyncio
async def test_get_historical_raw_data_invalid_entry_id():
    """Service handler raises ServiceValidationError for unknown entry_id."""
    from homeassistant.exceptions import ServiceValidationError

    hass, _, _ = _make_hass(entry_id="real_entry")

    registered_handlers: dict = {}

    def capture_register(domain, service_name, handler, **kwargs):
        registered_handlers[service_name] = handler

    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock(side_effect=capture_register)

    entry = MagicMock()
    entry.entry_id = "real_entry"
    entry.data = {"api_key": "test_key", "origin": "PAD", "destination": "RDG",
                  "time_window": 60, "num_services": 3, "night_updates": False,
                  "severe_delay_threshold": 15, "major_delay_threshold": 10,
                  "minor_delay_threshold": 3, "departed_train_grace_period": 5}
    entry.options = {}

    with (
        patch("custom_components.my_rail_commute.async_get_clientsession"),
        patch("custom_components.my_rail_commute.NationalRailAPI"),
        patch("custom_components.my_rail_commute.NationalRailDataUpdateCoordinator") as MockCoord,
        patch("custom_components.my_rail_commute.CommuteStatisticsStore") as MockStore,
    ):
        mock_coord_instance = MagicMock()
        mock_coord_instance.async_config_entry_first_refresh = AsyncMock()
        MockCoord.return_value = mock_coord_instance

        mock_store_instance = MagicMock()
        mock_store_instance.async_load = AsyncMock()
        MockStore.return_value = mock_store_instance

        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.data = {}

        await async_setup_entry(hass, entry)

    assert SERVICE_GET_HISTORICAL_RAW_DATA in registered_handlers
    call = _FakeServiceCall("nonexistent_entry_id")
    with pytest.raises(ServiceValidationError):
        await registered_handlers[SERVICE_GET_HISTORICAL_RAW_DATA](call)
