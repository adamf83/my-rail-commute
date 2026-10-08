"""Tests for the Delay Repay claim store and ClaimRecord."""

from __future__ import annotations

import pytest

from custom_components.my_rail_commute.delay_repay.models import (
    ClaimRecord,
    ClaimStatus,
    Confirmation,
    arrival_datetime,
    claim_key,
    resolve_clock_datetime,
)
from custom_components.my_rail_commute.delay_repay.store import DelayRepayStore

from .delay_repay_helpers import NOW, fake_storage


def _record(**overrides) -> ClaimRecord:
    values = {
        "date": "2026-10-08",
        "leg": 1,
        "service_id": "9494208WHYTELF",
        "operator": "Southern",
        "origin": "WYT",
        "destination": "LBG",
        "scheduled_departure": "22:40",
        "scheduled_arrival": "22:53",
        "arrival": "23:10",
        "delay_minutes": 17,
        "tier": 15,
        "is_cancelled": False,
        "confirmation": Confirmation.ESTIMATED,
        "status": ClaimStatus.PENDING,
        "live_until": "2026-10-08T23:03:00+00:00",
        "first_seen": "2026-10-08T22:30:00+00:00",
        "last_updated": "2026-10-08T22:30:00+00:00",
        "delay_reason": "a speed restriction",
    }
    values.update(overrides)
    return ClaimRecord(**values)


def test_claim_record_key():
    assert _record().key == "2026-10-08|1|9494208WHYTELF"


def test_claim_key_falls_back_without_service_id():
    assert claim_key("2026-10-08", 2, "", "WYT", "22:40") == "2026-10-08|2|WYT-22:40"


def test_claim_record_round_trip():
    record = _record(status=ClaimStatus.ELIGIBLE)
    data = record.to_dict()
    assert data["status"] == "eligible"
    assert data["confirmation"] == "estimated"
    assert ClaimRecord.from_dict(data) == record


def test_claim_record_from_dict_ignores_unknown_keys():
    data = _record().to_dict() | {"future_field": 1}
    assert ClaimRecord.from_dict(data) == _record()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("date"),
        lambda d: d.update(status="bogus"),
        lambda d: d.update(confirmation="bogus"),
        lambda d: d.pop("status"),
    ],
)
def test_claim_record_from_dict_rejects_bad_data(mutate):
    data = _record().to_dict()
    mutate(data)
    with pytest.raises(ValueError):
        ClaimRecord.from_dict(data)


def test_resolve_clock_datetime_picks_nearest_day():
    # 23:55 now: a 00:10 train is tomorrow's
    late = NOW.replace(hour=23, minute=55)
    result = resolve_clock_datetime("00:10", late)
    assert result.date().isoformat() == "2026-10-09"
    # 00:05 now: a 23:50 train was yesterday's
    early = NOW.replace(hour=0, minute=5)
    assert resolve_clock_datetime("23:50", early).date().isoformat() == "2026-10-07"
    assert resolve_clock_datetime("22:40", NOW).date().isoformat() == "2026-10-08"
    assert resolve_clock_datetime("nope", NOW) is None


def test_arrival_datetime_rolls_past_midnight():
    dep = resolve_clock_datetime("23:50", NOW)
    arr = arrival_datetime(dep, "23:50", "00:20")
    assert arr.isoformat() == "2026-10-09T00:20:00+00:00"
    assert arrival_datetime(dep, "23:50", "bad") is None


async def test_store_round_trip_and_dirty_saving():
    with fake_storage() as backing:
        store = DelayRepayStore(None, "e1")
        await store.async_load()
        assert len(store) == 0
        assert await store.async_save_if_dirty() is False

        store.set(_record())
        assert await store.async_save_if_dirty() is True
        assert await store.async_save_if_dirty() is False  # clean again
        assert backing.save_count == 1

        # Setting an identical record does not dirty the store
        store.set(_record())
        assert await store.async_save_if_dirty() is False

        reloaded = DelayRepayStore(None, "e1")
        await reloaded.async_load()
        assert reloaded.get(_record().key) == _record()


async def test_store_remove():
    with fake_storage():
        store = DelayRepayStore(None, "e1")
        await store.async_load()
        store.set(_record())
        await store.async_save_if_dirty()
        store.remove(_record().key)
        store.remove("missing")
        assert await store.async_save_if_dirty() is True
        assert len(store) == 0


async def test_store_skips_unreadable_records():
    with fake_storage() as backing:
        good = _record()
        backing.saved["my_rail_commute_e1_delay_repay"] = {
            "version": 1,
            "records": {good.key: good.to_dict(), "bad": {"date": "x"}, "worse": 5},
        }
        store = DelayRepayStore(None, "e1")
        await store.async_load()
        assert len(store) == 1
        # Dropping the bad ones marks the store dirty so they get rewritten
        assert await store.async_save_if_dirty() is True
