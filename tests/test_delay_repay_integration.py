"""Integration-level tests for Delay Repay: options flow, setup and services."""

from __future__ import annotations

from datetime import timedelta

from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
import pytest

from custom_components.my_rail_commute.const import (
    CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    CONF_DELAY_REPAY_ENABLED,
    CONF_DELAY_REPAY_OPERATORS,
    CONF_DELAY_REPAY_THRESHOLDS,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DOMAIN,
    SERVICE_DISMISS_DELAY_REPAY,
    SERVICE_GET_DELAY_REPAY_CLAIMS,
    SERVICE_MARK_DELAY_REPAY_CLAIMED,
)

from .delay_repay_helpers import NOW, fake_storage, late_service

ENTITY_CLAIMS = "sensor.test_commute_delay_repay_claims"
ENTITY_ELIGIBLE = "binary_sensor.test_commute_delay_repay_eligible"
KEY = "2026-10-08|1|9494208WHYTELF"

_BASE_OPTIONS = {
    CONF_TIME_WINDOW: 60,
    CONF_NUM_SERVICES: 3,
    CONF_NIGHT_UPDATES: True,
    CONF_SEVERE_DELAY_THRESHOLD: 15,
    CONF_MAJOR_DELAY_THRESHOLD: 10,
    CONF_MINOR_DELAY_THRESHOLD: 3,
}


class TestOptionsFlow:
    """Delay Repay fields in the options flow."""

    async def test_defaults_when_not_supplied(
        self, hass: HomeAssistant, mock_config_entry
    ):
        mock_config_entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=dict(_BASE_OPTIONS)
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_DELAY_REPAY_ENABLED] is False
        assert result["data"][CONF_DELAY_REPAY_THRESHOLDS] == "15,30,60,120"
        assert result["data"][CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS] == 28

    async def test_saves_valid_schemes(self, hass: HomeAssistant, mock_config_entry):
        mock_config_entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                **_BASE_OPTIONS,
                CONF_DELAY_REPAY_ENABLED: True,
                CONF_DELAY_REPAY_THRESHOLDS: "30,60,120",
                CONF_DELAY_REPAY_OPERATORS: "Southern = 15,30 | https://example.com/c",
                CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS: 14,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_DELAY_REPAY_ENABLED] is True
        assert result["data"][CONF_DELAY_REPAY_THRESHOLDS] == "30,60,120"
        assert result["data"][CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS] == 14

    @pytest.mark.parametrize(
        ("thresholds", "operators"),
        [("fifteen", ""), ("15,30", "Southern"), ("15,30", "Southern = x")],
    )
    async def test_rejects_invalid_schemes_when_enabled(
        self, hass: HomeAssistant, mock_config_entry, thresholds, operators
    ):
        mock_config_entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                **_BASE_OPTIONS,
                CONF_DELAY_REPAY_ENABLED: True,
                CONF_DELAY_REPAY_THRESHOLDS: thresholds,
                CONF_DELAY_REPAY_OPERATORS: operators,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_delay_repay_schemes"}

    async def test_invalid_schemes_are_ignored_when_disabled(
        self, hass: HomeAssistant, mock_config_entry
    ):
        mock_config_entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                **_BASE_OPTIONS,
                CONF_DELAY_REPAY_ENABLED: False,
                CONF_DELAY_REPAY_THRESHOLDS: "fifteen",
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


def _board(services):
    return {
        "location_name": "Whyteleafe",
        "destination_name": "London Bridge",
        "services": services,
        "generated_at": "2026-10-08T22:30:00",
        "nrcc_messages": [],
    }


async def _setup(hass, entry, mock_api_client, freezer, services, **options):
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(NOW)
    mock_api_client.get_departure_board.return_value = _board(services)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_disabled_by_default(hass, mock_config_entry, mock_api_client, freezer):
    with fake_storage():
        mock_config_entry.add_to_hass(hass)
        await hass.config.async_set_time_zone("UTC")
        freezer.move_to(NOW)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
        assert coordinator.delay_repay is None
        assert hass.states.get(ENTITY_CLAIMS) is None
        assert hass.states.get(ENTITY_ELIGIBLE) is None
        assert not hass.services.has_service(DOMAIN, SERVICE_GET_DELAY_REPAY_CLAIMS)


async def test_enabled_end_to_end(hass, mock_config_entry, mock_api_client, freezer):
    with fake_storage():
        coordinator = await _setup(
            hass,
            mock_config_entry,
            mock_api_client,
            freezer,
            [late_service()],
            **{CONF_DELAY_REPAY_ENABLED: True},
        )
        assert coordinator.delay_repay is not None
        for name in (
            SERVICE_MARK_DELAY_REPAY_CLAIMED,
            SERVICE_DISMISS_DELAY_REPAY,
            SERVICE_GET_DELAY_REPAY_CLAIMS,
        ):
            assert hass.services.has_service(DOMAIN, name)

        # The late train is live: today's binary sensor is on, nothing claimable yet
        assert hass.states.get(ENTITY_ELIGIBLE).state == "on"
        assert hass.states.get(ENTITY_CLAIMS).state == "0"

        # Train has gone from the board and its arrival time has passed
        freezer.move_to(NOW + timedelta(minutes=40))
        mock_api_client.get_departure_board.return_value = _board([])
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(ENTITY_CLAIMS)
        assert state.state == "1"
        assert state.attributes["claims"][0]["key"] == KEY
        assert state.attributes["claims"][0]["delay_minutes"] == 17

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_DELAY_REPAY_CLAIMS,
            {"entry_id": mock_config_entry.entry_id},
            blocking=True,
            return_response=True,
        )
        assert [c["key"] for c in result["claims"]] == [KEY]

        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_DELAY_REPAY_CLAIMED,
            {"entry_id": mock_config_entry.entry_id, "journeys": [KEY]},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(ENTITY_CLAIMS).state == "0"


async def test_services_removed_when_last_entry_unloads(
    hass, mock_config_entry, mock_api_client, freezer
):
    with fake_storage():
        await _setup(
            hass,
            mock_config_entry,
            mock_api_client,
            freezer,
            [],
            **{CONF_DELAY_REPAY_ENABLED: True},
        )
        assert hass.services.has_service(DOMAIN, SERVICE_GET_DELAY_REPAY_CLAIMS)
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert not hass.services.has_service(DOMAIN, SERVICE_GET_DELAY_REPAY_CLAIMS)


async def test_tracker_failure_does_not_break_updates(
    hass, mock_config_entry, mock_api_client, freezer, caplog
):
    with fake_storage():
        coordinator = await _setup(
            hass,
            mock_config_entry,
            mock_api_client,
            freezer,
            [late_service()],
            **{CONF_DELAY_REPAY_ENABLED: True},
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        coordinator.delay_repay.async_observe = _boom
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert "Delay Repay tracking failed" in caplog.text


async def test_invalid_stored_schemes_fall_back_to_defaults(
    hass, mock_config_entry, mock_api_client, freezer, caplog
):
    with fake_storage():
        coordinator = await _setup(
            hass,
            mock_config_entry,
            mock_api_client,
            freezer,
            [],
            **{
                CONF_DELAY_REPAY_ENABLED: True,
                CONF_DELAY_REPAY_THRESHOLDS: "garbage",
            },
        )
        assert coordinator.delay_repay.schemes.default.thresholds == (15, 30, 60, 120)
        assert "using defaults" in caplog.text
