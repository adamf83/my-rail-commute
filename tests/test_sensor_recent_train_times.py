"""Tests for RecentTrainTimesSensor and NrodFeedConnectedBinarySensor."""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.my_rail_commute.binary_sensor import (
    NrodFeedConnectedBinarySensor,
)
from custom_components.my_rail_commute.const import (
    ATTR_FEED_CONNECTED,
    ATTR_FEED_LAST_MESSAGE_AT,
    ATTR_JOURNEYS_RECORDED_TODAY,
    ATTR_LAST_JOURNEY,
    ATTR_RECENT_JOURNEYS,
)
from custom_components.my_rail_commute.coordinator import (
    NationalRailDataUpdateCoordinator,
)
from custom_components.my_rail_commute.sensor import RecentTrainTimesSensor


def _make_coordinator(journeys_store=None, feed_manager=None):
    coordinator = MagicMock(spec=NationalRailDataUpdateCoordinator)
    coordinator.origin = "PAD"
    coordinator.destination = "RDG"
    coordinator.journeys_store = journeys_store
    coordinator.feed_manager = feed_manager
    return coordinator


def _make_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"commute_name": "Test Commute"}
    return entry


class TestRecentTrainTimesSensor:
    def test_native_value_none_when_feature_disabled(self):
        coordinator = _make_coordinator(journeys_store=None)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_native_value_no_recent_journeys(self):
        store = MagicMock()
        store.get_last_journey.return_value = None
        coordinator = _make_coordinator(journeys_store=store)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())
        assert sensor.native_value == "No recent journeys"

    def test_native_value_on_time(self):
        store = MagicMock()
        store.get_last_journey.return_value = {"is_cancelled": False, "delay_minutes": 0}
        coordinator = _make_coordinator(journeys_store=store)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())
        assert sensor.native_value == "On Time"

    def test_native_value_delayed(self):
        store = MagicMock()
        store.get_last_journey.return_value = {"is_cancelled": False, "delay_minutes": 7}
        coordinator = _make_coordinator(journeys_store=store)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())
        assert sensor.native_value == "7 min late"

    def test_native_value_cancelled(self):
        store = MagicMock()
        store.get_last_journey.return_value = {"is_cancelled": True, "delay_minutes": None}
        coordinator = _make_coordinator(journeys_store=store)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())
        assert sensor.native_value == "Cancelled"

    def test_extra_state_attributes(self):
        store = MagicMock()
        journeys = [{"train_id": "1A23"}]
        store.get_recent_journeys.return_value = journeys
        store.get_last_journey.return_value = journeys[0]
        store.count_for_date.return_value = 3

        feed_manager = MagicMock()
        feed_manager.connected = True
        feed_manager.last_message_at = "2026-07-05T08:00:00+00:00"

        coordinator = _make_coordinator(journeys_store=store, feed_manager=feed_manager)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())

        attrs = sensor.extra_state_attributes
        assert attrs[ATTR_RECENT_JOURNEYS] == journeys
        assert attrs[ATTR_LAST_JOURNEY] == journeys[0]
        assert attrs[ATTR_JOURNEYS_RECORDED_TODAY] == 3
        assert attrs[ATTR_FEED_CONNECTED] is True
        assert attrs[ATTR_FEED_LAST_MESSAGE_AT] == "2026-07-05T08:00:00+00:00"

    def test_extra_state_attributes_no_feed_manager(self):
        """Feed connectivity attrs degrade gracefully if the feed never started."""
        store = MagicMock()
        store.get_recent_journeys.return_value = []
        store.get_last_journey.return_value = None
        store.count_for_date.return_value = 0

        coordinator = _make_coordinator(journeys_store=store, feed_manager=None)
        sensor = RecentTrainTimesSensor(coordinator, _make_entry())

        attrs = sensor.extra_state_attributes
        assert attrs[ATTR_FEED_CONNECTED] is False
        assert attrs[ATTR_FEED_LAST_MESSAGE_AT] is None


class TestNrodFeedConnectedBinarySensor:
    def test_is_on_reflects_feed_connected(self):
        feed_manager = MagicMock()
        feed_manager.connected = True
        coordinator = _make_coordinator(feed_manager=feed_manager)
        sensor = NrodFeedConnectedBinarySensor(coordinator, _make_entry())
        assert sensor.is_on is True

    def test_is_on_false_when_disconnected(self):
        feed_manager = MagicMock()
        feed_manager.connected = False
        coordinator = _make_coordinator(feed_manager=feed_manager)
        sensor = NrodFeedConnectedBinarySensor(coordinator, _make_entry())
        assert sensor.is_on is False

    def test_is_on_false_when_no_feed_manager(self):
        coordinator = _make_coordinator(feed_manager=None)
        sensor = NrodFeedConnectedBinarySensor(coordinator, _make_entry())
        assert sensor.is_on is False

    def test_extra_state_attributes(self):
        feed_manager = MagicMock()
        feed_manager.last_message_at = "2026-07-05T08:00:00+00:00"
        coordinator = _make_coordinator(feed_manager=feed_manager)
        sensor = NrodFeedConnectedBinarySensor(coordinator, _make_entry())
        assert sensor.extra_state_attributes[ATTR_FEED_LAST_MESSAGE_AT] == (
            "2026-07-05T08:00:00+00:00"
        )
