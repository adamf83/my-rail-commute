"""Tests for the arrival board and service details API calls."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
import pytest

from custom_components.my_rail_commute.api import (
    PRODUCT_ARRIVAL,
    PRODUCT_SERVICE_DETAILS,
    AuthenticationError,
    MissingAPIKeyError,
    NationalRailAPI,
    NationalRailAPIError,
)
from custom_components.my_rail_commute.const import (
    API_BASE_URL,
    ARRIVAL_API_BASE_URL,
    SERVICE_DETAILS_API_BASE_URL,
)


@pytest.fixture(name="api_client")
async def api_client_fixture(aiohttp_session):
    return NationalRailAPI(
        "test_api_key",
        aiohttp_session,
        arrival_api_key="arrival_key",
        service_details_api_key="details_key",
    )


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
        assert f"{ARRIVAL_API_BASE_URL}/GetArrBoardWithDetails/LBG" in url
        assert API_BASE_URL not in url
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


def _request_headers(mock):
    return [call.kwargs["headers"] for calls in mock.requests.values() for call in calls]


def test_the_three_products_use_distinct_base_urls():
    assert "1010-live-departure-board" in API_BASE_URL
    assert "1010-live-arrival-board" in ARRIVAL_API_BASE_URL
    assert "1010-service-details" in SERVICE_DETAILS_API_BASE_URL


async def test_each_product_is_called_with_its_own_key(api_client):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrBoardWithDetails/LBG.*"), payload={})
        mock.get(re.compile(r".*GetServiceDetails/abc$"), payload={"ata": "x"})
        mock.get(re.compile(r".*GetDepBoardWithDetails/PAD.*"), payload={})
        await api_client.get_arrival_board("LBG", "WYT")
        await api_client.get_service_details("abc")
        await api_client.get_departure_board("PAD")
        urls = _request_urls(mock)
        keys = [h["x-apikey"] for h in _request_headers(mock)]
    assert ARRIVAL_API_BASE_URL in urls[0]
    assert SERVICE_DETAILS_API_BASE_URL in urls[1]
    assert API_BASE_URL in urls[2]
    assert keys == ["arrival_key", "details_key", "test_api_key"]


async def test_arrival_board_without_a_key_raises_before_any_request(aiohttp_session):
    api = NationalRailAPI("test_api_key", aiohttp_session)
    assert not api.has_product(PRODUCT_ARRIVAL)
    with aioresponses() as mock:
        with pytest.raises(MissingAPIKeyError):
            await api.get_arrival_board("LBG", "WYT")
        assert _request_urls(mock) == []


async def test_service_details_without_a_key_raises_before_any_request(aiohttp_session):
    api = NationalRailAPI("test_api_key", aiohttp_session)
    assert not api.has_product(PRODUCT_SERVICE_DETAILS)
    with aioresponses() as mock:
        with pytest.raises(MissingAPIKeyError):
            await api.get_service_details("abc")
        assert _request_urls(mock) == []


def test_a_missing_key_is_an_authentication_error():
    assert issubclass(MissingAPIKeyError, AuthenticationError)


async def test_arrival_board_does_not_retry_a_server_error(api_client):
    # One response only: a retry would find no mock and sleep first
    with aioresponses() as mock:
        mock.get(
            re.compile(r".*GetArrBoardWithDetails/LBG.*"),
            status=500,
            payload={"fault": {"faultstring": "Unable to route"}},
        )
        with pytest.raises(NationalRailAPIError, match="500"):
            await api_client.get_arrival_board("LBG", "WYT")
        assert len(_request_urls(mock)) == 1


async def test_a_rejected_arrival_key_is_an_authentication_error(api_client):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrBoardWithDetails/LBG.*"), status=401)
        with pytest.raises(AuthenticationError):
            await api_client.get_arrival_board("LBG", "WYT")


async def test_validate_arrival_key(api_client):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrivalBoard/PAD.*"), payload={"trainServices": []})
        assert await api_client.validate_arrival_api_key() is True
        (url,) = _request_urls(mock)
        assert ARRIVAL_API_BASE_URL in url


async def test_validate_arrival_key_rejected(api_client):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetArrivalBoard/PAD.*"), status=401)
        with pytest.raises(AuthenticationError):
            await api_client.validate_arrival_api_key()


async def test_validate_arrival_key_without_a_key(aiohttp_session):
    api = NationalRailAPI("test_api_key", aiohttp_session)
    with pytest.raises(AuthenticationError):
        await api.validate_arrival_api_key()


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_validate_service_details_key_accepts_an_unknown_id(api_client, status):
    # An accepted key but an ID that cannot exist is not a failure
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetServiceDetails/.*"), status=status)
        assert await api_client.validate_service_details_api_key() is True
        (url,) = _request_urls(mock)
        assert SERVICE_DETAILS_API_BASE_URL in url


@pytest.mark.parametrize("status", [401, 403])
async def test_validate_service_details_key_rejected(api_client, status):
    with aioresponses() as mock:
        mock.get(re.compile(r".*GetServiceDetails/.*"), status=status)
        with pytest.raises(AuthenticationError):
            await api_client.validate_service_details_api_key()


async def test_validate_service_details_key_reports_network_failure(api_client):
    import aiohttp

    with aioresponses() as mock:
        mock.get(
            re.compile(r".*GetServiceDetails/.*"),
            exception=aiohttp.ClientConnectionError("down"),
            repeat=True,
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(NationalRailAPIError, match="Network error"):
                await api_client.validate_service_details_api_key()
