"""Tests for operator filtering."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant import data_entry_flow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.my_rail_commute.api import NationalRailAPI
from custom_components.my_rail_commute.const import (
    CONF_DESTINATION,
    CONF_LEGS,
    CONF_MAJOR_DELAY_THRESHOLD,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_OPERATOR_FILTER,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    OPERATOR_FILTER_MIN_ROWS,
)
from custom_components.my_rail_commute.coordinator import (
    NationalRailDataUpdateCoordinator,
)
from custom_components.my_rail_commute.operators import (
    KNOWN_OPERATORS,
    clean_operator_filter,
    operator_options,
    service_matches_operators,
)

from .config_flow_helpers import open_options_page

_TEST_TIME = datetime(2024, 1, 15, 8, 0, 0, tzinfo=dt_util.UTC)


def _svc(sid: str, code: str, name: str, departure: str = "09:00") -> dict:
    return {
        "scheduled_departure": departure,
        "expected_departure": departure,
        "platform": "1",
        "operator": name,
        "operator_code": code,
        "service_id": sid,
        "calling_points": [],
        "delay_minutes": 0,
        "status": "on_time",
        "is_cancelled": False,
        "cancellation_reason": None,
        "delay_reason": None,
        "scheduled_arrival": "09:30",
        "estimated_arrival": "09:30",
        "destination": "Reading",
    }


def _board(services: list) -> dict:
    return {
        "location_name": "Paddington",
        "destination_name": "Reading",
        "services": services,
        "generated_at": "2024-01-15T08:00:00",
        "nrcc_messages": [],
    }


def _config(operator_filter=None, legs=None, num_services=3) -> dict:
    config = {
        CONF_API_KEY: "k",
        CONF_ORIGIN: "PAD",
        CONF_DESTINATION: "RDG",
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: num_services,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: 15,
        CONF_MAJOR_DELAY_THRESHOLD: 10,
        CONF_MINOR_DELAY_THRESHOLD: 3,
    }
    if operator_filter is not None:
        config[CONF_OPERATOR_FILTER] = operator_filter
    if legs:
        config[CONF_LEGS] = legs
    return config


async def _update(hass, api, config) -> tuple[NationalRailDataUpdateCoordinator, dict]:
    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, config)
        return coordinator, await coordinator._async_update_data()


# --- operators module ---------------------------------------------------


def test_matching_is_by_code_and_case_insensitive() -> None:
    service = {"operator_code": "gw"}
    assert service_matches_operators(service, ["GW"])
    assert not service_matches_operators(service, ["SW"])


def test_empty_filter_matches_everything() -> None:
    assert service_matches_operators({"operator_code": ""}, [])


def test_service_without_code_excluded_when_filtering() -> None:
    assert not service_matches_operators({"operator": "Rail Replacement"}, ["GW"])


def test_clean_filter_dedupes_and_sorts() -> None:
    assert clean_operator_filter(["sw", "GW", " gw ", ""]) == ["GW", "SW"]
    assert clean_operator_filter(None) == []


def test_options_merge_known_observed_and_selected() -> None:
    options = operator_options({"ZZ": "New Rail", "GW": "Ignored"}, ["QQ", "gw"])
    assert options["GW"] == KNOWN_OPERATORS["GW"]
    assert options["ZZ"] == "New Rail"
    assert options["QQ"] == "QQ"
    assert list(options.values()) == sorted(options.values(), key=str.lower)


def test_api_parses_operator_code() -> None:
    api = NationalRailAPI("k", AsyncMock())
    parsed = api._parse_service(
        {"std": "09:00", "etd": "On time", "operator": "GWR", "operatorCode": "GW"}
    )
    assert parsed["operator_code"] == "GW"


# --- coordinator --------------------------------------------------------


async def test_single_leg_filters_before_truncating(hass: HomeAssistant) -> None:
    services = [
        _svc("a", "SW", "South Western", "09:00"),
        _svc("b", "SW", "South Western", "09:05"),
        _svc("c", "GW", "GWR", "09:10"),
        _svc("d", "GW", "GWR", "09:15"),
    ]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(return_value=_board(services))

    coordinator, data = await _update(hass, api, _config(["GW"], num_services=1))

    assert [s["service_id"] for s in data["services"]] == ["c"]
    assert data["total_services_found"] == 4
    assert data["operator_filter"] == ["GW"]
    assert api.get_departure_board.call_args.kwargs["num_rows"] >= OPERATOR_FILTER_MIN_ROWS
    assert coordinator.observed_operators == {"SW": "South Western", "GW": "GWR"}


async def test_no_filter_leaves_services_alone(hass: HomeAssistant) -> None:
    services = [_svc("a", "SW", "S"), _svc("b", "GW", "G", "09:05")]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(return_value=_board(services))

    _, data = await _update(hass, api, _config())

    assert len(data["services"]) == 2
    assert data["operator_filter"] == []
    assert api.get_departure_board.call_args.kwargs["num_rows"] == 3


async def test_all_filtered_out_has_clear_summary(hass: HomeAssistant) -> None:
    api = AsyncMock()
    api.get_departure_board = AsyncMock(return_value=_board([_svc("a", "SW", "S")]))

    _, data = await _update(hass, api, _config(["GW"]))

    assert data["services"] == []
    assert data["summary"] == "No services from selected operators"


async def test_filter_applies_to_every_leg(hass: HomeAssistant) -> None:
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    leg1 = _board([_svc("a1", "GW", "GWR"), _svc("a2", "SW", "S", "09:05")])
    leg2 = _board(
        [_svc("b1", "XC", "CrossCountry", "10:00"), _svc("b2", "GW", "GWR", "10:10")]
    )
    api = AsyncMock()
    api.get_departure_board = AsyncMock(side_effect=[leg1, leg2])

    _, data = await _update(hass, api, _config(["GW"], legs=legs))

    assert [s["service_id"] for s in data["legs"][0]["services"]] == ["a1"]
    assert [s["service_id"] for s in data["legs"][1]["services"]] == ["b2"]
    assert data["operator_filter"] == ["GW"]
    # The connection is evaluated against the filtered second leg only
    assert data["connections"][0]["connecting_service_id"] == "b2"


# --- options flow -------------------------------------------------------


async def test_options_flow_offers_and_saves_operator_filter(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await open_options_page(hass, result["flow_id"], "trains")
    assert CONF_OPERATOR_FILTER in {str(k) for k in result["data_schema"].schema}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_OPERATOR_FILTER: ["GW", "SW"]}
    )
    result = await open_options_page(hass, result["flow_id"], "save")

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_OPERATOR_FILTER] == ["GW", "SW"]


async def test_options_flow_uses_a_dropdown(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await open_options_page(hass, result["flow_id"], "trains")
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == CONF_OPERATOR_FILTER)
    assert schema[key].config["mode"] == "dropdown"
    assert schema[key].config["multiple"] is True
