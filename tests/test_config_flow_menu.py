"""Tests for the grouped setup pages and the options menu with a final Save."""

from __future__ import annotations

from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.my_rail_commute.const import (
    CONF_DELAY_REPAY_ENABLED,
    CONF_DELAY_REPAY_THRESHOLDS,
    CONF_FAVOURITE_TRAINS,
    CONF_NIGHT_UPDATES,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DOMAIN,
)

from .config_flow_helpers import open_options_page


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return result["flow_id"]


async def _configure(hass: HomeAssistant, flow_id: str, user_input: dict):
    return await hass.config_entries.options.async_configure(
        flow_id, user_input=user_input
    )


async def test_nothing_is_stored_until_save(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Edits to a page stay in the draft; the entry only changes on Save."""
    flow_id = await _start(hass, mock_config_entry)

    result = await open_options_page(hass, flow_id, "trains")
    result = await _configure(hass, flow_id, {CONF_TIME_WINDOW: 90})
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert CONF_TIME_WINDOW not in mock_config_entry.options

    result = await open_options_page(hass, flow_id, "save")
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TIME_WINDOW] == 90


async def test_pages_show_unsaved_edits_when_reopened(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """A page reopened before Save defaults to what was just entered."""
    flow_id = await _start(hass, mock_config_entry)

    await open_options_page(hass, flow_id, "trains")
    await _configure(hass, flow_id, {CONF_TIME_WINDOW: 90})
    result = await open_options_page(hass, flow_id, "trains")

    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_TIME_WINDOW] == 90


async def test_save_keeps_options_from_other_pages(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Saving merges the draft over the stored options rather than replacing."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_FAVOURITE_TRAINS: "07:42"}
    )
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    flow_id = result["flow_id"]

    await open_options_page(hass, flow_id, "disruption")
    await _configure(hass, flow_id, {CONF_NIGHT_UPDATES: False})
    result = await open_options_page(hass, flow_id, "save")

    assert result["data"][CONF_FAVOURITE_TRAINS] == "07:42"
    assert result["data"][CONF_NIGHT_UPDATES] is False


async def test_favourites_set_at_setup_prefill_the_options_page(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Favourites stored in the setup data are not blanked by the options page."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_FAVOURITE_TRAINS: "08:15"},
    )
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await open_options_page(hass, result["flow_id"], "trains")

    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_FAVOURITE_TRAINS] == "08:15"


async def test_bad_page_input_keeps_the_page_open(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Invalid thresholds re-show the page and do not reach the draft."""
    flow_id = await _start(hass, mock_config_entry)

    await open_options_page(hass, flow_id, "disruption")
    result = await _configure(
        hass,
        flow_id,
        {
            CONF_SEVERE_DELAY_THRESHOLD: 2,
            "major_delay_threshold": 10,
            "minor_delay_threshold": 5,
        },
    )
    assert result["step_id"] == "disruption"
    assert result["errors"] == {"base": "invalid_thresholds"}


async def test_delay_repay_off_skips_details_and_keeps_stored_values(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Switching Delay Repay off returns to the menu and keeps its details."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_DELAY_REPAY_ENABLED: True,
            CONF_DELAY_REPAY_THRESHOLDS: "30,60",
        },
    )
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    flow_id = result["flow_id"]

    result = await open_options_page(hass, flow_id, "delay_repay")
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_DELAY_REPAY_ENABLED] is True

    result = await _configure(hass, flow_id, {CONF_DELAY_REPAY_ENABLED: False})
    assert result["type"] == data_entry_flow.FlowResultType.MENU

    result = await open_options_page(hass, flow_id, "save")
    assert result["data"][CONF_DELAY_REPAY_ENABLED] is False
    assert result["data"][CONF_DELAY_REPAY_THRESHOLDS] == "30,60"


async def test_delay_repay_on_opens_details(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Switching Delay Repay on opens the details page, then returns to the menu."""
    flow_id = await _start(hass, mock_config_entry)

    await open_options_page(hass, flow_id, "delay_repay")
    result = await _configure(hass, flow_id, {CONF_DELAY_REPAY_ENABLED: True})
    assert result["step_id"] == "delay_repay_details"

    result = await _configure(hass, flow_id, {CONF_DELAY_REPAY_THRESHOLDS: "30,60"})
    assert result["type"] == data_entry_flow.FlowResultType.MENU

    result = await open_options_page(hass, flow_id, "save")
    assert result["data"][CONF_DELAY_REPAY_ENABLED] is True
    assert result["data"][CONF_DELAY_REPAY_THRESHOLDS] == "30,60"


async def test_closing_without_save_discards_changes(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Abandoning the flow leaves the entry's options untouched."""
    flow_id = await _start(hass, mock_config_entry)

    await open_options_page(hass, flow_id, "trains")
    await _configure(hass, flow_id, {CONF_TIME_WINDOW: 90})
    hass.config_entries.options.async_abort(flow_id)

    assert dict(mock_config_entry.options) == {}
    assert DOMAIN in {e.domain for e in hass.config_entries.async_entries()}
