"""Tests for multi-leg journey support in the coordinator."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest

from custom_components.my_rail_commute.api import NationalRailAPIError
from custom_components.my_rail_commute.const import (
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
    STATUS_CANCELLED,
    STATUS_CRITICAL,
    STATUS_DELAYED,
    STATUS_MAJOR_DELAYS,
    STATUS_MINOR_DELAYS,
    STATUS_NORMAL,
    STATUS_ON_TIME,
    STATUS_SEVERE_DISRUPTION,
)
from custom_components.my_rail_commute.coordinator import (
    NationalRailDataUpdateCoordinator,
    build_route_id,
)

_TEST_TIME = datetime(2024, 1, 15, 8, 0, 0, tzinfo=dt_util.UTC)


def _make_service(
    service_id: str, status: str = STATUS_ON_TIME, delay_minutes: int = 0
) -> dict:
    """Build a minimal already-parsed service dict."""
    is_cancelled = status == STATUS_CANCELLED
    return {
        "scheduled_departure": "09:00",
        "expected_departure": "09:00" if delay_minutes == 0 else "09:10",
        "platform": "1",
        "operator": "Test Operator",
        "service_id": service_id,
        "calling_points": [],
        "delay_minutes": delay_minutes,
        "status": status,
        "is_cancelled": is_cancelled,
        "cancellation_reason": "Signal failure" if is_cancelled else None,
        "delay_reason": "Late running" if delay_minutes else None,
        "scheduled_arrival": "09:30",
        "estimated_arrival": "09:30",
        "destination": "Destination",
    }


def _make_leg_response(origin_name: str, destination_name: str, services: list) -> dict:
    """Build a raw get_departure_board response for one leg."""
    return {
        "location_name": origin_name,
        "destination_name": destination_name,
        "services": services,
        "generated_at": "2024-01-15T08:00:00",
        "nrcc_messages": [],
    }


def _make_config(legs: list[dict]) -> dict:
    """Build a multi-leg config dict."""
    return {
        CONF_API_KEY: "test_key",
        CONF_ORIGIN: legs[0]["origin"],
        CONF_DESTINATION: legs[-1]["destination"],
        CONF_LEGS: legs,
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: 3,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
        CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
        CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
    }


def test_build_route_id_single_leg_matches_legacy_format():
    """Single-leg route id is byte-identical to the historical format."""
    legs = [{"origin": "PAD", "destination": "RDG"}]
    assert build_route_id(legs) == "PAD_RDG"


def test_build_route_id_all_departures():
    """All-departures single leg uses the historical suffix."""
    legs = [{"origin": "PAD", "destination": None}]
    assert build_route_id(legs) == "PAD_all_departures"


def test_build_route_id_multi_leg_chain():
    """Multi-leg route id embeds every intermediate station."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    assert build_route_id(legs) == "PAD_RDG_OXF"


def test_build_route_id_distinguishes_different_interchanges():
    """Two chains sharing endpoints but different interchanges never collide."""
    via_rdg = build_route_id(
        [{"origin": "A", "destination": "RDG"}, {"origin": "RDG", "destination": "D"}]
    )
    via_oxf = build_route_id(
        [{"origin": "A", "destination": "OXF"}, {"origin": "OXF", "destination": "D"}]
    )
    assert via_rdg != via_oxf


async def test_coordinator_parses_legs_from_config(hass: HomeAssistant) -> None:
    """The coordinator wraps a single origin/destination into one leg by default."""
    api = AsyncMock()
    coordinator = NationalRailDataUpdateCoordinator(
        hass,
        api,
        {
            CONF_API_KEY: "test_key",
            CONF_ORIGIN: "PAD",
            CONF_DESTINATION: "RDG",
            CONF_TIME_WINDOW: 60,
            CONF_NUM_SERVICES: 3,
            CONF_NIGHT_UPDATES: True,
            CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
            CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
            CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
        },
    )

    assert coordinator.is_multi_leg is False
    assert coordinator.legs == [{"origin": "PAD", "destination": "RDG"}]


