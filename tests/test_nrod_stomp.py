"""Tests for the NROD STOMP frame parser and shared feed manager."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.my_rail_commute.const import (
    NROD_RECONNECT_BACKOFF_FACTOR,
    NROD_RECONNECT_INITIAL_DELAY,
    NROD_RECONNECT_MAX_DELAY,
)
from custom_components.my_rail_commute.nrod_stomp import (
    EVENT_ARRIVAL,
    EVENT_DEPARTURE,
    EVENT_PASS,
    CancellationEvent,
    MovementEvent,
    NrodFeedManager,
    parse_stomp_frame,
)


def _movement_record(
    msg_type="0003",
    event_type="DEPARTURE",
    train_id="1A23",
    loc_stanox="87701",
    planned_timestamp="1751702100000",
    actual_timestamp="1751702160000",
    timetable_variation="1",
    variation_status="LATE",
    platform="9",
    toc_id="23",
):
    return {
        "header": {"msg_type": msg_type},
        "body": {
            "event_type": event_type,
            "train_id": train_id,
            "loc_stanox": loc_stanox,
            "planned_timestamp": planned_timestamp,
            "actual_timestamp": actual_timestamp,
            "timetable_variation": timetable_variation,
            "variation_status": variation_status,
            "platform": platform,
            "toc_id": toc_id,
        },
    }


def _cancellation_record(
    train_id="1A23",
    loc_stanox="87701",
    dep_timestamp="1751702100000",
    canx_timestamp="1751702000000",
    canx_reason_code="YI",
    canx_type="AT ORIGIN",
):
    return {
        "header": {"msg_type": "0002"},
        "body": {
            "train_info": {
                "train_id": train_id,
                "loc_stanox": loc_stanox,
                "dep_timestamp": dep_timestamp,
                "canx_timestamp": canx_timestamp,
                "canx_reason_code": canx_reason_code,
                "canx_type": canx_type,
            }
        },
    }


class TestParseStompFrame:
    """Tests for the pure parse_stomp_frame function."""

    def test_parses_departure_movement(self):
        events = parse_stomp_frame(json.dumps([_movement_record()]))
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, MovementEvent)
        assert event.event_type == EVENT_DEPARTURE
        assert event.train_id == "1A23"
        assert event.stanox == "87701"
        assert event.delay_minutes == 1
        assert event.platform == "9"
        assert event.toc == "23"
        assert event.service_date  # derived from planned_timestamp

    def test_parses_arrival_movement(self):
        events = parse_stomp_frame(
            json.dumps([_movement_record(event_type="ARRIVAL", loc_stanox="88616")])
        )
        assert events[0].event_type == EVENT_ARRIVAL
        assert events[0].stanox == "88616"

    def test_parses_pass_movement(self):
        events = parse_stomp_frame(json.dumps([_movement_record(event_type="PASS")]))
        assert events[0].event_type == EVENT_PASS

    def test_early_variation_is_negative_delay(self):
        events = parse_stomp_frame(
            json.dumps(
                [_movement_record(timetable_variation="3", variation_status="EARLY")]
            )
        )
        assert events[0].delay_minutes == -3

    def test_on_time_variation_is_zero_delay(self):
        events = parse_stomp_frame(
            json.dumps(
                [_movement_record(timetable_variation="0", variation_status="ON TIME")]
            )
        )
        assert events[0].delay_minutes == 0

    def test_parses_cancellation(self):
        events = parse_stomp_frame(json.dumps([_cancellation_record()]))
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, CancellationEvent)
        assert event.train_id == "1A23"
        assert event.stanox == "87701"
        assert event.reason_code == "YI"
        assert event.cancelled_at_origin is True

    def test_cancellation_not_at_origin(self):
        events = parse_stomp_frame(
            json.dumps([_cancellation_record(canx_type="EN ROUTE")])
        )
        assert events[0].cancelled_at_origin is False

    def test_unknown_msg_type_ignored(self):
        record = {"header": {"msg_type": "0001"}, "body": {}}
        assert parse_stomp_frame(json.dumps([record])) == []

    def test_unknown_event_type_ignored(self):
        events = parse_stomp_frame(json.dumps([_movement_record(event_type="REINSTATEMENT")]))
        assert events == []

    def test_missing_required_fields_ignored(self):
        record = _movement_record()
        del record["body"]["train_id"]
        assert parse_stomp_frame(json.dumps([record])) == []

    def test_malformed_json_returns_empty(self):
        assert parse_stomp_frame("not json") == []

    def test_non_list_json_returns_empty(self):
        assert parse_stomp_frame(json.dumps({"not": "a list"})) == []

    def test_non_dict_record_is_skipped(self):
        events = parse_stomp_frame(json.dumps(["not a dict", _movement_record()]))
        assert len(events) == 1
        assert events[0].train_id == "1A23"

    def test_mixed_valid_and_invalid_records(self):
        records = [
            _movement_record(train_id="1A23"),
            {"header": {"msg_type": "9999"}, "body": {}},
            _cancellation_record(train_id="1B45"),
        ]
        events = parse_stomp_frame(json.dumps(records))
        assert len(events) == 2
        assert events[0].train_id == "1A23"
        assert events[1].train_id == "1B45"


def _make_hass():
    """Return a hass mock whose executor/loop hooks run callables inline."""
    hass = MagicMock()
    hass.loop.call_soon_threadsafe = MagicMock(side_effect=lambda fn, *a: fn(*a))
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *a: fn(*a))
    hass.async_create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro))
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name: asyncio.ensure_future(coro)
    )
    return hass


@pytest.mark.asyncio
async def test_acquire_connects_only_once_for_multiple_entries():
    """A second entry acquiring the feed must not open a second connection."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")
    manager._connect_sync = MagicMock(
        side_effect=lambda generation: setattr(manager, "_connection", MagicMock())
    )

    callback_a = MagicMock()
    callback_b = MagicMock()

    await manager.async_acquire("entry_a", callback_a, {"87701"})
    await manager.async_acquire("entry_b", callback_b, {"88616"})

    manager._connect_sync.assert_called_once()
    assert manager.has_subscribers


