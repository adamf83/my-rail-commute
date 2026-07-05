"""Tests for the RecentJourneysStore persistence log."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util
import pytest

from custom_components.my_rail_commute.const import (
    RECENT_JOURNEYS_MAX_STORED,
    RECENT_JOURNEYS_RETENTION_DAYS,
)
from custom_components.my_rail_commute.journey_store import RecentJourneysStore


def _make_store(load_return=None):
    """Return a RecentJourneysStore with a mocked HA Store."""
    hass = MagicMock()
    with patch("custom_components.my_rail_commute.journey_store.Store") as MockStore:
        instance = MockStore.return_value
        instance.async_load = AsyncMock(return_value=load_return)
        instance.async_save = AsyncMock(return_value=None)
        store = RecentJourneysStore(hass, "test_entry_id")
        store._store = instance
    return store


def _journey(train_id="1A23", service_date="2026-07-05", recorded_at="2026-07-05T08:35:00+00:00"):
    return {
        "train_id": train_id,
        "service_date": service_date,
        "recorded_at": recorded_at,
        "is_cancelled": False,
        "delay_minutes": 0,
    }


@pytest.mark.asyncio
async def test_load_no_data():
    store = _make_store(load_return=None)
    await store.async_load()
    assert store.get_recent_journeys() == []
    assert store.get_last_journey() is None


@pytest.mark.asyncio
async def test_add_journey_persists_and_returns_newest_first():
    store = _make_store(load_return=None)
    await store.async_load()

    await store.async_add_journey(_journey(train_id="1A01", recorded_at="2026-07-05T08:00:00+00:00"))
    await store.async_add_journey(_journey(train_id="1A02", recorded_at="2026-07-05T09:00:00+00:00"))

    recent = store.get_recent_journeys()
    assert [j["train_id"] for j in recent] == ["1A02", "1A01"]
    assert store.get_last_journey()["train_id"] == "1A02"
    store._store.async_save.assert_called()


@pytest.mark.asyncio
async def test_get_recent_journeys_respects_limit():
    store = _make_store(load_return=None)
    await store.async_load()

    for i in range(5):
        await store.async_add_journey(
            _journey(train_id=f"1A{i:02d}", recorded_at=f"2026-07-05T0{i}:00:00+00:00")
        )

    limited = store.get_recent_journeys(limit=2)
    assert [j["train_id"] for j in limited] == ["1A04", "1A03"]


@pytest.mark.asyncio
async def test_count_for_date():
    store = _make_store(load_return=None)
    await store.async_load()

    await store.async_add_journey(_journey(service_date="2026-07-05"))
    await store.async_add_journey(_journey(train_id="1B45", service_date="2026-07-05"))
    await store.async_add_journey(_journey(train_id="1C67", service_date="2026-07-04"))

    assert store.count_for_date("2026-07-05") == 2
    assert store.count_for_date("2026-07-04") == 1
    assert store.count_for_date("2026-07-06") == 0


@pytest.mark.asyncio
async def test_prune_removes_entries_beyond_retention_window():
    today = dt_util.now().date()
    stale_date = (today - timedelta(days=RECENT_JOURNEYS_RETENTION_DAYS + 5)).isoformat()
    fresh_date = today.isoformat()

    existing = {
        "journeys": [
            _journey(train_id="OLD", service_date=stale_date, recorded_at=f"{stale_date}T08:00:00+00:00"),
            _journey(train_id="NEW", service_date=fresh_date, recorded_at=f"{fresh_date}T08:00:00+00:00"),
        ]
    }
    store = _make_store(load_return=existing)
    await store.async_load()

    remaining = store.get_recent_journeys(limit=10)
    assert [j["train_id"] for j in remaining] == ["NEW"]


@pytest.mark.asyncio
async def test_prune_caps_total_stored_count():
    store = _make_store(load_return=None)
    await store.async_load()

    for i in range(RECENT_JOURNEYS_MAX_STORED + 10):
        await store.async_add_journey(
            _journey(train_id=f"T{i}", recorded_at=f"2026-07-05T00:00:{i % 60:02d}+00:00")
        )

    assert len(store.get_recent_journeys(limit=RECENT_JOURNEYS_MAX_STORED + 50)) == RECENT_JOURNEYS_MAX_STORED