async def test_coordinator_reads_multi_leg_config(hass: HomeAssistant) -> None:
    """CONF_LEGS is parsed into coordinator.legs and marks is_multi_leg."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    api = AsyncMock()
    coordinator = NationalRailDataUpdateCoordinator(hass, api, _make_config(legs))

    assert coordinator.is_multi_leg is True
    assert coordinator.legs == legs
    assert coordinator.origin == "PAD"
    assert coordinator.destination == "OXF"


async def test_multi_leg_update_fetches_each_leg_sequentially(
    hass: HomeAssistant,
) -> None:
    """Each leg is fetched with its own origin/destination, in order."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [_make_service("svc1")]),
            _make_leg_response("Reading", "Oxford", [_make_service("svc2")]),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, _make_config(legs))
        data = await coordinator._async_update_data()

    assert api.get_departure_board.call_count == 2
    first_call, second_call = api.get_departure_board.call_args_list
    assert first_call.args[0] == "PAD"
    assert first_call.kwargs["destination_crs"] == "RDG"
    assert second_call.args[0] == "RDG"
    assert second_call.kwargs["destination_crs"] == "OXF"

    assert data["is_multi_leg"] is True
    assert len(data["legs"]) == 2
    assert data["origin"] == "PAD"
    assert data["destination"] == "OXF"


async def test_multi_leg_services_are_concatenated_and_tagged_with_leg(
    hass: HomeAssistant,
) -> None:
    """Top-level services concatenate every leg's services, tagged with leg number."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [_make_service("svc1")]),
            _make_leg_response(
                "Reading", "Oxford", [_make_service("svc2"), _make_service("svc3")]
            ),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, _make_config(legs))
        data = await coordinator._async_update_data()

    assert [s["service_id"] for s in data["services"]] == ["svc1", "svc2", "svc3"]
    assert [s["leg"] for s in data["services"]] == [1, 2, 2]
    assert data["services_tracked"] == 3
    assert data["on_time_count"] == 3


@pytest.mark.parametrize(
    ("leg1_status_kwargs", "leg2_status_kwargs", "expected_overall"),
    [
        ({}, {}, STATUS_NORMAL),
        ({"delay_minutes": 5}, {}, STATUS_MINOR_DELAYS),
        ({}, {"delay_minutes": 12}, STATUS_MAJOR_DELAYS),
        ({"delay_minutes": 20}, {}, STATUS_SEVERE_DISRUPTION),
        ({}, {"status": STATUS_CANCELLED}, STATUS_CRITICAL),
        ({"status": STATUS_CANCELLED}, {"delay_minutes": 20}, STATUS_CRITICAL),
    ],
)
async def test_multi_leg_overall_status_is_worst_case_across_legs(
    hass: HomeAssistant, leg1_status_kwargs, leg2_status_kwargs, expected_overall
) -> None:
    """overall_status reflects the most severe status across all legs."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    leg1_service = _make_service("svc1", **leg1_status_kwargs)
    leg2_service = _make_service("svc2", **leg2_status_kwargs)
    if leg1_status_kwargs.get("delay_minutes"):
        leg1_service["status"] = STATUS_DELAYED
    if leg2_status_kwargs.get("delay_minutes"):
        leg2_service["status"] = STATUS_DELAYED

    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [leg1_service]),
            _make_leg_response("Reading", "Oxford", [leg2_service]),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, _make_config(legs))
        data = await coordinator._async_update_data()

    assert data["overall_status"] == expected_overall


async def test_multi_leg_leg_failure_propagates_like_single_leg(
    hass: HomeAssistant,
) -> None:
    """A failure fetching any leg raises UpdateFailed, same as a single-leg failure."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [_make_service("svc1")]),
            NationalRailAPIError("boom"),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, _make_config(legs))
        coordinator._max_failed_updates = 1
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


async def test_single_leg_output_shape_unchanged(hass: HomeAssistant) -> None:
    """Single-leg _parse_data output has exactly the same keys as before the refactor."""
    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        return_value=_make_leg_response(
            "Paddington", "Reading", [_make_service("svc1")]
        )
    )

    config = {
        CONF_API_KEY: "test_key",
        CONF_ORIGIN: "PAD",
        CONF_DESTINATION: "RDG",
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: 3,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
        CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
        CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
    }

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(hass, api, config)
        data = await coordinator._async_update_data()

    expected_keys = {
        "origin",
        "origin_name",
        "destination",
        "destination_name",
        "time_window",
        "services_tracked",
        "total_services_found",
        "services",
        "on_time_count",
        "delayed_count",
        "cancelled_count",
        "next_train",
        "overall_status",
        "max_delay_minutes",
        "disruption_reasons",
        "summary",
        "multi_destination",
        "last_updated",
        "next_update",
        "nrcc_messages",
    }
    assert set(data.keys()) == expected_keys
    assert "is_multi_leg" not in data
    assert "legs" not in data
