"""Helpers for driving the setup flow's grouped settings pages in tests."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.const import (
    CONF_ARRIVAL_API_KEY,
    CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    CONF_DELAY_REPAY_ENABLED,
    CONF_DELAY_REPAY_OPERATORS,
    CONF_DELAY_REPAY_THRESHOLDS,
    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
    CONF_FAVOURITE_TRAINS,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MIN_CONNECTION_TIME,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ONLY_CATCHABLE_SERVICES,
    CONF_OPERATOR_FILTER,
    CONF_SERVICE_DETAILS_API_KEY,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
)

DISRUPTION_KEYS = {
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_DEPARTED_TRAIN_GRACE_PERIOD,
    CONF_NIGHT_UPDATES,
}


async def submit_setup_settings(
    hass: HomeAssistant, flow_id: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Submit the trains, disruption and Delay Repay toggle pages in order.

    ``user_input`` may mix fields from all three pages. Stops early (returning
    the form with its errors) if a page is rejected, otherwise returns the
    result after the Delay Repay toggle page, i.e. the details form when it was
    enabled, or whatever follows the settings pages when it was not.
    """
    user_input = dict(user_input)
    enabled = user_input.pop(CONF_DELAY_REPAY_ENABLED, False)
    disruption = {k: user_input.pop(k) for k in DISRUPTION_KEYS & user_input.keys()}

    result = await hass.config_entries.flow.async_configure(
        flow_id, user_input=user_input
    )
    if result.get("step_id") != "disruption" or result.get("errors"):
        return result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=disruption
    )
    if result.get("step_id") != "delay_repay":
        return result
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_DELAY_REPAY_ENABLED: enabled}
    )


TRAINS_KEYS = {
    CONF_FAVOURITE_TRAINS,
    CONF_OPERATOR_FILTER,
    CONF_NUM_SERVICES,
    CONF_TIME_WINDOW,
    CONF_MIN_CONNECTION_TIME,
    CONF_ONLY_CATCHABLE_SERVICES,
}

DELAY_REPAY_DETAIL_KEYS = {
    CONF_DELAY_REPAY_THRESHOLDS,
    CONF_DELAY_REPAY_OPERATORS,
    CONF_DELAY_REPAY_CLAIM_WINDOW_DAYS,
    CONF_ARRIVAL_API_KEY,
    CONF_SERVICE_DETAILS_API_KEY,
}


async def open_options_page(hass: HomeAssistant, flow_id: str, page: str) -> dict[str, Any]:
    """Choose a page from the options menu."""
    return await hass.config_entries.options.async_configure(
        flow_id, user_input={"next_step_id": page}
    )


async def submit_options(
    hass: HomeAssistant, flow_id: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Fill in the options pages for the fields given, then Save.

    ``user_input`` may mix fields from every page, as the old single form did.
    A page is only visited when it has fields. Returns the page's form (with its
    errors) if one is rejected, otherwise the result of Save.
    """
    user_input = dict(user_input)
    toggle = user_input.pop(CONF_DELAY_REPAY_ENABLED, None)
    pages = {
        "trains": {k: user_input.pop(k) for k in TRAINS_KEYS & user_input.keys()},
        "disruption": {
            k: user_input.pop(k) for k in DISRUPTION_KEYS & user_input.keys()
        },
    }
    details = {
        k: user_input.pop(k) for k in DELAY_REPAY_DETAIL_KEYS & user_input.keys()
    }
    assert not user_input, f"unrouted options fields: {sorted(user_input)}"

    for page, fields in pages.items():
        if not fields:
            continue
        result = await open_options_page(hass, flow_id, page)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=fields
        )
        if result["step_id"] != "init":
            return result

    if toggle is not None:
        result = await open_options_page(hass, flow_id, "delay_repay")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_DELAY_REPAY_ENABLED: toggle}
        )
        if toggle:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], user_input=details
            )
        if result["step_id"] != "init":
            return result

    result = await open_options_page(hass, flow_id, "save")
    return result
