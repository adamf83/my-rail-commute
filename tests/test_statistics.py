"""Tests for the CommuteStatisticsStore."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.my_rail_commute.statistics import CommuteStatisticsStore


def _make_store(load_return=None):
    """Return a CommuteStatisticsStore with a mocked HA Store."""
    hass = MagicMock()
    with patch(
        "custom_components.my_rail_commute.statistics.Store"
    ) as MockStore:
        instance = MockStore.return_value
        instance.async_load = AsyncMock(return_value=load_return)
        instance.async_save = AsyncMock(return_value=None)
        store = CommuteStatisticsStore(hass, "test_entry_id")
        store._store = instance
    return store


def _parsed_data(on_time=2, delayed=1, cancelled=0, services=None):
    """Build a minimal parsed_data dict as the coordinator produces."""
    if services is None:
        services = []
        for _ in range(on_time):
            services.append({"status": "on_time", "delay_minutes": 0, "is_cancelled": False})
        for dm in range(delayed):
            services.append({"status": "delayed", "delay_minutes": 5 * (dm + 1), "is_cancelled": False})
        for _ in range(cancelled):
            services.append({"status": "cancelled", "delay_minutes": 0, "is_cancelled": True})
    return {
        "on_time_count": on_time,
        "delayed_count": delayed,
        "cancelled_count": cancelled,
        "services_tracked": on_time + delayed + cancelled,
        "services": services,
    }


@pytest.mark.asyncio
async def test_load_no_data():
    """async_load with no persisted data initialises empty dict."""
    store = _make_store(load_return=None)
    await store.async_load()
    assert store._data == {}


@pytest.mark.asyncio
async def test_load_existing_data():
    """async_load restores previously persisted data."""
    existing = {"days": {"2026-05-17": {"on_time_count": 5, "delayed_count": 1,
                                         "cancelled_count": 0, "total_observations": 6,
                                         "total_delay_minutes": 10,
                                         "on_time_pct": 83.33, "avg_delay_minutes": 10.0}}}
    store = _make_store(load_return=existing)
    await store.async_load()
    assert "2026-05-17" in store._data
    assert store._data["2026-05-17"]["on_time_count"] == 5


@pytest.mark.asyncio
async def test_record_new_day():
    """First observation of the day creates a new entry and persists."""
    store = _make_store()
    await store.async_load()

    today = date.today().isoformat()
    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = date.fromisoformat(today)
        await store.async_record_observation(_parsed_data(on_time=2, delayed=1, cancelled=0))

    assert today in store._data
    day = store._data[today]
    assert day["on_time_count"] == 2
    assert day["delayed_count"] == 1
    assert day["cancelled_count"] == 0
    assert day["total_observations"] == 3
    store._store.async_save.assert_called_once()


@pytest.mark.asyncio
async def test_record_same_day_accumulates():
    """Multiple observations on the same day accumulate counts."""
    store = _make_store()
    await store.async_load()
    today = "2026-05-17"

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = date.fromisoformat(today)
        await store.async_record_observation(_parsed_data(on_time=2, delayed=1, cancelled=0))
        await store.async_record_observation(_parsed_data(on_time=1, delayed=0, cancelled=1))

    day = store._data[today]
    assert day["on_time_count"] == 3
    assert day["delayed_count"] == 1
    assert day["cancelled_count"] == 1
    assert day["total_observations"] == 6


@pytest.mark.asyncio
async def test_record_zero_services_skipped():
    """Observations with zero services_tracked are silently skipped."""
    store = _make_store()
    await store.async_load()
    today = "2026-05-17"

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = date.fromisoformat(today)
        await store.async_record_observation({"services_tracked": 0, "on_time_count": 0,
                                               "delayed_count": 0, "cancelled_count": 0,
                                               "services": []})

    assert today not in store._data
    store._store.async_save.assert_not_called()


def test_prune_removes_old_entries():
    """_prune_old_entries removes dates older than STATS_RETENTION_DAYS."""
    store = _make_store()
    store._data = {}

    cutoff_date = date.today() - timedelta(days=91)
    recent_date = date.today().isoformat()
    old_date = cutoff_date.isoformat()

    store._data[old_date] = {"on_time_count": 1, "total_observations": 1}
    store._data[recent_date] = {"on_time_count": 3, "total_observations": 3}

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = date.today()
        store._prune_old_entries()

    assert old_date not in store._data
    assert recent_date in store._data


def test_get_rolling_stats_no_data():
    """get_rolling_stats returns None values when there is no data."""
    store = _make_store()
    result = store.get_rolling_stats(7)
    assert result["on_time_pct"] is None
    assert result["avg_delay_minutes"] is None
    assert result["days_with_data"] == 0


def test_get_rolling_stats_with_data():
    """get_rolling_stats aggregates correctly across multiple days."""
    store = _make_store()
    today = date.today()

    store._data = {
        (today - timedelta(days=0)).isoformat(): {
            "on_time_count": 8, "delayed_count": 2, "cancelled_count": 0,
            "total_observations": 10, "total_delay_minutes": 20,
        },
        (today - timedelta(days=1)).isoformat(): {
            "on_time_count": 6, "delayed_count": 4, "cancelled_count": 0,
            "total_observations": 10, "total_delay_minutes": 40,
        },
    }

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = today
        result = store.get_rolling_stats(7)

    assert result["days_with_data"] == 2
    assert result["on_time_pct"] == 70.0  # 14/20 * 100
    assert result["avg_delay_minutes"] == 10.0  # 60 total delay / 6 delayed


def test_get_rolling_stats_excludes_days_outside_window():
    """get_rolling_stats only includes days within the requested window."""
    store = _make_store()
    today = date.today()

    store._data = {
        (today - timedelta(days=2)).isoformat(): {
            "on_time_count": 5, "delayed_count": 0, "cancelled_count": 0,
            "total_observations": 5, "total_delay_minutes": 0,
        },
        (today - timedelta(days=10)).isoformat(): {
            "on_time_count": 1, "delayed_count": 9, "cancelled_count": 0,
            "total_observations": 10, "total_delay_minutes": 90,
        },
    }

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = today
        result = store.get_rolling_stats(7)

    assert result["days_with_data"] == 1
    assert result["on_time_pct"] == 100.0


def test_get_best_and_worst_days_no_data():
    """get_best_and_worst_days returns None values when there is no data."""
    store = _make_store()
    result = store.get_best_and_worst_days(30)
    assert result["worst_day"] is None
    assert result["best_day"] is None


def test_get_best_and_worst_days():
    """get_best_and_worst_days identifies correct best and worst days."""
    store = _make_store()
    today = date.today()

    good_day = (today - timedelta(days=1)).isoformat()
    bad_day = (today - timedelta(days=2)).isoformat()

    store._data = {
        good_day: {"on_time_pct": 95.0, "avg_delay_minutes": 2.0, "total_observations": 10},
        bad_day: {"on_time_pct": 30.0, "avg_delay_minutes": 15.0, "total_observations": 10},
    }

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = today
        result = store.get_best_and_worst_days(30)

    assert result["best_day"]["date"] == good_day
    assert result["worst_day"]["date"] == bad_day


def test_get_today_stats_empty():
    """get_today_stats returns empty dict when no data for today."""
    store = _make_store()
    today = date.today()
    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value.isoformat.return_value = (
            today - timedelta(days=5)
        ).isoformat()
        result = store.get_today_stats()
    assert result == {}


@pytest.mark.asyncio
async def test_on_time_pct_computed_correctly():
    """on_time_pct is stored and computed correctly after recording."""
    store = _make_store()
    await store.async_load()
    today = "2026-05-17"

    with patch("custom_components.my_rail_commute.statistics.dt_util") as mock_dt:
        mock_dt.now.return_value.date.return_value = date.fromisoformat(today)
        await store.async_record_observation(_parsed_data(on_time=3, delayed=1, cancelled=0))

    day = store._data[today]
    assert day["on_time_pct"] == 75.0  # 3/4 * 100
    assert day["avg_delay_minutes"] == 5.0  # 1 delayed service with 5 min delay
