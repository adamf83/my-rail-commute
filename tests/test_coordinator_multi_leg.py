"""Tests for multi-leg journey support in the coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta
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
    CONF_MIN_CONNECTION_TIME,
    CONF_MINOR_DELAY_THRESHOLD,
    CONF_NIGHT_UPDATES,
    CONF_NUM_SERVICES,
    CONF_ONLY_CATCHABLE_SERVICES,
    CONF_ORIGIN,
    CONF_SEVERE_DELAY_THRESHOLD,
    CONF_TIME_WINDOW,
    DEFAULT_MAJOR_DELAY_THRESHOLD,
    DEFAULT_MIN_CONNECTION_TIME,
    DEFAULT_MINOR_DELAY_THRESHOLD,
    DEFAULT_SEVERE_DELAY_THRESHOLD,
    STATUS_CANCELLED,
    STATUS_CONNECTION_DELAYED,
    STATUS_CONNECTION_MISSED,
    STATUS_CONNECTION_OK,
    STATUS_CONNECTION_TIGHT,
    STATUS_CONNECTION_UNKNOWN,
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


def _shift_time(time_str: str, minutes: int) -> str:
    """Shift an "HH:MM" string by `minutes` (may cross midnight)."""
    dt = datetime.strptime(f"2000-01-01 {time_str}", "%Y-%m-%d %H:%M") + timedelta(
        minutes=minutes
    )
    return dt.strftime("%H:%M")


def _make_service(
    service_id: str,
    status: str = STATUS_ON_TIME,
    delay_minutes: int = 0,
    scheduled_departure: str = "09:00",
    scheduled_arrival: str = "09:30",
    estimated_arrival: str | None = None,
) -> dict:
    """Build a minimal already-parsed service dict.

    Defaults depart at 09:00 and arrive at 09:30 so that a second leg's
    default service (also departing 09:00) is NOT reachable from this one —
    tests that chain two legs and don't care about connection feasibility
    should pass an explicit `scheduled_departure` for the second leg's
    service that falls comfortably after 09:30.
    """
    is_cancelled = status == STATUS_CANCELLED
    expected_departure = (
        scheduled_departure
        if delay_minutes == 0
        else _shift_time(scheduled_departure, delay_minutes)
    )
    return {
        "scheduled_departure": scheduled_departure,
        "expected_departure": expected_departure,
        "platform": "1",
        "operator": "Test Operator",
        "service_id": service_id,
        "calling_points": [],
        "delay_minutes": delay_minutes,
        "status": status,
        "is_cancelled": is_cancelled,
        "cancellation_reason": "Signal failure" if is_cancelled else None,
        "delay_reason": "Late running" if delay_minutes else None,
        "scheduled_arrival": scheduled_arrival,
        "estimated_arrival": estimated_arrival or scheduled_arrival,
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


def _make_config(
    legs: list[dict],
    min_connection_time: int = DEFAULT_MIN_CONNECTION_TIME,
    num_services: int = 3,
    only_catchable_services: bool = False,
) -> dict:
    """Build a multi-leg config dict."""
    return {
        CONF_API_KEY: "test_key",
        CONF_ORIGIN: legs[0]["origin"],
        CONF_DESTINATION: legs[-1]["destination"],
        CONF_LEGS: legs,
        CONF_TIME_WINDOW: 60,
        CONF_NUM_SERVICES: num_services,
        CONF_NIGHT_UPDATES: True,
        CONF_SEVERE_DELAY_THRESHOLD: DEFAULT_SEVERE_DELAY_THRESHOLD,
        CONF_MAJOR_DELAY_THRESHOLD: DEFAULT_MAJOR_DELAY_THRESHOLD,
        CONF_MINOR_DELAY_THRESHOLD: DEFAULT_MINOR_DELAY_THRESHOLD,
        CONF_MIN_CONNECTION_TIME: min_connection_time,
        CONF_ONLY_CATCHABLE_SERVICES: only_catchable_services,
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
    # Departs comfortably after leg1's 09:30 arrival so this test only
    # exercises per-leg severity combination, not connection feasibility
    # (which is covered separately below).
    leg2_service = _make_service(
        "svc2", scheduled_departure="09:45", **leg2_status_kwargs
    )
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


def _make_leg_result(
    destination: str, destination_name: str, next_train: dict | None
) -> dict:
    """Build a minimal parsed leg_result dict with just the fields
    `_evaluate_connection` reads from the *incoming* leg."""
    return {
        "destination": destination,
        "destination_name": destination_name,
        "next_train": next_train,
    }


@pytest.fixture(name="two_leg_coordinator")
def two_leg_coordinator_fixture(hass: HomeAssistant) -> NationalRailDataUpdateCoordinator:
    """A coordinator configured for a two-leg PAD -> RDG -> OXF journey."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    return NationalRailDataUpdateCoordinator(hass, AsyncMock(), _make_config(legs))


