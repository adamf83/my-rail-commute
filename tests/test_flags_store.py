"""Tests for FlagsStore persistent storage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.my_rail_commute.const import (
    EVENT_FLAGS_UPDATED,
    FLAGS_STORAGE_KEY_PREFIX,
)
from custom_components.my_rail_commute.helpers import FlagsStore


@pytest.fixture
def flags_store(hass: HomeAssistant) -> FlagsStore:
    """Return a FlagsStore instance for testing."""
    return FlagsStore(hass, "test_commute")


async def test_initial_state_empty(flags_store: FlagsStore) -> None:
    """FlagsStore starts with empty lists before loading."""
    assert flags_store.get_favourites() == []
    assert flags_store.get_flagged() == []


async def test_load_empty_storage(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Loading from empty storage leaves lists empty."""
    with patch.object(flags_store._store, "async_load", return_value=None):
        await flags_store.async_load()
    assert flags_store.get_favourites() == []
    assert flags_store.get_flagged() == []


async def test_load_persisted_data(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Loading from storage restores persisted favourites and flagged trains."""
    stored = {
        "favourites": [{"scheduled_departure": "08:15", "operator": "GWR", "added_at": "2024-01-01T08:00:00+00:00"}],
        "flagged": [{"service_id": "abc123", "scheduled_departure": "08:30", "flagged_at": "2024-01-01T08:25:00+00:00"}],
    }
    with patch.object(flags_store._store, "async_load", return_value=stored):
        await flags_store.async_load()

    assert len(flags_store.get_favourites()) == 1
    assert flags_store.get_favourites()[0]["scheduled_departure"] == "08:15"
    assert len(flags_store.get_flagged()) == 1
    assert flags_store.get_flagged()[0]["service_id"] == "abc123"


async def test_add_favourite(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Adding a favourite stores it and fires the update event."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        fired_events = []
        hass.bus.async_listen(EVENT_FLAGS_UPDATED, fired_events.append)

        await flags_store.async_add_favourite("08:15", "GWR")

    favourites = flags_store.get_favourites()
    assert len(favourites) == 1
    assert favourites[0]["scheduled_departure"] == "08:15"
    assert favourites[0]["operator"] == "GWR"
    assert "added_at" in favourites[0]


async def test_add_favourite_deduplicated(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Adding the same departure time twice only stores one entry."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")
        await flags_store.async_add_favourite("08:15")

    assert len(flags_store.get_favourites()) == 1


async def test_add_favourite_without_operator(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Adding a favourite without an operator omits the operator key."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")

    fav = flags_store.get_favourites()[0]
    assert "operator" not in fav


async def test_remove_favourite(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Removing a favourite by departure time deletes that entry."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")
        await flags_store.async_add_favourite("09:00")
        await flags_store.async_remove_favourite("08:15")

    departures = [f["scheduled_departure"] for f in flags_store.get_favourites()]
    assert "08:15" not in departures
    assert "09:00" in departures


async def test_remove_nonexistent_favourite(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Removing a non-existent favourite does not raise."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_remove_favourite("08:15")

    assert flags_store.get_favourites() == []


async def test_clear_favourites(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Clearing all favourites leaves the list empty."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")
        await flags_store.async_add_favourite("09:00")
        await flags_store.async_clear_favourites()

    assert flags_store.get_favourites() == []


async def test_flag_train(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Flagging a train stores it with all provided fields."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_flag_train("abc123", "08:30", "GWR", "delay")

    flagged = flags_store.get_flagged()
    assert len(flagged) == 1
    assert flagged[0]["service_id"] == "abc123"
    assert flagged[0]["scheduled_departure"] == "08:30"
    assert flagged[0]["operator"] == "GWR"
    assert flagged[0]["reason"] == "delay"
    assert "flagged_at" in flagged[0]


async def test_flag_train_deduplicated(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Flagging the same service_id twice only stores one entry."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_flag_train("abc123", "08:30")
        await flags_store.async_flag_train("abc123", "08:30")

    assert len(flags_store.get_flagged()) == 1


async def test_flag_train_without_optional_fields(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Flagging without optional fields omits those keys."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_flag_train("abc123", "08:30")

    flagged = flags_store.get_flagged()[0]
    assert "operator" not in flagged
    assert "reason" not in flagged


async def test_unflag_train(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Unflagging a train by service_id removes only that entry."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_flag_train("abc123", "08:30")
        await flags_store.async_flag_train("def456", "09:00")
        await flags_store.async_unflag_train("abc123")

    ids = [f["service_id"] for f in flags_store.get_flagged()]
    assert "abc123" not in ids
    assert "def456" in ids


async def test_clear_flagged(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Clearing all flagged trains leaves the list empty."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_flag_train("abc123", "08:30")
        await flags_store.async_flag_train("def456", "09:00")
        await flags_store.async_clear_flagged()

    assert flags_store.get_flagged() == []


async def test_flags_and_favourites_independent(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Clearing favourites does not affect flagged trains and vice versa."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")
        await flags_store.async_flag_train("abc123", "08:30")
        await flags_store.async_clear_favourites()

    assert flags_store.get_favourites() == []
    assert len(flags_store.get_flagged()) == 1


async def test_get_returns_copy(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """get_favourites/get_flagged return copies, not the internal list."""
    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")

    copy = flags_store.get_favourites()
    copy.clear()
    assert len(flags_store.get_favourites()) == 1


async def test_storage_key_uses_base(hass: HomeAssistant) -> None:
    """The store key is derived from the commute base name."""
    store = FlagsStore(hass, "morning_commute")
    assert store._store.key == f"{FLAGS_STORAGE_KEY_PREFIX}morning_commute"


async def test_save_fires_event(hass: HomeAssistant, flags_store: FlagsStore) -> None:
    """Each mutation fires EVENT_FLAGS_UPDATED on the HA event bus."""
    fired = []
    hass.bus.async_listen(EVENT_FLAGS_UPDATED, fired.append)

    with patch.object(flags_store._store, "async_save", new_callable=AsyncMock):
        await flags_store.async_add_favourite("08:15")
        await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["base"] == "test_commute"
