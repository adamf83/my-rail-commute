"""Tests for matching a train on the destination board and reading its arrival."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.my_rail_commute.api import NationalRailAPIError
from custom_components.my_rail_commute.delay_repay.confirm import (
    DestinationBoardSource,
    details_ids,
    id_prefix,
    match_arrival_service,
)
from custom_components.my_rail_commute.delay_repay.models import (
    ClaimRecord,
    ClaimStatus,
    Confirmation,
)
from custom_components.my_rail_commute.delay_repay.parsing import ArrivalObservation


def _record(**overrides) -> ClaimRecord:
    values = {
        "date": "2026-10-08",
        "leg": 1,
        "service_id": "9494208WHYTELF",
        "operator": "Southern",
        "origin": "WYT",
        "destination": "LBG",
        "scheduled_departure": "22:40",
        "scheduled_arrival": "22:53",
        "arrival": "23:10",
        "delay_minutes": 17,
        "tier": 15,
        "is_cancelled": False,
        "confirmation": Confirmation.ESTIMATED,
        "status": ClaimStatus.PENDING,
        "live_until": "2026-10-08T23:03:00+00:00",
        "first_seen": "2026-10-08T22:30:00+00:00",
        "last_updated": "2026-10-08T22:30:00+00:00",
    }
    values.update(overrides)
    return ClaimRecord(**values)


def _arrival(service_id="9494208LNDNBDC", **extra):
    return {"serviceID": service_id, "sta": "22:53", "eta": "23:05", **extra}


@pytest.mark.parametrize(
    ("service_id", "expected"),
    [
        ("9494208WHYTELF", "9494208"),
        ("9494208LNDNBDC_", "9494208"),
        ("ABC123", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_id_prefix(service_id, expected):
    assert id_prefix(service_id) == expected


def test_match_by_shared_id_prefix():
    services = [_arrival("1111111XXXXXXX"), _arrival("9494208LNDNBDC")]
    assert match_arrival_service(services, _record()) == services[1]


def test_match_falls_back_to_origin_and_departure_time():
    services = [
        _arrival(
            "5555555LNDNBDC",
            previousCallingPoints=[
                {"callingPoint": [{"crs": "WYT", "st": "22:40"}, {"crs": "ECR"}]}
            ],
        ),
        _arrival("6666666LNDNBDC"),
    ]
    # The record has no numeric ID to match on
    record = _record(service_id="ABC")
    assert match_arrival_service(services, record) == services[0]


def test_fallback_requires_matching_origin_and_time():
    wrong_time = _arrival(
        "5555555X",
        previousCallingPoints=[{"callingPoint": [{"crs": "WYT", "st": "22:10"}]}],
    )
    wrong_origin = _arrival(
        "6666666X",
        previousCallingPoints=[{"callingPoint": [{"crs": "ECR", "st": "22:40"}]}],
    )
    assert (
        match_arrival_service([wrong_time, wrong_origin], _record(service_id=""))
        is None
    )


@pytest.mark.parametrize(
    "previous", [None, [], {}, [None], [{"callingPoint": "x"}], [{"callingPoint": []}]]
)
def test_fallback_tolerates_odd_calling_point_shapes(previous):
    service = {"serviceID": "9", "previousCallingPoints": previous}
    assert match_arrival_service([service], _record(service_id="")) is None


def test_fallback_accepts_single_dict_shapes():
    service = {
        "serviceID": "9",
        "previousCallingPoints": {"callingPoint": {"crs": "wyt", "st": "22:40"}},
    }
    assert match_arrival_service([service], _record(service_id="")) == service


def test_no_match_on_an_empty_board():
    assert match_arrival_service([], _record()) is None


def test_details_ids_prefers_url_safe_and_dedupes():
    assert details_ids({"serviceID": "A", "serviceIdUrlSafe": "A_"}) == ["A_", "A"]
    assert details_ids({"serviceID": "A", "serviceIdUrlSafe": "A"}) == ["A"]
    assert details_ids({"serviceID": "A"}) == ["A"]
    assert details_ids({"serviceID": "", "serviceIdUrlSafe": None}) == []


def _api(services=None, details=None, details_error=None):
    api = AsyncMock()
    api.get_arrival_board.return_value = services if services is not None else []
    if details_error is not None:
        api.get_service_details.side_effect = details_error
    else:
        api.get_service_details.return_value = details or {}
    return api


async def test_source_reads_the_actual_arrival():
    api = _api(
        [_arrival(serviceIdUrlSafe="9494208LNDNBDC_")],
        details={"crs": "LBG", "sta": "22:53", "ata": "23:05"},
    )
    observation = await DestinationBoardSource(api).async_fetch(_record())

    assert observation == ArrivalObservation(actual="23:05")
    api.get_arrival_board.assert_awaited_once_with("LBG", "WYT")
    api.get_service_details.assert_awaited_once_with("9494208LNDNBDC_")


async def test_source_reports_not_arrived_yet():
    api = _api([_arrival()], details={"crs": "LBG", "sta": "22:53", "eta": "23:05"})
    observation = await DestinationBoardSource(api).async_fetch(_record())
    assert observation == ArrivalObservation()
    assert observation.has_outcome is False


async def test_source_returns_none_when_the_train_is_not_on_the_board():
    api = _api([_arrival("1111111XXXXXXX")])
    assert await DestinationBoardSource(api).async_fetch(_record()) is None
    api.get_service_details.assert_not_awaited()


async def test_source_reports_a_cancellation_from_the_board_without_a_details_call():
    api = _api([_arrival(isCancelled=True)])
    observation = await DestinationBoardSource(api).async_fetch(_record())
    assert observation == ArrivalObservation(is_cancelled=True)
    api.get_service_details.assert_not_awaited()


async def test_source_tries_the_next_id_when_one_fails():
    api = AsyncMock()
    api.get_arrival_board.return_value = [
        _arrival("9494208LNDNBDC", serviceIdUrlSafe="9494208LNDNBDC_")
    ]
    api.get_service_details.side_effect = [
        NationalRailAPIError("500"),
        {"ata": "On time"},
    ]
    observation = await DestinationBoardSource(api).async_fetch(_record())
    assert observation == ArrivalObservation(actual="On time")
    assert [c.args[0] for c in api.get_service_details.await_args_list] == [
        "9494208LNDNBDC_",
        "9494208LNDNBDC",
    ]


async def test_source_returns_none_when_no_id_works():
    api = _api([_arrival()], details_error=NationalRailAPIError("500"))
    assert await DestinationBoardSource(api).async_fetch(_record()) is None


async def test_source_lets_board_errors_propagate():
    api = AsyncMock()
    api.get_arrival_board.side_effect = NationalRailAPIError("down")
    with pytest.raises(NationalRailAPIError):
        await DestinationBoardSource(api).async_fetch(_record())