async def test_evaluate_connection_ok_with_comfortable_buffer(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """A generous buffer on the outgoing leg's own next train is Connection OK."""
    leg_from = _make_leg_result("RDG", "Reading", _make_service("svc1"))
    outgoing = _make_service("svc2", scheduled_departure="09:45")
    leg_to = {"services": [outgoing], "next_train": outgoing}

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_OK
    assert conn["feasible"] is True
    assert conn["buffer_minutes"] == 15
    assert conn["arrival_time"] == "09:30"
    assert conn["connecting_departure"] == "09:45"
    assert conn["connecting_service_id"] == "svc2"


async def test_evaluate_connection_tight_below_margin(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """A buffer that clears the minimum but not the comfort margin is Tight."""
    leg_from = _make_leg_result("RDG", "Reading", _make_service("svc1"))
    outgoing = _make_service("svc2", scheduled_departure="09:36")
    leg_to = {"services": [outgoing], "next_train": outgoing}

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_TIGHT
    assert conn["feasible"] is True
    assert conn["buffer_minutes"] == 6


async def test_evaluate_connection_missed_when_no_service_has_enough_buffer(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """No tracked outgoing service leaves enough time after arrival."""
    leg_from = _make_leg_result("RDG", "Reading", _make_service("svc1"))
    outgoing = _make_service("svc2", scheduled_departure="09:20")
    leg_to = {"services": [outgoing], "next_train": outgoing}

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_MISSED
    assert conn["feasible"] is False
    assert conn["connecting_service_id"] is None


async def test_evaluate_connection_delayed_when_a_later_train_is_needed(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """Feasible only on a later train than the outgoing leg's own next train."""
    leg_from = _make_leg_result("RDG", "Reading", _make_service("svc1"))
    unreachable_next = _make_service("svc2a", scheduled_departure="09:20")
    reachable_later = _make_service("svc2b", scheduled_departure="09:50")
    leg_to = {
        "services": [unreachable_next, reachable_later],
        "next_train": unreachable_next,
    }

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_DELAYED
    assert conn["feasible"] is True
    assert conn["connecting_service_id"] == "svc2b"


async def test_evaluate_connection_unknown_when_incoming_leg_has_no_next_train(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """No next train on the incoming leg means feasibility can't be judged."""
    leg_from = _make_leg_result("RDG", "Reading", None)
    outgoing = _make_service("svc2", scheduled_departure="09:45")
    leg_to = {"services": [outgoing], "next_train": outgoing}

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_UNKNOWN
    assert conn["feasible"] is None


async def test_evaluate_connection_unknown_when_arrival_time_unparseable(
    two_leg_coordinator: NationalRailDataUpdateCoordinator,
) -> None:
    """An unparseable/missing arrival time is treated as Unknown, not Missed."""
    next_train = {**_make_service("svc1"), "estimated_arrival": None, "scheduled_arrival": None}
    leg_from = _make_leg_result("RDG", "Reading", next_train)
    outgoing = _make_service("svc2", scheduled_departure="09:45")
    leg_to = {"services": [outgoing], "next_train": outgoing}

    conn = two_leg_coordinator._evaluate_connection(leg_from, leg_to)

    assert conn["status"] == STATUS_CONNECTION_UNKNOWN
    assert conn["feasible"] is None


async def test_missed_connection_elevates_overall_status_even_with_normal_legs(
    hass: HomeAssistant,
) -> None:
    """A missed connection alone pushes overall_status to Critical, even when
    both legs are individually Normal."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    # leg1 arrives 09:30; leg2's only service departs 09:20 (before arrival)
    leg1_service = _make_service("svc1")
    leg2_service = _make_service("svc2", scheduled_departure="09:20")

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

    assert data["legs"][0]["overall_status"] == STATUS_NORMAL
    assert data["legs"][1]["overall_status"] == STATUS_NORMAL
    assert data["connections"][0]["status"] == STATUS_CONNECTION_MISSED
    assert data["journey_feasible"] is False
    assert data["overall_status"] == STATUS_CRITICAL


async def test_connection_feasible_journey_reports_ok_and_feasible_true(
    hass: HomeAssistant,
) -> None:
    """A comfortably-timed connection reports OK, feasible, and Normal overall."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    leg1_service = _make_service("svc1")
    leg2_service = _make_service("svc2", scheduled_departure="09:45")

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

    assert data["connections"][0]["status"] == STATUS_CONNECTION_OK
    assert data["journey_feasible"] is True
    assert data["overall_status"] == STATUS_NORMAL


async def test_min_connection_time_config_affects_feasibility(
    hass: HomeAssistant,
) -> None:
    """Raising min_connection_time can turn a previously-OK buffer into Missed."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    leg1_service = _make_service("svc1")
    # 7-minute buffer: feasible under the default 5-minute minimum, infeasible
    # once min_connection_time is raised to 10.
    leg2_service = _make_service("svc2", scheduled_departure="09:37")

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
        coordinator = NationalRailDataUpdateCoordinator(
            hass, api, _make_config(legs, min_connection_time=10)
        )
        data = await coordinator._async_update_data()

    assert data["connections"][0]["status"] == STATUS_CONNECTION_MISSED
    assert data["journey_feasible"] is False


async def test_only_catchable_services_defaults_to_false(
    hass: HomeAssistant,
) -> None:
    """The coordinator defaults only_catchable_services to False when absent."""
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

    assert coordinator.only_catchable_services is False


async def test_non_last_leg_services_are_tagged_catchable(
    hass: HomeAssistant,
) -> None:
    """Every non-final leg's services get a catchable flag, even when the
    filter itself is off."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    catchable_service = _make_service("svc1")  # arrives 09:30
    # Arrives at 09:50, after outgoing's only departure (09:45) — unreachable.
    uncatchable_service = _make_service(
        "svc1b", scheduled_departure="09:20", scheduled_arrival="09:50"
    )
    outgoing = _make_service("svc2", scheduled_departure="09:45")  # only reachable from svc1

    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response(
                "Paddington", "Reading", [catchable_service, uncatchable_service]
            ),
            _make_leg_response("Reading", "Oxford", [outgoing]),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(
            hass, api, _make_config(legs, num_services=2)
        )
        data = await coordinator._async_update_data()

    leg1_services = {s["service_id"]: s["catchable"] for s in data["legs"][0]["services"]}
    assert leg1_services == {"svc1": True, "svc1b": False}
    # The last leg has nothing to connect onto, so it isn't tagged.
    assert "catchable" not in data["legs"][1]["services"][0]


async def test_only_catchable_services_filters_before_truncation(
    hass: HomeAssistant,
) -> None:
    """Non-catchable services are dropped before num_services truncates the list,
    so a catchable train further down isn't cut off by a low num_services."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    # Arrives 09:50 - after outgoing's only departure (09:45) - unreachable.
    unreachable_first = _make_service(
        "svc1a", scheduled_departure="08:55", scheduled_arrival="09:50"
    )
    # Arrives 09:30 - 15-minute buffer before outgoing's 09:45 departure.
    reachable_second = _make_service(
        "svc1b", scheduled_departure="09:00", scheduled_arrival="09:30"
    )
    outgoing = _make_service("svc2", scheduled_departure="09:45")

    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response(
                "Paddington", "Reading", [unreachable_first, reachable_second]
            ),
            _make_leg_response("Reading", "Oxford", [outgoing]),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(
            hass,
            api,
            _make_config(legs, num_services=1, only_catchable_services=True),
        )
        data = await coordinator._async_update_data()

    leg1_services = data["legs"][0]["services"]
    assert len(leg1_services) == 1
    assert leg1_services[0]["service_id"] == "svc1b"
    assert data["legs"][0]["next_train"]["service_id"] == "svc1b"


async def test_only_catchable_services_disabled_keeps_uncatchable_services(
    hass: HomeAssistant,
) -> None:
    """With the filter off, uncatchable services stay in the list (just tagged)."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    # Arrives 09:50 - after outgoing's only departure (09:45) - unreachable.
    unreachable_first = _make_service(
        "svc1a", scheduled_departure="08:55", scheduled_arrival="09:50"
    )
    reachable_second = _make_service(
        "svc1b", scheduled_departure="09:00", scheduled_arrival="09:30"
    )
    outgoing = _make_service("svc2", scheduled_departure="09:45")

    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response(
                "Paddington", "Reading", [unreachable_first, reachable_second]
            ),
            _make_leg_response("Reading", "Oxford", [outgoing]),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(
            hass,
            api,
            _make_config(legs, num_services=1, only_catchable_services=False),
        )
        data = await coordinator._async_update_data()

    leg1_services = data["legs"][0]["services"]
    assert len(leg1_services) == 1
    assert leg1_services[0]["service_id"] == "svc1a"
    assert leg1_services[0]["catchable"] is False


async def test_only_catchable_services_over_fetches_departure_rows(
    hass: HomeAssistant,
) -> None:
    """Enabling the filter requests more rows per leg so there's a large
    enough pool to filter down from and match connections against."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [_make_service("svc1")]),
            _make_leg_response(
                "Reading", "Oxford", [_make_service("svc2", scheduled_departure="09:45")]
            ),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(
            hass,
            api,
            _make_config(legs, num_services=3, only_catchable_services=True),
        )
        await coordinator._async_update_data()

    for call in api.get_departure_board.call_args_list:
        assert call.kwargs["num_rows"] == 20


async def test_catchable_ignores_cancelled_and_departed_next_leg_services(
    hass: HomeAssistant,
) -> None:
    """A cancelled or already-departed service on the next leg doesn't count
    as a viable connection, even if its scheduled time would otherwise match."""
    legs = [
        {"origin": "PAD", "destination": "RDG"},
        {"origin": "RDG", "destination": "OXF"},
    ]
    leg1_service = _make_service("svc1")  # arrives 09:30
    cancelled_outgoing = _make_service(
        "svc2a", scheduled_departure="09:45", status=STATUS_CANCELLED
    )
    departed_outgoing = _make_service("svc2b", scheduled_departure="07:00")
    viable_outgoing = _make_service("svc2c", scheduled_departure="09:50")

    api = AsyncMock()
    api.get_departure_board = AsyncMock(
        side_effect=[
            _make_leg_response("Paddington", "Reading", [leg1_service]),
            _make_leg_response(
                "Reading",
                "Oxford",
                [cancelled_outgoing, departed_outgoing, viable_outgoing],
            ),
        ]
    )

    with patch(
        "custom_components.my_rail_commute.coordinator.dt_util.now",
        return_value=_TEST_TIME,
    ):
        coordinator = NationalRailDataUpdateCoordinator(
            hass, api, _make_config(legs, num_services=1, only_catchable_services=True)
        )
        data = await coordinator._async_update_data()

    assert data["legs"][0]["services"][0]["catchable"] is True
    assert data["legs"][0]["services"][0]["service_id"] == "svc1"
