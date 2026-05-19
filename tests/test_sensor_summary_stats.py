"""Tests for CommuteSummarySensor historical stats in extra_state_attributes."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.my_rail_commute.const import (
    ATTR_AVG_DELAY_7D,
    ATTR_BEST_DAY,
    ATTR_DAILY_BREAKDOWN,
    ATTR_ON_TIME_PCT_7D,
    ATTR_ON_TIME_PCT_30D,
    ATTR_ON_TIME_PCT_TODAY,
    ATTR_WORST_DAY,
)
from custom_components.my_rail_commute.sensor import CommuteSummarySensor


def _make_sensor(stats_store=None):
    """Return a CommuteSummarySensor backed by a minimal mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = {
        "origin": "LBG",
        "origin_name": "London Bridge",
        "destination": "WYT",
        "destination_name": "Whyteleafe",
        "time_window": 60,
        "services_tracked": 2,
        "total_services_found": 2,
        "on_time_count": 2,
        "delayed_count": 0,
        "cancelled_count": 0,
        "last_updated": "2026-05-19T20:28:00+00:00",
        "next_update": "2026-05-19T20:30:00+00:00",
        "multi_destination": False,
        "services": [
            {
                "scheduled_departure": "20:40",
                "expected_departure": "20:40",
                "platform": "1",
                "operator": "Southern",
                "service_id": "svc1",
                "status": "on_time",
                "delay_minutes": 0,
                "is_cancelled": False,
                "calling_points": [],
                "estimated_arrival": None,
                "scheduled_arrival": None,
                "destination": "Whyteleafe",
            },
        ],
    }
    coordinator.num_services = 3
    coordinator.stats_store = stats_store

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"commute_name": "Test Commute"}

    sensor = CommuteSummarySensor(coordinator, entry)
    sensor.hass = MagicMock()
    sensor.async_write_ha_state = MagicMock()
    return sensor


def _make_stats_store(
    on_time_pct_today=97.19, on_time_pct_7d=98.1, on_time_pct_30d=98.1, avg_delay_7d=3.4
):
    """Return a mock stats store with preset values."""
    store = MagicMock()
    store.get_today_stats.return_value = {"on_time_pct": on_time_pct_today}
    store.get_rolling_stats.side_effect = lambda days: (
        {
            "on_time_pct": on_time_pct_7d,
            "avg_delay_minutes": avg_delay_7d,
            "days_with_data": 7,
        }
        if days == 7
        else {
            "on_time_pct": on_time_pct_30d,
            "avg_delay_minutes": avg_delay_7d,
            "days_with_data": 30,
        }
    )
    store.get_best_and_worst_days.return_value = {
        "best_day": {
            "date": "2026-05-18",
            "on_time_pct": 100.0,
            "avg_delay_minutes": 0.0,
        },
        "worst_day": {
            "date": "2026-05-17",
            "on_time_pct": 94.89,
            "avg_delay_minutes": 5.0,
        },
    }
    store.get_daily_breakdown.return_value = [
        {
            "date": "2026-05-17",
            "on_time_pct": 94.89,
            "avg_delay_minutes": 5.0,
            "total_observations": 10,
        },
        {
            "date": "2026-05-18",
            "on_time_pct": 100.0,
            "avg_delay_minutes": 0.0,
            "total_observations": 10,
        },
        {
            "date": "2026-05-19",
            "on_time_pct": 97.19,
            "avg_delay_minutes": 3.4,
            "total_observations": 10,
        },
    ]
    return store


def test_summary_includes_historical_stats_when_store_present():
    """CommuteSummarySensor attrs include historical stats when stats_store is set."""
    store = _make_stats_store()
    sensor = _make_sensor(stats_store=store)

    attrs = sensor.extra_state_attributes

    assert ATTR_ON_TIME_PCT_TODAY in attrs
    assert ATTR_ON_TIME_PCT_7D in attrs
    assert ATTR_ON_TIME_PCT_30D in attrs
    assert ATTR_AVG_DELAY_7D in attrs
    assert ATTR_BEST_DAY in attrs
    assert ATTR_WORST_DAY in attrs
    assert ATTR_DAILY_BREAKDOWN in attrs


def test_summary_historical_stats_values_match_store():
    """CommuteSummarySensor historical stats match what the store returns."""
    store = _make_stats_store(
        on_time_pct_today=97.19,
        on_time_pct_7d=98.1,
        on_time_pct_30d=98.1,
        avg_delay_7d=3.4,
    )
    sensor = _make_sensor(stats_store=store)

    attrs = sensor.extra_state_attributes

    assert attrs[ATTR_ON_TIME_PCT_TODAY] == 97.19
    assert attrs[ATTR_ON_TIME_PCT_7D] == 98.1
    assert attrs[ATTR_ON_TIME_PCT_30D] == 98.1
    assert attrs[ATTR_AVG_DELAY_7D] == 3.4
    assert attrs[ATTR_BEST_DAY]["date"] == "2026-05-18"
    assert attrs[ATTR_WORST_DAY]["date"] == "2026-05-17"
    assert len(attrs[ATTR_DAILY_BREAKDOWN]) == 3


def test_summary_no_historical_stats_when_store_absent():
    """CommuteSummarySensor attrs omit historical stats when stats_store is None."""
    sensor = _make_sensor(stats_store=None)

    attrs = sensor.extra_state_attributes

    assert ATTR_ON_TIME_PCT_TODAY not in attrs
    assert ATTR_ON_TIME_PCT_7D not in attrs
    assert ATTR_ON_TIME_PCT_30D not in attrs
    assert ATTR_AVG_DELAY_7D not in attrs
    assert ATTR_BEST_DAY not in attrs
    assert ATTR_WORST_DAY not in attrs
    assert ATTR_DAILY_BREAKDOWN not in attrs


def test_summary_different_routes_expose_different_stats():
    """Two sensors with different stores return route-specific stats (toggle correctness)."""
    store_outbound = _make_stats_store(
        on_time_pct_today=97.19, on_time_pct_7d=98.1, avg_delay_7d=3.4
    )
    store_return = _make_stats_store(
        on_time_pct_today=85.0, on_time_pct_7d=87.5, avg_delay_7d=6.2
    )

    sensor_outbound = _make_sensor(stats_store=store_outbound)
    sensor_return = _make_sensor(stats_store=store_return)

    attrs_out = sensor_outbound.extra_state_attributes
    attrs_ret = sensor_return.extra_state_attributes

    assert attrs_out[ATTR_ON_TIME_PCT_TODAY] != attrs_ret[ATTR_ON_TIME_PCT_TODAY]
    assert attrs_out[ATTR_ON_TIME_PCT_TODAY] == 97.19
    assert attrs_ret[ATTR_ON_TIME_PCT_TODAY] == 85.0
    assert attrs_out[ATTR_AVG_DELAY_7D] == 3.4
    assert attrs_ret[ATTR_AVG_DELAY_7D] == 6.2
