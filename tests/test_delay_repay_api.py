"""Tests for the arrival board and service details API calls."""

from __future__ import annotations

import re

from aioresponses import aioresponses
import pytest

from custom_components.my_rail_commute.api import (
    NationalRailAPI,
    NationalRailAPIError,
)
from custom_components.my_rail_commute.const import API_BASE_URL


@pytest.fixture(name="api_client")
async def api_client_fixture(aiohttp_session):
    return NationalRailAPI("test_api_key", aiohttp_session)


def _request_urls(mock):
    return [str(url) for (_, url) in mock.requests]


async def test_get_arrival_board_returns_raw_services(api_client):
    payload = {
        "GetStationBoardResult": {
            "trainServices": [
                {"serviceID": "9494208LNDNBDC", "sta": "22:53", "eta": "23:05"},
                {"serviceID": "other"},
            ]
        }
    }
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrBoardWithDetails/LBG.*"), payload=payload)
        services = await api_client.get_arrival_board("lbg", "wyt")

        assert [s["serviceID"] for s in services] == ["9494208LNDNBDC", "other"]
        (url,) = _request_urls(mock)
        assert f"{API_BASE_URL}/GetArrBoardWithDetails/LBG" in url
        # Filtered to trains from the origin, within the "with details" row limit
        for fragment in (
            "filterCrs=WYT",
            "filterType=from",
            "timeOffset=-10",
            "timeWindow=45",
            "numRows=9",
        ):
            assert fragment in url


async def test_get_arrival_board_handles_wrapped_and_empty_lists(api_client):
    with aioresponses() as mock:
        mock.get(
            re.compile(r".*GetArrBoardWithDetails/LBG.*"),
            payload={"trainServices": {"service": [{"serviceID": "1"}]}},
        )
        mock.get(
            re.compile(r".*GetArrBoardWithDetails/LBG.*"),
            payload={"GetStationBoardResult": {"locationName": "London Bridge"}},
        )
        assert len(await api_client.get_arrival_board("LBG", "WYT")) == 1
        assert await api_client.get_arrival_board("LBG", "WYT") == []


@pytest.mark.parametrize("payload", [[], "text", {"GetStationBoardResult": 5}])
async def test_get_arrival_board_rejects_unexpected_shapes(api_client, payload):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrBoardWithDetails/LBG.*"), payload=payload)
        with pytest.raises(NationalRailAPIError):
            await api_client.get_arrival_board("LBG", "WYT")


async def test_get_service_details_returns_flat_details(api_client):
    details = {"crs": "LBG", "sta": "22:53", "ata": "23:05"}
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetServiceDetails/9494208LNDNBDC_$"), payload=details)
        assert await api_client.get_service_details("9494208LNDNBDC_") == details


async def test_get_service_details_unwraps_result_object(api_client):
    with aioresponses() as mock:
        mock.get(
            re.compile(r".*GetServiceDetails/abc$"),
            payload={"GetServiceDetailsResult": {"ata": "On time"}},
        )
        assert await api_client.get_service_details("abc") == {"ata": "On time"}


async def test_get_service_details_does_not_retry_an_unavailable_id(api_client):
    # An expired ID gives a 500. Only one response is registered: a retry
    # would find no mock and fail differently (and sleep between attempts).
    with aioresponses() as mock:
        mock.get(
            re.compile(r".*GetServiceDetails/expired$"),
            status=500,
            payload={"Message": "Unable to retrieve the requested data"},
        )
        with pytest.raises(NationalRailAPIError, match="500"):
            await api_client.get_service_details("expired")
        assert len(_request_urls(mock)) == 1


@pytest.mark.parametrize("payload", [[], "text", None])
async def test_get_service_details_rejects_unexpected_shapes(api_client, payload):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetServiceDetails/abc$"), payload=payload)
        with pytest.raises(NationalRailAPIError):
            await api_client.get_service_details("abc")