@pytest.mark.asyncio
async def test_release_keeps_connection_while_other_entry_remains():
    """Releasing one of two entries must not tear down the shared connection."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")
    manager._connect_sync = MagicMock(
        side_effect=lambda generation: setattr(manager, "_connection", MagicMock())
    )
    manager._disconnect_sync = MagicMock()

    await manager.async_acquire("entry_a", MagicMock(), {"87701"})
    await manager.async_acquire("entry_b", MagicMock(), {"88616"})

    await manager.async_release("entry_a")

    manager._disconnect_sync.assert_not_called()
    assert manager.has_subscribers


@pytest.mark.asyncio
async def test_release_last_entry_disconnects():
    """Releasing the last entry tears down the shared connection."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")
    manager._connect_sync = MagicMock(
        side_effect=lambda generation: setattr(manager, "_connection", MagicMock())
    )
    manager._disconnect_sync = MagicMock()

    await manager.async_acquire("entry_a", MagicMock(), {"87701"})
    await manager.async_release("entry_a")

    manager._disconnect_sync.assert_called_once()
    assert not manager.has_subscribers


@pytest.mark.asyncio
async def test_dispatch_routes_by_stanox_only_to_matching_subscriber():
    """An incoming event is only delivered to the subscriber for its STANOX."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")
    manager._connect_sync = MagicMock()

    callback_a = MagicMock()
    callback_b = MagicMock()
    await manager.async_acquire("entry_a", callback_a, {"87701"})
    await manager.async_acquire("entry_b", callback_b, {"88616"})

    frame_body = json.dumps([_movement_record(loc_stanox="87701")])
    manager._on_message(MagicMock(body=frame_body))

    callback_a.assert_called_once()
    callback_b.assert_not_called()
    assert manager.last_message_at is not None


def test_connect_sync_disconnects_stale_attempt():
    """A connect that finishes after the caller gave up must tear itself down.

    asyncio.wait_for can't stop the blocking connect running in the executor
    thread, so a timed-out attempt keeps running in the background. If it
    later succeeds, it must not be adopted as the live connection (NROD only
    allows one login per account, so a stray leftover session would knock a
    subsequent, genuine connection off the feed).
    """
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")

    stale_connection = MagicMock()
    with patch(
        "custom_components.my_rail_commute.nrod_stomp.stomp.Connection",
        return_value=stale_connection,
    ):
        manager._connect_generation = 2  # a newer attempt has since started
        manager._connect_sync(1)  # this attempt was generation 1 - now stale

    stale_connection.disconnect.assert_called_once()
    assert manager._connection is None


@pytest.mark.asyncio
async def test_reconnect_loop_backs_off_and_resets_on_success():
    """Reconnect attempts back off exponentially and reset the delay once connected."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")

    attempts = {"count": 0}

    async def _fake_connect():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("simulated failure")
        manager._on_connected()  # simulates the real STOMP connect callback

    manager._async_connect = _fake_connect
    manager._entry_callback["entry_a"] = MagicMock()  # has_subscribers -> True
    manager._stopped = False

    sleep_calls = []

    async def _fake_sleep(delay):
        sleep_calls.append(delay)

    with patch(
        "custom_components.my_rail_commute.nrod_stomp.asyncio.sleep",
        side_effect=_fake_sleep,
    ):
        await manager._async_reconnect_loop()

    assert attempts["count"] == 3
    assert sleep_calls == [
        NROD_RECONNECT_INITIAL_DELAY,
        NROD_RECONNECT_INITIAL_DELAY * NROD_RECONNECT_BACKOFF_FACTOR,
        NROD_RECONNECT_INITIAL_DELAY * NROD_RECONNECT_BACKOFF_FACTOR**2,
    ]
    # A successful connect resets the backoff delay for next time
    assert manager._reconnect_delay == NROD_RECONNECT_INITIAL_DELAY
    assert manager.connected is True


@pytest.mark.asyncio
async def test_reconnect_delay_caps_at_max():
    """The reconnect delay never exceeds NROD_RECONNECT_MAX_DELAY."""
    hass = _make_hass()
    manager = NrodFeedManager(hass, "user", "pass")
    manager._reconnect_delay = NROD_RECONNECT_MAX_DELAY / (NROD_RECONNECT_BACKOFF_FACTOR - 0.5)

    async def _always_fail():
        raise ConnectionError("simulated failure")

    manager._async_connect = _always_fail
    manager._entry_callback["entry_a"] = MagicMock()
    manager._stopped = False

    call_count = {"n": 0}

    async def _fake_sleep(delay):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            manager._stopped = True  # stop the loop after a couple of iterations

    with patch(
        "custom_components.my_rail_commute.nrod_stomp.asyncio.sleep",
        side_effect=_fake_sleep,
    ):
        await manager._async_reconnect_loop()

    assert manager._reconnect_delay <= NROD_RECONNECT_MAX_DELAY
