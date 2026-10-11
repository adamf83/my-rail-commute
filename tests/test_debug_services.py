"""Tests for the raw-response debugging actions."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from aioresponses import aioresponses
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.my_rail_commute.api import NationalRailAPI
from custom_components.my_rail_commute.const import (
    API_BASE_URL,
    ARRIVAL_API_BASE_URL,
    CONF_ARRIVAL_API_KEY,
    CONF_SERVICE_DETAILS_API_KEY,
    DOMAIN,
    SERVICE_DETAILS_API_BASE_URL,
    SERVICE_GET_ARRIVAL_BOARD,
    SERVICE_GET_DEPARTURE_BOARD,
    SERVICE_GET_SERVICE_DETAILS,
)
from custom_components.my_rail_commute.debug_services import (
    async_register_services,
    async_remove_services,
)


class _Call:
    def __init__(self, **data):
        self.data = data


def _entry(entry_id, title="Commute", **config):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.data = {CONF_API_KEY: f"dep-{entry_id}", **config}
    entry.options = {}
    return entry


def _hass(*entries, raw_result=None):
    """Build a hass mock with the entries loaded and their API clients mocked."""
    hass = MagicMock()
    apis = {}
    hass.data = {DOMAIN: {}}
    for entry in entries:
        api = MagicMock()
        for name in (
            "get_raw_departure_board",
            "get_raw_arrival_board",
            "get_raw_service_details",
        ):
            setattr(api, name, AsyncMock(return_value=raw_result or {"status": 200}))
        apis[entry.entry_id] = api
        hass.data[DOMAIN][entry.entry_id] = MagicMock(api=api)
    by_id = {e.entry_id: e for e in entries}
    hass.config_entries.async_get_entry = lambda eid: by_id.get(eid)
    hass.config_entries.async_entries = lambda domain: list(entries)
    return hass, apis


def _register(hass):
    handlers, kwargs = {}, {}

    def capture(domain, name, handler, **kw):
        handlers[name] = handler
        kwargs[name] = kw

    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock(side_effect=capture)
    async_register_services(hass)
    return handlers, kwargs


class TestRegistration:
    def test_registers_three_response_only_actions(self):
        hass, _ = _hass(_entry("a"))
        handlers, kwargs = _register(hass)

        assert set(handlers) == {
            SERVICE_GET_DEPARTURE_BOARD,
            SERVICE_GET_ARRIVAL_BOARD,
            SERVICE_GET_SERVICE_DETAILS,
        }
        for kw in kwargs.values():
            assert kw["supports_response"].name == "ONLY"

    def test_not_registered_twice(self):
        hass, _ = _hass(_entry("a"))
        hass.services.has_service = MagicMock(return_value=True)
        hass.services.async_register = MagicMock()
        async_register_services(hass)
        hass.services.async_register.assert_not_called()

    def test_remove(self):
        hass, _ = _hass(_entry("a"))
        async_remove_services(hass)
        assert hass.services.async_remove.call_count == 3

    def test_schema_validates_filters(self):
        hass, _ = _hass(_entry("a"))
        _, kwargs = _register(hass)
        schema = kwargs[SERVICE_GET_ARRIVAL_BOARD]["schema"]

        assert schema({"crs": "rdg", "time_offset": "-10"})["time_offset"] == -10
        with pytest.raises(vol.Invalid):
            schema({"crs": "READING"})
        with pytest.raises(vol.Invalid):
            schema({"crs": "RDG", "time_window": 500})
        with pytest.raises(vol.Invalid):
            schema({})


class TestEntrySelection:
    async def test_no_entry_id_uses_the_first_commute(self):
        hass, apis = _hass(_entry("a"))
        handlers, _ = _register(hass)

        result = await handlers[SERVICE_GET_DEPARTURE_BOARD](_Call(crs="PAD"))

        assert result == {"status": 200}
        apis["a"].get_raw_departure_board.assert_awaited_once_with(
            "PAD",
            "dep-a",
            filter_crs=None,
            time_window=None,
            num_rows=None,
        )

    async def test_several_commutes_need_no_entry_id(self):
        hass, apis = _hass(_entry("a", "Morning"), _entry("b", "Evening"))
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_DEPARTURE_BOARD](_Call(crs="PAD"))

        apis["a"].get_raw_departure_board.assert_awaited_once()
        apis["b"].get_raw_departure_board.assert_not_awaited()

    async def test_entry_id_selects_that_commutes_key(self):
        hass, apis = _hass(_entry("a"), _entry("b"))
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_DEPARTURE_BOARD](_Call(crs="PAD", entry_id="b"))

        apis["b"].get_raw_departure_board.assert_awaited_once()
        assert apis["b"].get_raw_departure_board.await_args.args[1] == "dep-b"
        apis["a"].get_raw_departure_board.assert_not_awaited()

    async def test_unknown_entry_id(self):
        hass, _ = _hass(_entry("a"))
        handlers, _ = _register(hass)

        with pytest.raises(ServiceValidationError, match="No commute found"):
            await handlers[SERVICE_GET_DEPARTURE_BOARD](
                _Call(crs="PAD", entry_id="nope")
            )

    async def test_no_commutes_set_up(self):
        hass, _ = _hass()
        handlers, _ = _register(hass)

        with pytest.raises(ServiceValidationError, match="departure board"):
            await handlers[SERVICE_GET_DEPARTURE_BOARD](_Call(crs="PAD"))


class TestKeys:
    async def test_arrival_uses_own_key_without_delay_repay_enabled(self):
        hass, apis = _hass(_entry("a", **{CONF_ARRIVAL_API_KEY: "arr-a"}))
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_ARRIVAL_BOARD](
            _Call(crs="RDG", filter_crs="PAD", time_offset=-10, num_rows=5)
        )

        apis["a"].get_raw_arrival_board.assert_awaited_once_with(
            "RDG",
            "arr-a",
            filter_crs="PAD",
            time_offset=-10,
            time_window=None,
            num_rows=5,
        )

    async def test_arrival_key_shared_from_another_commute(self):
        hass, apis = _hass(
            _entry("a"), _entry("b", **{CONF_ARRIVAL_API_KEY: " shared "})
        )
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_ARRIVAL_BOARD](_Call(crs="RDG", entry_id="a"))

        assert apis["a"].get_raw_arrival_board.await_args.args[1] == "shared"

    async def test_service_details_key_shared_from_another_commute(self):
        hass, apis = _hass(
            _entry("a"), _entry("b", **{CONF_SERVICE_DETAILS_API_KEY: "det-b"})
        )
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_SERVICE_DETAILS](
            _Call(service_id="abc", entry_id="a")
        )

        apis["a"].get_raw_service_details.assert_awaited_once_with("abc", "det-b")

    async def test_lookup_without_entry_id_uses_whichever_commute_has_the_key(self):
        hass, apis = _hass(
            _entry("a"),
            _entry("b"),
            _entry("c", **{CONF_SERVICE_DETAILS_API_KEY: "det-c"}),
        )
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_SERVICE_DETAILS](_Call(service_id="abc"))

        # The first commute resolves the key another commute holds
        apis["a"].get_raw_service_details.assert_awaited_once_with("abc", "det-c")

    async def test_entry_without_the_key_is_skipped_when_none_chosen(self):
        hass, apis = _hass(_entry("a"), _entry("b", **{CONF_ARRIVAL_API_KEY: "arr"}))
        handlers, _ = _register(hass)

        await handlers[SERVICE_GET_ARRIVAL_BOARD](_Call(crs="RDG"))

        assert apis["a"].get_raw_arrival_board.await_args.args[1] == "arr"

    async def test_missing_key_names_the_product(self):
        hass, _ = _hass(_entry("a"))
        handlers, _ = _register(hass)

        with pytest.raises(ServiceValidationError, match="arrival board"):
            await handlers[SERVICE_GET_ARRIVAL_BOARD](_Call(crs="RDG"))
        with pytest.raises(ServiceValidationError, match="service details"):
            await handlers[SERVICE_GET_SERVICE_DETAILS](_Call(service_id="x"))

    async def test_connection_failure_is_an_ha_error(self):
        from custom_components.my_rail_commute.api import NationalRailAPIError

        hass, apis = _hass(_entry("a"))
        apis["a"].get_raw_departure_board.side_effect = NationalRailAPIError("down")
        handlers, _ = _register(hass)

        with pytest.raises(HomeAssistantError, match="down"):
            await handlers[SERVICE_GET_DEPARTURE_BOARD](_Call(crs="PAD"))


@pytest.fixture(name="api")
async def api_fixture(aiohttp_session):
    return NationalRailAPI("unused", aiohttp_session)


class TestRawRequests:
    async def test_departure_board_request_and_envelope(self, api):
        payload = {"GetStationBoardResult": {"locationName": "London Paddington"}}
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD"
                "?filterCrs=RDG&numRows=3&timeWindow=45",
                payload=payload,
            )
            result = await api.get_raw_departure_board(
                "pad", "secret", filter_crs="rdg", time_window=45, num_rows=3
            )
            request = list(mock.requests.values())[0][0]

        assert result == {
            "request": {
                "product": "departure",
                "url": f"{API_BASE_URL}/GetDepBoardWithDetails/PAD",
                "params": {"filterCrs": "RDG", "timeWindow": 45, "numRows": 3},
            },
            "status": 200,
            "body": payload,
        }
        assert request.kwargs["headers"]["x-apikey"] == "secret"
        assert "secret" not in str(result)

    async def test_unset_filters_are_not_sent(self, api):
        with aioresponses() as mock:
            mock.get(f"{API_BASE_URL}/GetDepBoardWithDetails/PAD", payload={})
            result = await api.get_raw_departure_board("PAD", "k")

        assert result["request"]["params"] == {}

    async def test_arrival_board_filters_from_origin(self, api):
        with aioresponses() as mock:
            mock.get(
                f"{ARRIVAL_API_BASE_URL}/GetArrBoardWithDetails/RDG"
                "?filterCrs=PAD&filterType=from&numRows=9&timeOffset=-10&timeWindow=45",
                payload={"ok": True},
            )
            result = await api.get_raw_arrival_board(
                "RDG", "k", "PAD", time_offset=-10, time_window=45, num_rows=9
            )

        assert result["request"]["params"]["filterType"] == "from"
        assert result["body"] == {"ok": True}

    async def test_arrival_board_without_origin_sends_no_filter_type(self, api):
        with aioresponses() as mock:
            mock.get(f"{ARRIVAL_API_BASE_URL}/GetArrBoardWithDetails/RDG", payload={})
            result = await api.get_raw_arrival_board("RDG", "k")

        assert result["request"]["params"] == {}

    async def test_service_id_is_quoted_into_the_path(self, api):
        with aioresponses() as mock:
            mock.get(
                f"{SERVICE_DETAILS_API_BASE_URL}/GetServiceDetails/a%2Fb%3Fc",
                payload={"ok": True},
            )
            result = await api.get_raw_service_details("a/b?c", "k")

        assert result["status"] == 200

    @pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
    async def test_http_errors_are_returned_as_data(self, api, status):
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/ZZZ",
                status=status,
                payload={"message": "nope"},
            )
            result = await api.get_raw_departure_board("ZZZ", "k")

        assert result["status"] == status
        assert result["body"] == {"message": "nope"}

    async def test_non_json_body_is_returned_as_text(self, api):
        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD",
                status=502,
                body="<html>Bad gateway</html>",
            )
            result = await api.get_raw_departure_board("PAD", "k")

        assert result["status"] == 502
        assert result["body"] == "<html>Bad gateway</html>"

    async def test_empty_body_is_none(self, api):
        with aioresponses() as mock:
            mock.get(f"{API_BASE_URL}/GetDepBoardWithDetails/PAD", status=204)
            result = await api.get_raw_departure_board("PAD", "k")

        assert result["body"] is None

    async def test_not_retried_or_throttled(self, api):
        api._rate_limit_per_minute = 1
        api._call_timestamps.extend([datetime.now()] * 5)
        with aioresponses() as mock:
            mock.get(f"{API_BASE_URL}/GetDepBoardWithDetails/PAD", status=500)
            result = await api.get_raw_departure_board("PAD", "k")
            calls = sum(len(v) for v in mock.requests.values())

        assert result["status"] == 500
        assert calls == 1
        assert len(api._call_timestamps) == 5

    async def test_connection_failure_raises(self, api):
        from aiohttp import ClientConnectionError

        from custom_components.my_rail_commute.api import NationalRailAPIError

        with aioresponses() as mock:
            mock.get(
                f"{API_BASE_URL}/GetDepBoardWithDetails/PAD",
                exception=ClientConnectionError("boom"),
            )
            with pytest.raises(NationalRailAPIError):
                await api.get_raw_departure_board("PAD", "k")
