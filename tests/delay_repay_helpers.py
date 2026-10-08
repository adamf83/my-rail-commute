"""Shared helpers for Delay Repay tests."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Any
from unittest.mock import patch

from custom_components.my_rail_commute.delay_repay.schemes import (
    SchemeSet,
    build_scheme_set,
)
from custom_components.my_rail_commute.delay_repay.store import DelayRepayStore
from custom_components.my_rail_commute.delay_repay.tracker import DelayRepayTracker

# Thursday 8 Oct 2026, 22:30
NOW = datetime(2026, 10, 8, 22, 30, tzinfo=UTC)


class FakeHAStore:
    """In-memory stand-in for homeassistant.helpers.storage.Store."""

    saved: dict[str, Any] = {}
    save_count = 0

    def __init__(self, hass, version, key) -> None:
        self.key = key

    async def async_load(self):
        data = FakeHAStore.saved.get(self.key)
        return deepcopy(data)

    async def async_save(self, data) -> None:
        FakeHAStore.saved[self.key] = json.loads(json.dumps(data))
        FakeHAStore.save_count += 1


@contextmanager
def fake_storage():
    """Patch the HA Store used by DelayRepayStore with an in-memory fake."""
    FakeHAStore.saved = {}
    FakeHAStore.save_count = 0
    with patch(
        "custom_components.my_rail_commute.delay_repay.store.Store", FakeHAStore
    ):
        yield FakeHAStore


async def make_tracker(
    *,
    schemes: SchemeSet | None = None,
    claim_window_days: int = 28,
    entry_id: str = "entry1",
) -> tuple[DelayRepayTracker, DelayRepayStore]:
    """Build a loaded tracker over a store (call inside fake_storage())."""
    store = DelayRepayStore(None, entry_id)
    await store.async_load()
    tracker = DelayRepayTracker(
        store, schemes or build_scheme_set("15,30,60,120", ""), claim_window_days
    )
    return tracker, store


def make_service(**overrides: Any) -> dict[str, Any]:
    """Return a coordinator-style service dict: 22:40 departure, 22:53 arrival."""
    service: dict[str, Any] = {
        "scheduled_departure": "22:40",
        "expected_departure": "22:40",
        "platform": "1",
        "operator": "Southern",
        "service_id": "9494208WHYTELF",
        "delay_minutes": 0,
        "status": "on_time",
        "is_cancelled": False,
        "cancellation_reason": None,
        "delay_reason": None,
        "scheduled_arrival": "22:53",
        "estimated_arrival": "22:53",
        "destination": "London Bridge",
    }
    service.update(overrides)
    return service


def late_service(arrival: str = "23:10", **overrides: Any) -> dict[str, Any]:
    """A service forecast to arrive at ``arrival`` (22:53 scheduled)."""
    values: dict[str, Any] = {
        "status": "delayed",
        "estimated_arrival": arrival,
        "delay_reason": "a speed restriction",
    }
    values.update(overrides)
    return make_service(**values)


def single_leg(services: list[dict[str, Any]], origin="WYT", destination="LBG"):
    """Coordinator data for a single-leg commute."""
    return {"origin": origin, "destination": destination, "services": services}


def multi_leg(legs: list[tuple[str, str, list[dict[str, Any]]]]):
    """Coordinator data for a multi-leg commute: [(origin, dest, services), ...]."""
    return {
        "is_multi_leg": True,
        "origin": legs[0][0],
        "destination": legs[-1][1],
        "legs": [{"origin": o, "destination": d, "services": s} for o, d, s in legs],
    }
