"""Actions returning the raw Rail Data Marketplace response, for debugging."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api import NationalRailAPI, NationalRailAPIError
from .const import (
    CONF_ARRIVAL_API_KEY,
    CONF_SERVICE_DETAILS_API_KEY,
    DOMAIN,
    SERVICE_GET_ARRIVAL_BOARD,
    SERVICE_GET_DEPARTURE_BOARD,
    SERVICE_GET_SERVICE_DETAILS,
)
from .keys import resolve_key

_CRS = vol.All(cv.string, vol.Match(r"^[A-Za-z]{3}$"))
_TIME_WINDOW = vol.All(vol.Coerce(int), vol.Range(min=0, max=120))
_TIME_OFFSET = vol.All(vol.Coerce(int), vol.Range(min=-120, max=120))
_NUM_ROWS = vol.All(vol.Coerce(int), vol.Range(min=0, max=150))

_BOARD_FIELDS = {
    vol.Optional("entry_id"): cv.string,
    vol.Required("crs"): _CRS,
    vol.Optional("filter_crs"): _CRS,
    vol.Optional("time_window"): _TIME_WINDOW,
    vol.Optional("num_rows"): _NUM_ROWS,
}

_ALL_SERVICES = (
    SERVICE_GET_DEPARTURE_BOARD,
    SERVICE_GET_ARRIVAL_BOARD,
    SERVICE_GET_SERVICE_DETAILS,
)


def _resolve_entry_id(hass: HomeAssistant, entry_id: str | None) -> str:
    """Return the commute to use, defaulting to the only one that is set up."""
    loaded = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in loaded:
            raise ServiceValidationError(f"No commute found with entry_id: {entry_id}")
        return entry_id
    if len(loaded) == 1:
        return next(iter(loaded))

    options = []
    for loaded_id in loaded:
        entry = hass.config_entries.async_get_entry(loaded_id)
        options.append(f"{entry.title if entry else 'unknown'} ({loaded_id})")
    raise ServiceValidationError(
        "Choose a commute with entry_id. Available: "
        + (", ".join(options) if options else "none set up")
    )


def _key_for(
    hass: HomeAssistant, entry_id: str, conf_key: str, product_label: str
) -> str:
    """Return the entry's key for a product, falling back to a shared one."""
    entry = hass.config_entries.async_get_entry(entry_id)
    config = {**entry.data, **entry.options} if entry else {}
    if conf_key == CONF_API_KEY:
        key = config.get(CONF_API_KEY)
    else:
        key = resolve_key(hass, conf_key, config, entry_id)
    if not key:
        raise ServiceValidationError(
            f"No API key is configured for the {product_label} product. "
            "Add it under Configure on the integration"
        )
    return key


async def _call(request: Any) -> dict[str, Any]:
    """Await a raw request, turning a connection failure into an HA error."""
    try:
        return await request
    except NationalRailAPIError as err:
        raise HomeAssistantError(str(err)) from err


def async_register_services(hass: HomeAssistant) -> None:
    """Register the raw-response actions (once, domain-wide)."""

    def _api(entry_id: str) -> NationalRailAPI:
        return hass.data[DOMAIN][entry_id].api

    async def _get_departure_board(call: ServiceCall) -> dict[str, Any]:
        entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
        key = _key_for(hass, entry_id, CONF_API_KEY, "departure board")
        return await _call(
            _api(entry_id).get_raw_departure_board(
                call.data["crs"],
                key,
                filter_crs=call.data.get("filter_crs"),
                time_window=call.data.get("time_window"),
                num_rows=call.data.get("num_rows"),
            )
        )

    async def _get_arrival_board(call: ServiceCall) -> dict[str, Any]:
        entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
        key = _key_for(hass, entry_id, CONF_ARRIVAL_API_KEY, "arrival board")
        return await _call(
            _api(entry_id).get_raw_arrival_board(
                call.data["crs"],
                key,
                filter_crs=call.data.get("filter_crs"),
                time_offset=call.data.get("time_offset"),
                time_window=call.data.get("time_window"),
                num_rows=call.data.get("num_rows"),
            )
        )

    async def _get_service_details(call: ServiceCall) -> dict[str, Any]:
        entry_id = _resolve_entry_id(hass, call.data.get("entry_id"))
        key = _key_for(hass, entry_id, CONF_SERVICE_DETAILS_API_KEY, "service details")
        return await _call(
            _api(entry_id).get_raw_service_details(call.data["service_id"], key)
        )

    if hass.services.has_service(DOMAIN, SERVICE_GET_DEPARTURE_BOARD):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEPARTURE_BOARD,
        _get_departure_board,
        schema=vol.Schema(_BOARD_FIELDS),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ARRIVAL_BOARD,
        _get_arrival_board,
        schema=vol.Schema({**_BOARD_FIELDS, vol.Optional("time_offset"): _TIME_OFFSET}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SERVICE_DETAILS,
        _get_service_details,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): cv.string,
                vol.Required("service_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )


def async_remove_services(hass: HomeAssistant) -> None:
    """Remove the raw-response actions."""
    for name in _ALL_SERVICES:
        hass.services.async_remove(DOMAIN, name)
